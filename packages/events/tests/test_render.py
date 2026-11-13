"""The one-line rendering is fixed-width and carries the agent, kind and summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mothusi_contracts import AgentRole, Event, EventKind
from mothusi_events import render, summarise

SAST = timezone(timedelta(hours=2))


def event(seq: int, agent: AgentRole | None, kind: EventKind, summary: str) -> Event:
    return Event(
        seq=seq,
        at=datetime(2026, 11, 13, 11, 5, tzinfo=SAST),
        assessment_id="a",
        kind=kind,
        agent=agent,
        summary=summary,
    )


def test_summarise_fixed_widths() -> None:
    line = summarise(
        event(12, AgentRole.MARKER, EventKind.CHECK, "L-014 Q3: evidence check failed")
    )
    assert (
        line == "00012 2026-11-13T11:05:00+02:00 check    marker    L-014 Q3: evidence check failed"
    )


def test_summarise_without_an_agent_and_render() -> None:
    first = event(0, None, EventKind.STATE, "draft -> memo_check")
    second = event(1, AgentRole.PLANNER, EventKind.DECISION, "widen moderation: 2 of 10 disagree")
    text = render([first, second])
    assert text.splitlines()[0].startswith(
        "00000 2026-11-13T11:05:00+02:00 state    -         draft"
    )
    assert text.count("\n") == 2
    assert summarise(event(0, None, EventKind.TEACHER, "approved")).endswith("approved")
