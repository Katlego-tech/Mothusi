"""The live log: one Event per Brief, Result, check, decision, state change and teacher answer.

Design: docs/design/event-log.md §6 (the store) and console-ui.md §6 (the kinds and payloads). The
Planner emits through `EventSink`; the API's SSE endpoint follows the same JSONL file with
`follow`. No learner name is ever in a summary or payload: pseudonyms only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from mothusi_contracts import AgentRole, Brief, CheckResult, Event, EventKind, MarkedScript, Result
from mothusi_events import EventLog

type Clock = Callable[[], datetime]

# The watchdog (infra/mothusi_infra/watchdog.py) counts these markers in the core's log group and
# emails within a minute: a halted run, or a run waiting for the teacher, leaves the GPU idle.
RUN_HALTED = "WATCHDOG_RUN_HALTED"
NEEDS_TEACHER = "WATCHDOG_NEEDS_TEACHER"
watchdog_log = logging.getLogger("mothusi.watchdog")


def signal_watchdog(event: Event) -> None:
    """Log the events that leave the model host idle, each with its marker on one line."""
    if event.kind is EventKind.NEEDS_TEACHER:
        watchdog_log.warning("%s run %s: %s", NEEDS_TEACHER, event.assessment_id, event.summary)
    elif event.kind is EventKind.DECISION and event.payload.get("action") == "halt":
        watchdog_log.error("%s run %s: %s", RUN_HALTED, event.assessment_id, event.summary)


def _one_line(text: str, limit: int = 200) -> str:
    line = " ".join(text.split()) or "-"
    return line if len(line) <= limit else line[: limit - 1] + "…"


class EventSink:
    """Append events to one assessment's log, assigning seq from the log itself."""

    def __init__(self, path: Path, assessment_id: str, clock: Clock | None = None) -> None:
        self.path = path
        self.assessment_id = assessment_id
        self._log = EventLog(path)
        self._clock = clock or (lambda: datetime.now(UTC))

    def emit(
        self,
        kind: EventKind,
        summary: str,
        payload: Mapping[str, Any] | None = None,
        agent: AgentRole | None = None,
    ) -> Event:
        event = Event(
            seq=self._log.next_seq(),
            at=self._clock(),
            assessment_id=self.assessment_id,
            kind=kind,
            agent=agent,
            summary=_one_line(summary),
            payload=_json(payload or {}),
        )
        self._log.append(event)
        signal_watchdog(event)
        return event

    def read(self) -> tuple[Event, ...]:
        return self._log.read()

    # ------------------------------------------------------------------ the console's kinds

    def brief(self, brief: Brief, script_id: str | None = None) -> Event:
        return self.emit(
            EventKind.BRIEF,
            f"{brief.agent.value} briefed: {brief.task} (attempt {brief.attempt})",
            {
                "brief_id": brief.brief_id,
                "agent": brief.agent.value,
                "task": brief.task,
                "attempt": brief.attempt,
                "script_id": script_id,
            },
            brief.agent,
        )

    def result(
        self,
        result: Result,
        summary: str,
        script_id: str | None = None,
        marked: MarkedScript | None = None,
    ) -> Event:
        max_marks = sum(m.max_marks for m in marked.marks) if marked else None
        return self.emit(
            EventKind.RESULT,
            summary if result.status.value == "done" else f"{summary}: {result.reason}",
            {
                "brief_id": result.brief_id,
                "agent": result.agent.value,
                "status": result.status.value,
                "script_id": script_id,
                "summary": _one_line(summary),
                "awarded": marked.total if marked else None,
                "max_marks": max_marks,
                "reason": result.reason,
                "cost_zar": str(result.cost_zar),
                "marks": [m.model_dump(mode="json") for m in marked.marks] if marked else None,
            },
            result.agent,
        )

    def check(self, check: CheckResult, agent: AgentRole | None = None) -> Event:
        verdict = "passed" if check.passed else "FAILED"
        return self.emit(
            EventKind.CHECK,
            f"{check.about}: {check.check} {verdict}: {check.detail}",
            check.model_dump(mode="json"),
            agent,
        )

    def decision(self, action: str, reason: str, brief_id: str | None = None) -> Event:
        return self.emit(
            EventKind.DECISION,
            f"{action}: {reason}",
            {"action": action, "reason": reason, "brief_id": brief_id},
            AgentRole.PLANNER,
        )


def _json(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Payloads are plain JSON (they were built with model_dump(mode="json"))."""
    return json.loads(json.dumps(dict(value), default=str))


def frame(event: Event) -> str:
    """One SSE frame (console-ui.md §6): id, event kind, the whole Event as data."""
    return f"id: {event.seq}\nevent: {event.kind.value}\ndata: {event.model_dump_json()}\n\n"


async def follow(
    path: Path,
    after: int = -1,
    *,
    poll_s: float = 0.2,
    stop: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[Event]:
    """Every event with seq > after, then new ones as they are appended (complete lines only)."""
    offset = 0
    pending = b""
    while True:
        if path.exists():
            with path.open("rb") as source:
                source.seek(offset)
                chunk = source.read()
            offset += len(chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                if not line.strip():
                    continue
                event = Event.model_validate_json(line)
                if event.seq > after:
                    yield event
        if stop is not None and await stop():
            return
        await asyncio.sleep(poll_s)


PING = ": ping\n\n"
"""An SSE comment: CloudFront closes an idle origin after 30 s (deployment-aws.md §6.5)."""


async def with_pings(source: AsyncIterator[str], every_s: float = 15.0) -> AsyncIterator[str]:
    """Pass `source` through, and send PING whenever nothing came for `every_s` seconds.

    The source runs in its own task feeding a queue, so a ping never cancels a pending read of it.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put(item)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=every_s)
            except TimeoutError:
                yield PING
                continue
            if item is None:
                return
            yield item
    finally:
        task.cancel()
