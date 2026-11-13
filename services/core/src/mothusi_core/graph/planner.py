"""The Planner: one LangGraph StateGraph that runs an assessment through §5's states.

Design: docs/design/marking-pipeline.md §4, §5, §6.1, §6.2. One work node per state; every state
change goes through `router.check_transition` and is logged as a `state` event. Teacher questions
are `interrupt` calls in their own small `ask_*` nodes, so resuming never re-runs a model call. The
Executor is called from the `approved` node only, through `executor_brief`, which refuses any
other state.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

from mothusi_contracts import (
    AgentRole,
    Answer,
    AssessmentState,
    AtpWeek,
    Brief,
    CatchUpItem,
    CheckResult,
    ConvertedMark,
    Disagreement,
    EventKind,
    ItemStat,
    LearnerNote,
    Mark,
    MarkedScript,
    Memo,
    MemoItem,
    Passage,
    PassageKind,
    PoATask,
    ReadingAnswer,
    Result,
    ResultStatus,
    Script,
    SubjectPack,
)
from mothusi_core.agents.analyst import Misconception, analyse, analyst_brief
from mothusi_core.agents.executor import (
    Approval,
    Bundle,
    NamedNote,
    SheetRow,
    execute,
    executor_brief,
)
from mothusi_core.agents.loop import run_with_retries
from mothusi_core.agents.marker import Attempt, MarkingOutcome, mark_script, marker_brief
from mothusi_core.agents.moderator import (
    blind_check,
    compare,
    moderate_script,
    sample_for_moderation,
)
from mothusi_core.agents.reader import read_script
from mothusi_core.agents.reviewer import review, reviewer_brief
from mothusi_core.agents.setter import sit_test
from mothusi_core.agents.support import plan_catch_up, support_brief
from mothusi_core.agents.writer import ModerationReportData, write, writer_brief
from mothusi_core.delivery import delivery_from_environment
from mothusi_core.events import EventSink
from mothusi_core.graph import inbox
from mothusi_core.graph.router import check_transition
from mothusi_core.graph.state import Json, RunState
from mothusi_core.knowledge import Knowledge, KnowledgeError, NoKnowledge
from mothusi_core.pages import crop_link, crop_path, load_manifests, read_uploads
from mothusi_core.providers import Fallback, Limited, Metered, Role, Slots
from mothusi_delivery import MaildirMailer, Mailer, Message
from mothusi_intake.pages import sweep_names
from mothusi_llm import Provider
from mothusi_rules import (
    check_cognitive_grid,
    check_pack_weights,
    convert_mark,
    find_task,
    item_analysis,
    load_pack,
    mark_sheet_csv,
)

S = AssessmentState
BATCH = 4  # the model host's OLLAMA_NUM_PARALLEL (marking-pipeline.md, deployment-aws.md §6.2)
REVIEW_ROUNDS = 3
MODERATOR = "moderator"
type Update = dict[str, Any]


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class Planner:
    """Builds and runs the graph. Providers, mailer and data directory are injected."""

    primary: Provider
    fallback: Provider | None
    data_dir: Path
    checkpointer: BaseCheckpointSaver[Any]
    store: BaseStore = field(default_factory=InMemoryStore)
    teacher_email: str | None = None
    priced: bool = True
    knowledge: Knowledge = field(default_factory=NoKnowledge)
    """CAPS and ATP passages for Support's citations (knowledge.md §4.2); none when unset."""
    batch_size: int = BATCH
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    mailer_for: Callable[[str], Mailer] | None = None
    roles: Mapping[Role, Provider] = field(default_factory=dict[Role, Provider])
    """Providers per role (llm-seam.md §11.2); a role not given uses `primary`."""
    fallback_roles: Mapping[Role, Provider] = field(default_factory=dict[Role, Provider])
    """Fallbacks per role; a role not given uses `fallback` (a reader needs a vision model)."""
    _providers: dict[tuple[str, Role], Metered] = field(
        default_factory=dict[tuple[str, Role], Metered]
    )

    def __post_init__(self) -> None:
        # Every role and assessment shares the one model host, so they share its slots.
        self.slots = Slots(self.batch_size)
        self.graph = _build(self).compile(checkpointer=self.checkpointer, store=self.store)  # pyright: ignore[reportUnknownMemberType]

    # ------------------------------------------------------------------ per-assessment parts

    def sink(self, assessment_id: str) -> EventSink:
        return EventSink(self.data_dir / assessment_id / "events.jsonl", assessment_id, self.clock)

    def provider(self, assessment_id: str, role: Role = "text") -> Metered:
        key = (assessment_id, role)
        if key not in self._providers:
            sink = self.sink(assessment_id)

            def on_fallback(primary: str, fallback: str, error: str) -> None:
                sink.decision("fall back", f"{primary} failed ({error}); asked {fallback}")

            self._providers[key] = Metered(
                Limited(
                    Fallback(
                        self.roles.get(role, self.primary),
                        self.fallback_roles.get(role, self.fallback),
                        on_fallback,
                    ),
                    self.slots,
                )
            )
        return self._providers[key]

    def drain(self, assessment_id: str) -> tuple[int, int, Decimal]:
        """Tokens and rand since the last drain, across every role of this assessment."""
        i = o = 0
        cost = Decimal(0)
        for (aid, _role), metered in self._providers.items():
            if aid == assessment_id:
                di, do, dc = metered.drain()
                i, o, cost = i + di, o + do, cost + dc
        return i, o, cost

    def mailer(self, assessment_id: str) -> Mailer:
        if self.mailer_for is not None:
            return self.mailer_for(assessment_id)
        return MaildirMailer(self.data_dir / assessment_id / "outbox", "mothusi@mothusi.local")

    def config(self, assessment_id: str) -> Any:
        return {"configurable": {"thread_id": assessment_id}, "recursion_limit": 500}

    # ------------------------------------------------------------------ running

    async def start(self, initial: RunState) -> RunState:
        await self.graph.ainvoke(initial, self.config(initial["assessment_id"]))  # pyright: ignore[reportUnknownMemberType]
        return await self.snapshot(initial["assessment_id"])

    async def resume(self, assessment_id: str, answer: Json) -> RunState:
        await self.graph.ainvoke(Command(resume=answer), self.config(assessment_id))  # pyright: ignore[reportUnknownMemberType]
        return await self.snapshot(assessment_id)

    async def snapshot(self, assessment_id: str) -> RunState:
        state = await self.graph.aget_state(self.config(assessment_id))
        return cast(RunState, state.values)


# ---------------------------------------------------------------------------- state readers


def _memo(s: RunState) -> Memo:
    return Memo.model_validate(s["memo"])


def _scripts(s: RunState) -> tuple[Script, ...]:
    return tuple(Script.model_validate(x) for x in s["scripts"])


def _pack(s: RunState) -> SubjectPack:
    return load_pack(Path(s["pack_path"]))


def _marked(s: RunState) -> dict[str, MarkedScript]:
    return {k: MarkedScript.model_validate(v) for k, v in s.get("marked", {}).items()}


def _spent(s: RunState) -> Decimal:
    return Decimal(s.get("spent_zar", "0"))


# ---------------------------------------------------------------------------- shared helpers


def _move(sink: EventSink, s: RunState, to: AssessmentState, reason: str) -> Update:
    current = S(s["state"])
    check_transition(current, to)
    sink.emit(
        EventKind.STATE,
        f"{current.value} -> {to.value}: {reason}",
        {"from": current.value, "to": to.value, "reason": reason},
    )
    return {"state": to.value}


def _usage(p: Planner, s: RunState, update: Update) -> Update:
    """Fold the meter into the state and emit the usage event (console Meters)."""
    i, o, cost = p.drain(s["assessment_id"])
    spent = _spent(s) + cost
    tokens_in = s.get("input_tokens", 0) + i
    tokens_out = s.get("output_tokens", 0) + o
    marked = update.get("marked", s.get("marked", {}))
    p.sink(s["assessment_id"]).emit(
        EventKind.USAGE,
        f"R{_money(spent)} spent, {tokens_in + tokens_out} tokens",
        {
            "cost_zar_total": _money(spent),
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "scripts_settled": len(marked),
            "teacher_steps": update.get("teacher_steps", s.get("teacher_steps", 0)),
            "priced": p.priced,
        },
    )
    return {
        **update,
        "spent_zar": str(spent),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
    }


def _live_marking(sink: EventSink, sid: str, max_attempts: int = 3) -> dict[str, Any]:
    """Hooks for mark_script: brief at dispatch; result, checks and any re-brief as they land."""

    def on_brief(brief: Brief) -> None:
        sink.brief(brief, sid)

    def on_attempt(attempt: Attempt) -> None:
        marked = None
        if attempt.result.status is ResultStatus.DONE:
            marked = MarkedScript.model_validate(attempt.result.outputs["marked"])
        summary = (
            f"{sid}: {marked.total}/{sum(m.max_marks for m in marked.marks)}" if marked else sid
        )
        sink.result(attempt.result, summary, sid, marked)
        for check in attempt.checks:
            sink.check(check, attempt.brief.agent)
        failed = [c for c in attempt.checks if not c.passed]
        retrying = failed or attempt.result.status is ResultStatus.RETRY
        if retrying and attempt.brief.attempt < max_attempts:
            why = "; ".join(f"{c.check}: {c.detail}" for c in failed) or attempt.result.reason
            sink.decision(
                f"re-brief {attempt.brief.agent.value} attempt {attempt.brief.attempt + 1}",
                f"{sid}: {why}",
                attempt.brief.brief_id,
            )

    return {"on_brief": on_brief, "on_attempt": on_attempt}


def _live_agent(
    sink: EventSink, label: Callable[[Result], str], max_attempts: int = 3
) -> dict[str, Any]:
    """Hooks for run_with_retries: the same live brief / result / re-brief rows."""

    def on_brief(brief: Brief) -> None:
        sink.brief(brief)

    def on_attempt(attempt: Attempt) -> None:
        sink.result(attempt.result, label(attempt.result))
        if attempt.result.status is ResultStatus.RETRY and attempt.brief.attempt < max_attempts:
            sink.decision(
                f"re-brief {attempt.brief.agent.value} attempt {attempt.brief.attempt + 1}",
                attempt.result.reason,
                attempt.brief.brief_id,
            )

    return {"on_brief": on_brief, "on_attempt": on_attempt}


def _rejected(r: Result) -> str:
    return f"Reviewer: {sum(1 for v in r.outputs.get('verdicts', []) if not v['passed'])} rejected"


def _count(key: str, label: str) -> Callable[[Result], str]:
    return lambda r: f"{label.format(len(r.outputs.get(key, [])))}"


def _script_question(s: RunState, sid: str, attempts: list[Json], stage: str | None = None) -> Json:
    memo = _memo(s)
    script = next(x for x in _scripts(s) if x.script_id == sid)
    answers = {a.question_id: a.text for a in script.answers}
    named = next(
        (i for i in memo.items for a in attempts if i.question_id in str(a.get("detail", ""))),
        memo.items[0],
    )
    title = f"{sid} did not pass the checks in {len(attempts)} attempts"
    if stage == "moderation":
        title = f"{sid}: the blind moderation did not pass; your marks are the second marking"
    return {
        "topic": "script",
        "stage": stage or "marking",
        "title": title,
        "script_id": sid,
        "question_id": named.question_id,
        "question": named.question,
        "max_marks": named.max_marks,
        "memo_answer": named.answer,
        "marking_notes": named.marking_notes,
        "answer": answers.get(named.question_id, ""),
        "attempts": attempts,
    }


def _attempt_rows(outcome: MarkingOutcome) -> list[Json]:
    rows: list[Json] = []
    for n, attempt in enumerate(outcome.attempts, start=1):
        marked = attempt.result.outputs.get("marked")
        failed = next((c for c in attempt.checks if not c.passed), None)
        ms = MarkedScript.model_validate(marked) if marked else None
        rows.append(
            {
                "attempt": n,
                "awarded": ms.total if ms else None,
                "max_marks": sum(m.max_marks for m in ms.marks) if ms else None,
                "failed_check": failed.check if failed else attempt.result.status.value,
                "detail": failed.detail if failed else attempt.result.reason,
                "marks": [m.model_dump(mode="json") for m in ms.marks] if ms else [],
            }
        )
    return rows


def _withheld(detail: str) -> Json:
    return {
        "attempt": 1,
        "awarded": None,
        "max_marks": None,
        "failed_check": "blind_brief",
        "detail": detail,
        "marks": [],
    }


def _budget_guard(p: Planner, s: RunState) -> Update | None:
    """Before a paid stage: past the budget, the teacher decides whether to go on.

    An unpriced run (no sourced prices) cannot be checked; new_run logged that once."""
    spent, limit = _spent(s), Decimal(s["budget_zar"])
    if not p.priced or spent <= limit:
        return None
    question = _budget_question(s, spent, limit, len(s.get("marked", {})))
    return _ask(p.sink(s["assessment_id"]), question)


def _ask(sink: EventSink, question: Json) -> Update:
    sink.emit(EventKind.NEEDS_TEACHER, question["title"], question)
    return {"question": question}


# ---------------------------------------------------------------------------- the nodes


def _build(p: Planner) -> StateGraph[RunState]:
    g = StateGraph(RunState)

    async def draft(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if s.get("mode") == "pages" and not s["scripts"]:
            return await _read_pages(p, s)
        scripts = _scripts(s)
        if s.get("memo_amended"):
            sink.decision(
                "re-read the memo", "the teacher amended the memo; the Setter sits it again"
            )
        else:
            ids = [x.script_id for x in scripts]
            sink.decision(
                "intake", f"{len(scripts)} scripts, learners pseudonymised {ids[0]}…{ids[-1]}"
            )
        return _move(sink, s, S.MEMO_CHECK, "intake done")

    async def memo_check(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        memo, pack = _memo(s), _pack(s)
        provider = p.provider(s["assessment_id"])

        def brief_for(attempt: int, previous: str | None) -> Brief:
            return Brief(
                brief_id=f"{s['assessment_id']}/memo/setter/{attempt}",
                assessment_id=s["assessment_id"],
                agent=AgentRole.SETTER,
                task="Sit the test blind to the memo; code compares your answers with it",
                inputs={
                    "memo": memo.model_dump(mode="json"),
                    "pack_conventions": list(pack.conventions),
                },
                attempt=attempt,
            )

        outcome = await run_with_retries(
            brief_for,
            lambda b: sit_test(b, provider),
            **_live_agent(sink, _count("issues", "Setter: {} memo issue(s)")),
        )
        issues: list[Json] = outcome.outputs.get("issues", [])
        sink.check(check_pack_weights(pack))
        if pack.cognitive is None:
            sink.decision("skip check", "no cognitive-level grid in this pack; check skipped")
        else:
            sink.check(check_cognitive_grid(memo, pack))
        unverified: list[Json] = outcome.outputs.get("unverified", [])
        for item in unverified:
            sink.decision(
                "memo item unverified",
                f"{item['question_id']}: code could not compare '{item['setter_answer']}' with "
                f"the memo's '{item['memo_answer']}'; the teacher sees it in the bundle",
            )
        update = _usage(p, s, {"memo_amended": False, "memo_unverified": unverified})
        if outcome.status != "done":
            sink.decision(
                "memo not verified", f"the Setter could not sit the test: {outcome.reason}"
            )
            return {**update, **_move(sink, s, S.MARKING, "memo not verified; marking goes ahead")}
        if not issues:
            return {**update, **_move(sink, s, S.MARKING, "memo confirmed: the Setter agrees")}
        items = {i.question_id: i for i in memo.items}
        queue = [_memo_question(items[i["question_id"]], i) for i in issues]
        return {**update, "memo_queue": queue[1:], **_ask(sink, queue[0])}

    async def ask_memo(s: RunState) -> Update:
        q = cast(Json, s.get("question"))
        answer = cast(Json, interrupt(q))
        sink = p.sink(s["assessment_id"])
        memo = _memo(s)
        decision = answer["decision"]
        if decision == "use_proposed" and not q["proposed_answer"]:
            raise ValueError(f"the Setter proposed no answer for {q['question_id']}")
        new = {"use_proposed": q["proposed_answer"], "amend": answer.get("answer")}.get(decision)
        steps = s.get("teacher_steps", 0) + 1
        amended = s.get("memo_amended", False)
        if new:
            items = tuple(
                i.model_copy(update={"answer": new}) if i.question_id == q["question_id"] else i
                for i in memo.items
            )
            memo = memo.model_copy(update={"items": items})
            amended = True
        sink.emit(
            EventKind.TEACHER,
            f"memo {q['question_id']}: {decision}" + (f" -> {new}" if new else ""),
            {"topic": "memo", **answer},
        )
        update: Update = {
            "memo": memo.model_dump(mode="json"),
            "teacher_steps": steps,
            "memo_amended": amended,
            "question": None,
        }
        queue = s.get("memo_queue", [])
        if queue:
            return {**update, "memo_queue": queue[1:], **_ask(sink, queue[0])}
        if amended:
            return {**update, **_move(sink, s, S.DRAFT, "teacher amended the memo")}
        return {**update, **_move(sink, s, S.MARKING, "teacher kept the memo")}

    def _apply_script_answer(s: RunState, sid: str, answer: Json) -> Update:
        sink = p.sink(s["assessment_id"])
        memo, script = _memo(s), next(x for x in _scripts(s) if x.script_id == sid)
        pending = dict(s.get("pending", {}))
        attempts = pending.pop(sid)
        if answer["decision"] == "use_attempt":
            row = attempts[int(answer["attempt"]) - 1]
            if not attempt_is_complete(memo, row["marks"]):
                raise ValueError(f"attempt {answer['attempt']} does not mark every memo question")
            ms = MarkedScript(
                script_id=sid,
                marker="teacher",
                marks=tuple(Mark.model_validate(m) for m in row["marks"]),
                total=sum(int(m["awarded"]) for m in row["marks"]),
            )
            summary = f"{sid}: teacher used attempt {answer['attempt']}"
        else:
            ms = teacher_marks(memo, script, answer["marks"])
            summary = f"{sid}: teacher set the marks ({ms.total}/{memo.total_marks})"
            sink.decision("exempt from check_evidence", f"{sid}: marks are the teacher's decision")
        sink.emit(EventKind.TEACHER, summary, {"topic": "script", **answer, "script_id": sid})
        update: Update = {
            "pending": pending,
            "teacher_steps": s.get("teacher_steps", 0) + 1,
            "question": None,
        }
        if (s.get("question") or {}).get("stage") == "moderation":
            # The teacher's marks stand in for the blind second marking of this sampled script.
            update["moderated"] = {**s.get("moderated", {}), sid: ms.model_dump(mode="json")}
        else:
            update["marked"] = {**s.get("marked", {}), sid: ms.model_dump(mode="json")}
        if S(s["state"]) is S.RE_MARKING:
            # A dispute the teacher settled is the teacher's, in the moderation report too.
            awarded = {m.question_id: m.awarded for m in ms.marks}
            resolutions = dict(s.get("resolutions", {}))
            for key in [k for k in resolutions if k.startswith(f"{sid}/")]:
                question = key.split("/", 1)[1]
                resolutions[key] = f"teacher's decision: {awarded[question]}"
            update["resolutions"] = resolutions
        return update

    async def apply_inbox(s: RunState) -> Update:
        update: Update = {}
        if S(s["state"]) is not S.MARKING:
            return update
        for sid, answer in (await inbox.take_all(p.store, s["assessment_id"])).items():
            current = cast(RunState, {**s, **update})
            if sid in current.get("pending", {}):
                update.update(_apply_script_answer(current, sid, answer))
                await p.store.adelete(("inbox", s["assessment_id"]), sid)
        return update

    async def marking(s: RunState) -> Update:
        if applied := await apply_inbox(s):
            return applied
        sink = p.sink(s["assessment_id"])
        memo, pack, scripts = _memo(s), _pack(s), _scripts(s)
        provider = p.provider(s["assessment_id"])
        marked = dict(s.get("marked", {}))
        pending = dict(s.get("pending", {}))
        todo = [x for x in scripts if x.script_id not in marked and x.script_id not in pending]
        batch, rest = todo[: p.batch_size], todo[p.batch_size :]
        outcomes = await asyncio.gather(
            *(
                mark_script(
                    memo,
                    x,
                    pack,
                    provider,
                    assessment_id=s["assessment_id"],
                    **_live_marking(sink, x.script_id),
                )
                for x in batch
            )
        )
        for outcome in outcomes:
            if outcome.status == "done" and outcome.marked is not None:
                marked[outcome.script_id] = outcome.marked.model_dump(mode="json")
            else:
                sink.decision("to the teacher", outcome.reason)
                pending[outcome.script_id] = _attempt_rows(outcome)
                question = _script_question(s, outcome.script_id, pending[outcome.script_id])
                sink.emit(EventKind.NEEDS_TEACHER, question["title"], question)
        s = cast(RunState, {**s, "marked": marked, "pending": pending})
        applied = await apply_inbox(s)
        s = cast(RunState, {**s, **applied})
        marked, pending = s.get("marked", {}), s.get("pending", {})
        update = _usage(
            p,
            s,
            {
                "marked": marked,
                "pending": pending,
                "teacher_steps": s.get("teacher_steps", 0),
                "question": None,
            },
        )
        if rest:
            spent, limit = Decimal(update["spent_zar"]), Decimal(s["budget_zar"])
            if p.priced and spent > limit:
                return {**update, **_ask(sink, _budget_question(s, spent, limit, len(marked)))}
            return update
        if pending:
            sid = next(iter(pending))
            return {**update, **_ask(sink, _script_question(s, sid, pending[sid]))}
        return {
            **update,
            **_move(sink, s, S.MODERATING, "every script settled"),
        }

    async def ask_budget(s: RunState) -> Update:
        answer = cast(Json, interrupt(s.get("question")))
        p.sink(s["assessment_id"]).emit(
            EventKind.TEACHER,
            f"budget raised to R{answer['raise_to_zar']}",
            {"topic": "budget", **answer},
        )
        return {
            "budget_zar": str(answer["raise_to_zar"]),
            "teacher_steps": s.get("teacher_steps", 0) + 1,
            "question": None,
        }

    async def ask_script(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        q = cast(Json, s.get("question"))
        update = await apply_inbox(s)
        if update:
            # Checkpoint applied answers before a later interrupt can suspend the node.
            current = cast(RunState, {**s, **update})
            pending = current.get("pending", {})
            if pending:
                sid = next(iter(pending))
                return {**update, **_ask(sink, _script_question(current, sid, pending[sid]))}
            return {**update, **_move(sink, s, S.MODERATING, "every script settled")}
        answer = cast(Json, interrupt(q))
        sid = q["script_id"]
        update = _apply_script_answer(s, sid, answer)
        await p.store.adelete(("inbox", s["assessment_id"]), sid)
        pending = update["pending"]
        if pending:
            nxt = next(iter(pending))
            return {**update, **_ask(sink, _script_question(s, nxt, pending[nxt], q.get("stage")))}
        if q.get("stage") == "moderation":
            return update  # back to moderating, which compares now that the sample is complete
        if S(s["state"]) is S.RE_MARKING:
            return {
                **update,
                "moderation_closed": True,
                **_move(sink, s, S.MODERATING, "re-marks settled"),
            }
        return {**update, **_move(sink, s, S.MODERATING, "every script settled")}

    async def moderating(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if s.get("moderation_closed"):
            sink.decision("moderation closed", "the re-marked scripts pass the rules")
            return _move(sink, s, S.ANALYSING, "moderation complete")
        if (asked := _budget_guard(p, s)) is not None:
            return asked
        memo, pack, scripts = _memo(s), _pack(s), {x.script_id: x for x in _scripts(s)}
        provider = p.provider(s["assessment_id"], "moderator")
        first = _marked(s)
        names = list(s["names"].values())
        second = {k: MarkedScript.model_validate(v) for k, v in s.get("moderated", {}).items()}
        pending = dict(s.get("pending", {}))

        async def moderate(ids: Sequence[str]) -> None:
            """Blind re-marks into `second`; a failure goes to `pending`, never dropped."""
            ready: list[str] = []
            for sid in ids:
                brief = marker_brief(
                    s["assessment_id"], memo, scripts[sid], pack, 1, None, None, AgentRole.MODERATOR
                )
                check = blind_check(brief, names)
                sink.check(check, AgentRole.MODERATOR)
                if check.passed:
                    ready.append(sid)
                else:
                    sink.decision("withheld", f"{sid}: the moderator brief was not blind")
                    pending[sid] = [_withheld(check.detail)]
            outcomes = await asyncio.gather(
                *(
                    moderate_script(
                        memo,
                        scripts[sid],
                        pack,
                        provider,
                        assessment_id=s["assessment_id"],
                        **_live_marking(sink, sid),
                    )
                    for sid in ready
                )
            )
            for outcome in outcomes:
                if outcome.marked is not None:
                    second[outcome.script_id] = outcome.marked
                else:
                    sink.decision("to the teacher", f"moderation of {outcome.reason}")
                    pending[outcome.script_id] = _attempt_rows(outcome)

        def stage_update(**extra: Any) -> Update:
            return {
                "moderated": {k: v.model_dump(mode="json") for k, v in second.items()},
                "pending": pending,
                **extra,
            }

        def ask_teacher(update: Update) -> Update:
            sid = next(iter(pending))
            question = _script_question(s, sid, pending[sid], "moderation")
            return {**_usage(p, s, update), **_ask(sink, question)}

        sample = s.get("sample")
        extra: Update = {}
        if sample is None:
            sample = list(sample_for_moderation(list(first.values()), pack))
            sink.decision(
                "moderation sample", f"{len(sample)} scripts re-marked blind: {', '.join(sample)}"
            )
            await moderate(sample)
            extra["sample"] = sample
            if pending:
                return ask_teacher(stage_update(**extra))
        found, compared, widen = compare([first[k] for k in second], list(second.values()), pack)
        if widen and not s.get("widened"):
            rest = [k for k in first if k not in second]
            sink.decision(
                "widen",
                f"disagreement rate above the pack's rule: re-marking all {len(rest)} others",
            )
            await moderate(rest)
            extra["widened"] = True
            if pending:
                return ask_teacher(stage_update(**extra))
            found, compared, _ = compare([first[k] for k in second], list(second.values()), pack)
        items = {i.question_id: i for i in memo.items}
        for d in found:
            sink.emit(
                EventKind.DISAGREEMENT,
                f"{d.script_id} Q{d.question_id}: first {d.first}, second {d.second}",
                _disagreement(
                    d,
                    items[d.question_id],
                    scripts[d.script_id],
                    first[d.script_id],
                    second[d.script_id],
                ),
                AgentRole.MODERATOR,
            )
        update = _usage(
            p,
            s,
            stage_update(
                **extra,
                disagreements=[d.model_dump(mode="json") for d in found],
                questions_compared=compared,
            ),
        )
        if found:
            return {**update, **_move(sink, s, S.RE_MARKING, f"{len(found)} disagreement(s)")}
        return {**update, **_move(sink, s, S.ANALYSING, "the sample agrees")}

    async def re_marking(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if (asked := _budget_guard(p, s)) is not None:
            return asked
        memo, pack, scripts = _memo(s), _pack(s), {x.script_id: x for x in _scripts(s)}
        provider = p.provider(s["assessment_id"])
        first, second = (
            _marked(s),
            {k: MarkedScript.model_validate(v) for k, v in s.get("moderated", {}).items()},
        )
        disputes = [Disagreement.model_validate(d) for d in s.get("disagreements", [])]
        ids = sorted({d.script_id for d in disputes})
        outcomes = await asyncio.gather(
            *(
                mark_script(
                    memo,
                    scripts[sid],
                    pack,
                    provider,
                    assessment_id=s["assessment_id"],
                    moderation=(first[sid], second[sid]),
                    **_live_marking(sink, sid),
                )
                for sid in ids
            )
        )
        marked = dict(s.get("marked", {}))
        pending = dict(s.get("pending", {}))
        resolutions = dict(s.get("resolutions", {}))
        for outcome in outcomes:
            sid = outcome.script_id
            if outcome.marked is None:
                pending[sid] = _attempt_rows(outcome)
                for d in disputes:
                    if d.script_id == sid:
                        resolutions[f"{sid}/{d.question_id}"] = "with the teacher"
                continue
            marked[sid] = outcome.marked.model_dump(mode="json")
            awarded = {m.question_id: m for m in outcome.marked.marks}
            for d in disputes:
                if d.script_id == sid:
                    m = awarded[d.question_id]
                    resolutions[f"{sid}/{d.question_id}"] = (
                        f"re-marked to {m.awarded}: {m.principle}"
                    )
        update = _usage(
            p,
            s,
            {"marked": marked, "pending": pending, "resolutions": resolutions, "re_marked": ids},
        )
        if pending:
            nxt = next(iter(pending))
            return {**update, **_ask(sink, _script_question(s, nxt, pending[nxt]))}
        return {
            **update,
            "moderation_closed": True,
            **_move(sink, s, S.MODERATING, "re-marked scripts pass the rules"),
        }

    async def analysing(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if (asked := _budget_guard(p, s)) is not None:
            return asked
        memo, pack, scripts = _memo(s), _pack(s), _scripts(s)
        provider = p.provider(s["assessment_id"])
        marked = _marked(s)
        task = find_task(pack, s["poa_task"])
        converted = {k: convert_mark(k, m.total, task, pack) for k, m in marked.items()}
        ordered = [marked[x.script_id] for x in scripts if x.script_id in marked]
        stats = item_analysis(ordered, memo)
        flagged = [x.question_id for x in stats if x.flag == "re-teach"]
        sink.decision(
            "item analysis", f"{len(stats)} questions; re-teach: {', '.join(flagged) or 'none'}"
        )
        analysed = await run_with_retries(
            lambda n, prev: analyst_brief(
                s["assessment_id"], memo, stats, ordered, scripts, n, prev
            ),
            lambda b: analyse(b, provider),
            **_live_agent(sink, _count("misconceptions", "Analyst: {} misconception(s)")),
        )
        found = [
            Misconception.model_validate(m) for m in analysed.outputs.get("misconceptions", [])
        ]
        if analysed.status != "done":
            sink.decision(
                "no misconceptions", f"the Analyst's quotes did not pass: {analysed.reason}"
            )
        weeks = [AtpWeek.model_validate(w) for w in s.get("atp_weeks", [])]
        plan: list[Json] = []
        if not weeks:
            sink.decision("skip support", "no ATP weeks given: catch-up plan not placed")
        else:
            passages = await _passages_for(p, sink, memo, pack, found)
            planned = await run_with_retries(
                lambda n, prev: support_brief(
                    s["assessment_id"], found, weeks, n, prev, passages_by_question=passages
                ),
                lambda b: plan_catch_up(b, provider),
                **_live_agent(sink, _count("catch_up_plan", "Support: {} item(s)")),
            )
            if planned.status == "done":
                plan = planned.outputs["catch_up_plan"]
            else:
                sink.decision("catch-up plan not placed", planned.reason)
        update = _usage(
            p,
            s,
            {
                "converted": {k: v.model_dump(mode="json") for k, v in converted.items()},
                "item_stats": [x.model_dump(mode="json") for x in stats],
                "misconceptions": [m.model_dump(mode="json") for m in found],
                "catch_up_plan": plan,
            },
        )
        return {**update, **_move(sink, s, S.WRITING, "item stats and converted marks ready")}

    async def writing(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if (asked := _budget_guard(p, s)) is not None:
            return asked
        provider = p.provider(s["assessment_id"])
        drafts = s.get("drafts") or {}
        notes = [LearnerNote.model_validate(n) for n in drafts.get("learner_notes", [])]
        rewrite = s.get("rewrite") or {}
        teacher_note = s.get("teacher_note")
        sheet_reason = s.get("class_sheet_reason")
        rounds = s.get("review_rounds", 0)

        def brief_for(n: int, prev: str | None) -> Brief:
            return writer_brief(
                s["assessment_id"],
                memo=_memo(s),
                marked=[MarkedScript.model_validate(v) for v in s.get("marked", {}).values()],
                scripts=_scripts(s),
                item_stats=[ItemStat.model_validate(x) for x in s.get("item_stats", [])],
                misconceptions=[
                    Misconception.model_validate(m) for m in s.get("misconceptions", [])
                ],
                catch_up_plan=[CatchUpItem.model_validate(i) for i in s.get("catch_up_plan", [])],
                moderation=_moderation_data(s),
                attempt=n,
                notes=notes if (rewrite or teacher_note or sheet_reason) else (),
                rewrite=rewrite,
                teacher_note=teacher_note,
                class_sheet_reason=sheet_reason,
                previous_failure=prev,
                review_round=rounds + 1,
            )

        # The teacher's note is the one free text in a Writer brief; it was pseudonymised when
        # it arrived, and is checked again here because check_brief_blind exempts the Writer.
        if teacher_note and names_left(teacher_note, s["names"]):
            sink.decision("halt", "the teacher's note still names a learner; nothing sent")
            return {"halted": "a learner name in the teacher's note"}
        outcome = await run_with_retries(
            brief_for,
            lambda b: write(b, provider),
            **_live_agent(sink, _count("learner_notes", "Writer: {} notes drafted")),
        )
        update = _usage(p, s, {})
        if outcome.status != "done":
            sink.decision("halt", f"the Writer could not draft the bundle: {outcome.reason}")
            return {**update, "halted": outcome.reason}
        return {
            **update,
            "drafts": outcome.outputs,
            "rewrite": {},
            "teacher_note": None,
            "class_sheet_reason": None,
            **_move(sink, s, S.REVIEWING, "drafts ready"),
        }

    async def reviewing(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        if (asked := _budget_guard(p, s)) is not None:
            return asked
        provider = p.provider(s["assessment_id"])
        drafts = cast(Json, s.get("drafts"))
        rounds = s.get("review_rounds", 0) + 1

        def brief_for(n: int, prev: str | None) -> Brief:
            return reviewer_brief(
                s["assessment_id"],
                [LearnerNote.model_validate(x) for x in drafts["learner_notes"]],
                [MarkedScript.model_validate(v) for v in s.get("marked", {}).values()],
                _scripts(s),
                drafts["class_sheet_md"],
                [CatchUpItem.model_validate(i) for i in s.get("catch_up_plan", [])],
                attempt=n,
                review_round=rounds,
            )

        outcome = await run_with_retries(
            brief_for,
            lambda b: review(b, provider),
            **_live_agent(sink, _rejected),
        )
        verdicts: list[Json] = outcome.outputs.get("verdicts", [])
        rejected = {v["pseudonym"]: v["reason"] for v in verdicts if not v["passed"]}
        for pseudonym, reason in rejected.items():
            sink.decision("note rejected", f"{pseudonym}: {reason}")
        sheet: Json = outcome.outputs.get("class_sheet") or {}
        sheet_rejected = bool(sheet) and not sheet.get("passed")
        if sheet_rejected:
            sink.decision("class sheet rejected", str(sheet.get("reason")))
        update = _usage(
            p,
            s,
            {
                "verdicts": verdicts,
                "review_rounds": rounds,
                "class_sheet_review": sheet,
                "review_incomplete": None if outcome.status == "done" else outcome.reason,
            },
        )
        if outcome.status != "done":
            sink.decision(
                "review incomplete",
                f"the Reviewer did not finish ({outcome.reason}): every note goes to the "
                "teacher marked not reviewed",
            )
        elif (rejected or sheet_rejected) and rounds < REVIEW_ROUNDS:
            sent_back = f"{len(rejected)} note(s)" + (
                " and the class sheet" if sheet_rejected else ""
            )
            return {
                **update,
                "rewrite": rejected,
                "class_sheet_reason": str(sheet.get("reason")) if sheet_rejected else None,
                **_move(sink, s, S.WRITING, f"{sent_back} sent back"),
            }
        elif rejected or sheet_rejected:
            sink.decision(
                "to the teacher",
                f"{len(rejected)} note(s)"
                + (" and the class sheet" if sheet_rejected else "")
                + f" still rejected after {rounds} rounds stay in the bundle, marked",
            )
        full = {**s, **update}
        bundle = build_bundle(p, cast(RunState, full))
        return {
            **update,
            "bundle": bundle,
            **_move(sink, s, S.AWAITING_APPROVAL, "every draft reviewed"),
        }

    async def ask_approval(s: RunState) -> Update:
        answer = cast(Json, interrupt({"topic": "approval"}))
        sink = p.sink(s["assessment_id"])
        steps = s.get("teacher_steps", 0) + 1
        if answer["decision"] == "approve":
            approval = Approval(by=str(answer.get("by") or "the teacher"), at=p.clock())
            sink.emit(
                EventKind.TEACHER,
                f"approved by {approval.by}",
                {"topic": "approval", "decision": "approve"},
            )
            return {
                "teacher_steps": steps,
                "approval": approval.model_dump(mode="json"),
                **_move(sink, s, S.APPROVED, "the teacher approved the bundle"),
            }
        # The note is read by the Writer's model and shown in the log: no learner name in it.
        note = pseudonymise_text(str(answer["note"]), s["names"])
        sink.emit(
            EventKind.TEACHER,
            f"rejected: {note}",
            {"topic": "approval", "decision": "reject", "note": note},
        )
        moved = _move(sink, s, S.REJECTED, "the teacher rejected the bundle with a note")
        after = cast(RunState, {**s, **moved})
        return {
            "teacher_steps": steps,
            "teacher_note": note,
            "review_rounds": 0,
            "rewrite": {},
            **_move(sink, after, S.WRITING, "note absorbed: the Writer redrafts; marks unchanged"),
        }

    async def approved(s: RunState) -> Update:
        sink = p.sink(s["assessment_id"])
        approval = Approval.model_validate(s.get("approval"))
        bundle = _executor_bundle(p, s)
        messages = _messages(p, s, bundle)
        brief = executor_brief(s["assessment_id"], S(s["state"]), approval, bundle, messages)
        sink.brief(brief)
        result = await asyncio.to_thread(
            execute, brief, p.mailer(s["assessment_id"]), delivery_from_environment(p.data_dir)
        )
        sink.result(
            result,
            f"{len(result.outputs.get('written', []))} files written, "
            f"{len(result.outputs.get('sent', []))} sent",
        )
        if result.status is not ResultStatus.DONE:
            sink.decision("halt", f"delivery stopped: {result.reason}")
            return {"halted": result.reason}
        delivered = {"written": result.outputs["written"], "sent": result.outputs["sent"]}
        if result.outputs.get("links_issued"):
            sink.decision(
                "note links",
                f"{result.outputs['links_issued']} note links issued, "
                f"expire {result.outputs['links_expire']}",
            )
        moved = _move(
            sink,
            s,
            S.DELIVERED,
            f"{len(delivered['written'])} files written, {len(delivered['sent'])} messages sent",
        )
        sink.emit(
            EventKind.DELIVERED,
            f"{len(delivered['written'])} files, {len(delivered['sent'])} messages",
            delivered,
            AgentRole.EXECUTOR,
        )  # the last event of a run: the SSE stream ends after it
        return {"delivered": delivered, **moved}

    async def ask_reading(s: RunState) -> Update:
        q = cast(Json, s.get("question"))
        answer = cast(Json, interrupt(q))
        sink = p.sink(s["assessment_id"])
        by_name = {name: pseudonym for pseudonym, name in s["names"].items()}
        scripts = apply_readings(_scripts(s), q["items"], answer["answers"], by_name)
        corrected = sum(1 for a in answer["answers"] if a["action"] == "correct")
        sink.emit(
            EventKind.TEACHER,
            f"reading check: {len(answer['answers'])} line(s) answered, {corrected} corrected",
            {"topic": "reading", "corrected": corrected},
        )
        update: Update = {
            "scripts": [x.model_dump(mode="json") for x in scripts],
            "question": None,
            "teacher_steps": s.get("teacher_steps", 0) + 1,
        }
        return {**update, **_move(sink, s, S.MEMO_CHECK, "the teacher checked the readings")}

    nodes: dict[str, Callable[[RunState], Awaitable[Update]]] = {
        "draft": draft,
        "ask_reading": ask_reading,
        "memo_check": memo_check,
        "ask_memo": ask_memo,
        "marking": marking,
        "ask_budget": ask_budget,
        "ask_script": ask_script,
        "moderating": moderating,
        "re_marking": re_marking,
        "analysing": analysing,
        "writing": writing,
        "reviewing": reviewing,
        "ask_approval": ask_approval,
        "approved": approved,
    }
    for name, node in nodes.items():
        g.add_node(name, node)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    g.add_edge(START, "draft")
    for name in nodes:
        g.add_conditional_edges(name, route)  # pyright: ignore[reportUnknownMemberType]
    return g


ROUTES: dict[str, str] = {
    S.DRAFT.value: "draft",
    S.READING_CHECK.value: "ask_reading",
    S.MEMO_CHECK.value: "memo_check",
    S.MARKING.value: "marking",
    S.MODERATING.value: "moderating",
    S.RE_MARKING.value: "re_marking",
    S.ANALYSING.value: "analysing",
    S.WRITING.value: "writing",
    S.REVIEWING.value: "reviewing",
    S.AWAITING_APPROVAL.value: "ask_approval",
    S.APPROVED.value: "approved",
}
ASKS = {
    "memo": "ask_memo",
    "budget": "ask_budget",
    "script": "ask_script",
    "reading": "ask_reading",
}


def route(s: RunState) -> str:
    """The next node from the state alone: a pending question, a halt, or the state's node."""
    question = s.get("question")
    if question:
        return ASKS[question["topic"]]
    if s.get("halted"):
        return END
    return ROUTES.get(s["state"], END)


# ---------------------------------------------------------------------------- payload builders


def _memo_question(item: MemoItem, issue: Json) -> Json:
    return {
        "topic": "memo",
        "title": f"Memo {item.question_id}: the Setter's answer differs from the memo",
        "question_id": item.question_id,
        "question": item.question,
        "max_marks": item.max_marks,
        "marking_notes": item.marking_notes,
        "level": item.level,
        "memo_answer": item.answer,
        "proposed_answer": issue["setter_answer"],
        "working": issue["working"],
    }


def _budget_question(s: RunState, spent: Decimal, limit: Decimal, settled: int) -> Json:
    total = len(s["scripts"])
    per = spent / settled if settled else Decimal(0)
    return {
        "topic": "budget",
        "title": f"R{_money(spent)} spent of R{_money(limit)}: continue?",
        "spent_zar": _money(spent),
        "limit_zar": _money(limit),
        "scripts_settled": settled,
        "scripts_total": total,
        "spent": [{"stage": "marking", "zar": _money(spent)}],
        "estimate": _estimate(s, per, total, settled),
    }


def _estimate(s: RunState, per: Decimal, total: int, settled: int) -> list[Json]:
    """What is left, at this run's own rate per script; only stages with a rate are estimated."""
    rows: list[Json] = []
    if total > settled:
        rows.append(
            {
                "stage": f"marking the last {total - settled} scripts",
                "zar": _money(per * (total - settled)),
            }
        )
    if not s.get("moderated"):
        rule = _pack(s).moderation
        sample = min(total, max(rule.min_scripts, math.ceil(total * rule.sample_percent / 100)))
        rows.append({"stage": f"blind moderation, {sample} scripts", "zar": _money(per * sample)})
    return rows


def _disagreement(
    d: Disagreement, item: MemoItem, script: Script, first: MarkedScript, second: MarkedScript
) -> Json:
    def pen(ms: MarkedScript) -> Json:
        m = next(x for x in ms.marks if x.question_id == d.question_id)
        return {"awarded": m.awarded, "evidence_quote": m.evidence_quote, "memo_line": m.memo_line}

    answer = next((a.text for a in script.answers if a.question_id == d.question_id), "")
    return {
        "script_id": d.script_id,
        "question_id": d.question_id,
        "question": item.question,
        "max_marks": item.max_marks,
        "memo_answer": item.answer,
        "marking_notes": item.marking_notes,
        "answer": answer,
        "first": pen(first),
        "second": pen(second),
        "reason": d.reason,
    }


def teacher_marks(memo: Memo, script: Script, marks: Sequence[Json]) -> MarkedScript:
    """set_marks: principle "teacher's decision", the learner's whole answer as the quote."""
    given = {str(m["question_id"]): int(m["awarded"]) for m in marks}
    answers = {a.question_id: a.text for a in script.answers}
    out: list[Mark] = []
    for item in memo.items:
        awarded = given[item.question_id]
        out.append(
            Mark(
                question_id=item.question_id,
                awarded=awarded,
                max_marks=item.max_marks,
                evidence_quote=answers.get(item.question_id, "") if awarded else "",
                memo_line=item.answer,
                principle="teacher's decision",
            )
        )
    return MarkedScript(
        script_id=script.script_id,
        marker="teacher",
        marks=tuple(out),
        total=sum(m.awarded for m in out),
    )


def _moderation_data(s: RunState) -> ModerationReportData:
    compared = s.get("questions_compared", 0)
    return ModerationReportData(
        title=s["title"],
        sample=tuple(sorted(s.get("moderated", {}))) or ("none",),
        disagreements=tuple(Disagreement.model_validate(d) for d in s.get("disagreements", [])),
        resolutions=s.get("resolutions", {}),
        questions_compared=max(compared, 1),
        moderator=MODERATOR,
    )


def build_bundle(p: Planner, s: RunState) -> Json:
    """console-ui.md §6 Bundle: names appear here and only here (the teacher's output)."""
    memo = _memo(s)
    names = s["names"]
    marked = _marked(s)
    converted = {k: ConvertedMark.model_validate(v) for k, v in s.get("converted", {}).items()}
    pack = _pack(s)
    task = find_task(pack, s["poa_task"])
    drafts = cast(Json, s.get("drafts"))
    verdicts = {v["pseudonym"]: v for v in s.get("verdicts", [])}
    not_reviewed = f"not reviewed: {s.get('review_incomplete') or 'no verdict was returned'}"
    sheet_review = s.get("class_sheet_review") or {"passed": False, "reason": not_reviewed}
    moderated = set(s.get("moderated", {}))
    re_marked = set(s.get("re_marked", []))
    n = len(marked)
    spent = _spent(s)
    average = (
        Decimal(sum(m.total for m in marked.values())) / n / memo.total_marks * 100
        if n
        else Decimal(0)
    )
    started = datetime.fromisoformat(s["started_at"])
    compared = s.get("questions_compared", 0)
    rows = sorted(
        (
            {
                "learner": names[k],
                "raw": c.raw,
                "out_of": c.out_of,
                "sba_points": str(c.sba_points),
                "year_points": str(c.year_points),
                "moderated": k in moderated,
                "re_marked": k in re_marked,
            }
            for k, c in converted.items()
        ),
        key=lambda r: r["learner"],
    )
    notes: list[Json] = []
    for note in drafts["learner_notes"]:
        v = verdicts.get(note["pseudonym"]) or {"passed": False, "reason": not_reviewed}
        notes.append(
            {
                "learner": names[note["pseudonym"]],
                "outcome": note["outcome"],
                "mark_with_quote": note["mark_with_quote"],
                "next_task": note["next_task"],
                "self_assessment_prompt": note["self_assessment_prompt"],
                "review": {"passed": v["passed"], "reason": v["reason"]},
            }
        )
    return {
        "assessment_id": s["assessment_id"],
        "title": s["title"],
        "state": S.AWAITING_APPROVAL.value,
        "summary": {
            "class_average_percent": _money(average),
            "scripts": n,
            "moderated": len(moderated),
            "questions_compared": compared,
            "questions_agreed": compared - len(s.get("disagreements", [])),
            "re_marked": len(re_marked),
            "cost_zar": _money(spent),
            "per_script_zar": _money(spent / n if n else Decimal(0)),
            "priced": p.priced,
            "elapsed_s": int((p.clock() - started).total_seconds()),
            "teacher_steps": s.get("teacher_steps", 0),
        },
        "mark_sheet": rows,
        "mark_sheet_csv": mark_sheet_csv([(names[k], c) for k, c in converted.items()], task),
        "item_analysis": s.get("item_stats", []),
        "misconceptions": s.get("misconceptions", []),
        "catch_up_plan_md": drafts["catch_up_plan_md"],
        "learner_notes": notes,
        "moderation_report_md": drafts["moderation_report_md"],
        "class_sheet_md": drafts["class_sheet_md"],
        "memo_unverified": s.get("memo_unverified", []),
        "class_sheet_review": {
            "passed": bool(sheet_review.get("passed")),
            "reason": str(sheet_review.get("reason") or ""),
        },
    }


def _executor_bundle(p: Planner, s: RunState) -> Bundle:
    names = s["names"]
    drafts = cast(Json, s.get("drafts"))
    pack = _pack(s)
    return Bundle(
        root=str(p.data_dir / s["assessment_id"] / "bundle"),
        title=s["title"],
        task=find_task(pack, s["poa_task"]),
        mark_sheet_rows=tuple(
            SheetRow(learner=names[k], converted=ConvertedMark.model_validate(v))
            for k, v in s.get("converted", {}).items()
        ),
        class_feedback_md=drafts["class_sheet_md"],
        catch_up_plan_md=drafts["catch_up_plan_md"],
        moderation_report_md=drafts["moderation_report_md"],
        learner_notes=tuple(
            NamedNote(name=names[n["pseudonym"]], note=LearnerNote.model_validate(n))
            for n in drafts["learner_notes"]
        ),
    )


def _messages(p: Planner, s: RunState, bundle: Bundle) -> list[Message]:
    """Only the bundle notice to the teacher; nothing goes to learners or parents (§6.2)."""
    if not p.teacher_email:
        return []
    return [
        Message(
            to=p.teacher_email,
            subject=f"Mothusi: {s['title']} is approved and filed",
            body=(
                f"The bundle for {s['title']} is written to {bundle.root}.\n"
                f"{len(bundle.learner_notes)} learner notes are with you; "
                "none was sent to a learner or parent.\n"
            ),
        )
    ]


def teacher_email() -> str | None:
    return os.environ.get("MOTHUSI_TEACHER_EMAIL") or None


Topic = Literal["memo", "budget", "script", "approval"]


def new_run(
    p: Planner,
    *,
    assessment_id: str,
    goal: str,
    memo: Memo,
    scripts: Sequence[Script],
    names: dict[str, str],
    pack_path: Path,
    poa_task: str,
    budget_zar: Decimal,
    atp_weeks: Sequence[AtpWeek] = (),
    mode: Literal["csv", "pages"] = "csv",
) -> RunState:
    """The initial state, and the `created` event at seq 0 (console-ui.md §6).

    `names` maps pseudonym to name; it stays in the checkpoint for the bundle and nowhere else.
    """
    pack = load_pack(pack_path)
    task = find_task(pack, poa_task)
    check_memo_fits(memo, task)
    if not scripts and not (mode == "pages" and names):
        raise ValueError("an assessment needs at least one script")
    title = f"Grade {pack.grade} {pack.subject} · Term {task.term} {task.name}"
    sink = p.sink(assessment_id)
    if sink.read():
        raise ValueError(f"assessment {assessment_id} already has an event log")
    sink.emit(
        EventKind.CREATED,
        f"{title}: {len(scripts)} scripts, budget R{_money(budget_zar)}",
        {
            "title": title,
            "goal": goal,
            "pack_id": pack_ref(pack_path),
            "poa_task": task.name,
            "subject": pack.subject,
            "grade": pack.grade,
            "term": task.term,
            "total_marks": memo.total_marks,
            "budget_zar": _money(budget_zar),
            "script_ids": [x.script_id for x in scripts] or sorted(names),
            "mode": mode,
            "questions": [
                {
                    "question_id": i.question_id,
                    "question": i.question,
                    "max_marks": i.max_marks,
                    "level": i.level,
                }
                for i in memo.items
            ],
        },
    )
    if not p.priced:
        sink.decision(
            "skip check",
            "no sourced model prices (packages/llm/pricing.toml; "
            "docs/resources-to-fetch.md row 4): "
            "cost is not metered and the budget check is skipped",
        )
    return {
        "assessment_id": assessment_id,
        "state": S.DRAFT.value,
        "title": title,
        "goal": goal,
        "pack_path": str(pack_path),
        "poa_task": task.name,
        "budget_zar": str(budget_zar),
        "started_at": p.clock().isoformat(),
        "memo": memo.model_dump(mode="json"),
        "scripts": [x.model_dump(mode="json") for x in scripts],
        "names": dict(names),
        "atp_weeks": [w.model_dump(mode="json") for w in atp_weeks],
        "teacher_steps": 0,
        "mode": mode,
    }


async def _read_pages(p: Planner, s: RunState) -> Update:
    """Pages mode (intake.md §11.4): uploads to crops, crops to text, then the reading check."""
    aid = s["assessment_id"]
    sink = p.sink(aid)
    root = p.data_dir / aid
    manifests = load_manifests(root / "inputs")
    # CPU-bound (PDF rendering, deskew, QR decoding): off the event loop, which also serves
    # /health/live, the SSE pings and the other assessments.
    intake = await asyncio.to_thread(
        read_uploads, aid, root / "inputs" / "pages", manifests, root / "crops"
    )
    for message in intake.refused:
        sink.check(
            CheckResult(check="page", about="upload", passed=False, detail=message),
            AgentRole.INTAKE,
        )
    if not intake.crops:
        sink.decision("halt", "no page could be read; upload Mothusi answer sheets and start again")
        return {"halted": "no readable pages"}
    by_name = {name: pseudonym for pseudonym, name in s["names"].items()}
    reader = p.provider(aid, "reader")
    scripts: list[Script] = []
    swept = 0
    spent = _spent(s)
    tokens_in, tokens_out = s.get("input_tokens", 0), s.get("output_tokens", 0)
    limit = Decimal(s["budget_zar"])
    for pseudonym in sorted(intake.crops):
        if p.priced and spent > limit:  # metered per learner, so a paid reader cannot run away
            sink.decision(
                "halt",
                f"R{_money(spent)} spent of R{_money(limit)} while reading pages; "
                "raise the budget and start the reading again",
            )
            return {"halted": "budget reached while reading pages", "spent_zar": str(spent)}
        brief = Brief(
            brief_id=f"{aid}/read/{pseudonym}",
            assessment_id=aid,
            agent=AgentRole.INTAKE,
            task="Transcribe each answer box exactly; flag any unclear line",
            inputs={
                "pseudonym": pseudonym,
                "crops": [c.model_dump() for c in intake.crops[pseudonym]],
            },
        )
        result = await read_script(brief, reader, by_name)
        script = Script.model_validate(result.outputs["script"])
        swept += int(result.outputs["swept"])
        scripts.append(script)
        used_in, used_out, cost = p.drain(aid)
        spent, tokens_in, tokens_out = spent + cost, tokens_in + used_in, tokens_out + used_out
    # A learner none of whose pages arrived (the teacher chose to start anyway) stays on the
    # roster: every answer is unreadable, so the reading check asks about each one.
    absent = sorted(set(s["names"]) - {x.script_id for x in scripts})
    scripts += [Script(script_id=x, learner_pseudonym=x, answers=()) for x in absent]
    scripts.sort(key=lambda x: x.script_id)
    memo_ids = [i.question_id for i in _memo(s).items]
    scripts = [_with_missing_pages(x, memo_ids) for x in scripts]
    sink.decision(
        "intake",
        f"{len(scripts) - len(absent)} scripts read from photographed pages"
        + (f"; no pages for {', '.join(absent)}, asked at the reading check" if absent else ""),
    )
    if swept:
        sink.decision(
            "names removed", f"{swept} learner name(s) found in answers were pseudonymised"
        )
    so_far: RunState = {
        **s,
        "spent_zar": str(spent),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
    }
    update = _usage(p, so_far, {"scripts": [x.model_dump(mode="json") for x in scripts]})
    items = reading_items(aid, scripts, root / "crops")
    if not items:
        return {**update, **_move(sink, s, S.MEMO_CHECK, "every page read clearly")}
    question: Json = {
        "topic": "reading",
        "title": f"{len(items)} reading(s) to check before marking",
        "items": items,
    }
    return {
        **update,
        **_move(sink, s, S.READING_CHECK, "some lines were unclear"),
        **_ask(sink, question),
    }


async def _passages_for(
    p: Planner,
    sink: EventSink,
    memo: Memo,
    pack: SubjectPack,
    found: Sequence[Misconception],
) -> dict[str, list[Passage]]:
    """knowledge.md §4.2: CAPS and ATP passages per misconception, for Support to cite.

    Retrieval never blocks marking: a failed search is a decision event and Support is briefed
    without passages (every citation must then be empty)."""
    if isinstance(p.knowledge, NoKnowledge):
        if found:
            sink.decision(
                "no knowledge service",
                "knowledge service not configured; catch-up items carry no passage citations",
            )
        return {}
    questions = {item.question_id: item.question for item in memo.items}
    out: dict[str, list[Passage]] = {}
    try:
        for m in found:
            query = f"{questions.get(m.question_id, '')}\n{m.pattern}".strip()
            out[m.question_id] = list(
                await p.knowledge.search(
                    query,
                    k=3,
                    subject=pack.subject,
                    grade=pack.grade,
                    kinds=(PassageKind.ATP, PassageKind.CAPS),
                )
            )
    except KnowledgeError as error:
        sink.decision("knowledge search failed", f"{error}; Support briefed without passages")
        return {}
    return out


def _with_missing_pages(script: Script, memo_ids: Sequence[str]) -> Script:
    """A question whose box was on a page that never arrived becomes an unreadable answer, so
    the teacher types it (or confirms it blank) at the reading check: it is never a silent zero."""
    have = {a.question_id: a for a in script.answers}
    answers = tuple(
        have.get(q) or Answer(question_id=q, text="", unreadable=True) for q in memo_ids
    )
    return script.model_copy(update={"answers": answers})


def reading_items(assessment_id: str, scripts: Sequence[Script], crop_dir: Path) -> list[Json]:
    """One item per uncertain line, and one per unreadable box (line_no None). A box whose page
    never arrived has no crop: its item says `page_missing` and carries no crop_url."""
    items: list[Json] = []
    for script in scripts:
        for answer in script.answers:
            has_crop = crop_path(crop_dir, script.script_id, answer.question_id).exists()
            base: Json = {
                "script_id": script.script_id,
                "question_id": answer.question_id,
                "crop_url": (
                    crop_link(assessment_id, script.script_id, answer.question_id)
                    if has_crop
                    else None
                ),
                "page_missing": not has_crop,
            }
            if answer.unreadable:
                items.append({**base, "line_no": None, "line": ""})
                continue
            lines = answer.text.split("\n")
            for n in answer.uncertain_lines:
                items.append({**base, "line_no": n, "line": lines[n]})
    return items


def apply_readings(
    scripts: Sequence[Script],
    items: Sequence[Json],
    answers: Sequence[Json],
    names: Mapping[str, str],
) -> tuple[Script, ...]:
    """Apply the teacher's ReadingAnswers; every flagged line must be answered exactly once.

    The teacher's text is swept for class-list names (name -> pseudonym) like the reader's, so a
    correction that names a learner never reaches the Marker. A "page missing" item (an answer
    the learner's uploaded pages did not hold) is answered like an unreadable box.
    """
    expected = {(i["script_id"], i["question_id"], i["line_no"]) for i in items}
    parsed = [ReadingAnswer.model_validate(a) for a in answers]
    given = [(a.script_id, a.question_id, a.line_no) for a in parsed]
    if len(set(given)) != len(given) or set(given) != expected:
        raise ValueError("answer every flagged line exactly once")
    by_answer: dict[tuple[str, str], list[ReadingAnswer]] = {}
    for a in parsed:
        by_answer.setdefault((a.script_id, a.question_id), []).append(a)
    out: list[Script] = []
    for script in scripts:
        new_answers: list[Answer] = []
        for answer in script.answers:
            todo = by_answer.get((script.script_id, answer.question_id), [])
            if not todo:
                new_answers.append(answer)
                continue
            if answer.unreadable:
                fixed = Answer(question_id=answer.question_id, text=todo[0].text)
            else:
                lines = answer.text.split("\n")
                for a in sorted(todo, key=lambda r: cast(int, r.line_no), reverse=True):
                    if a.action == "correct":
                        lines[cast(int, a.line_no)] = a.text
                fixed = Answer(question_id=answer.question_id, text="\n".join(lines))
            new_answers.append(sweep_names(fixed, names)[0] if names else fixed)
        out.append(script.model_copy(update={"answers": tuple(new_answers)}))
    return tuple(out)


def pack_ref(path: Path) -> str:
    """The console's pack id: the file's path under packs/, e.g. "dbe-caps/mathematics-gr10"."""
    return f"{path.parent.name}/{path.stem}"


def check_memo_fits(memo: Memo, task: PoATask) -> None:
    """The mark sheet converts out of task.max_marks: a memo with another total cannot be used."""
    if memo.total_marks != task.max_marks:
        raise ValueError(
            f"the memo totals {memo.total_marks} marks but {task.name} is out of {task.max_marks}"
        )


def attempt_is_complete(memo: Memo, marks: Sequence[Json]) -> bool:
    """An attempt the teacher can adopt marks every memo question once, within its maximum."""
    items = {i.question_id: i.max_marks for i in memo.items}
    given = [str(m["question_id"]) for m in marks]
    return sorted(given) == sorted(items) and all(
        0 <= int(m["awarded"]) <= items[str(m["question_id"])] for m in marks
    )


def pseudonymise_text(text: str, names: dict[str, str]) -> str:
    """The teacher's own words with every learner name replaced by pseudonyms.

    `names` maps pseudonym to name. Full names first; then any first name or surname of three or
    more letters: one learner's becomes that pseudonym, one shared by several becomes all of theirs
    ("L-002 or L-003"), so no part of any name survives. Matching ignores case and keeps word
    boundaries, so "Sipho's" becomes "L-021's".
    """
    out = text
    for pseudonym, name in sorted(names.items(), key=lambda kv: -len(kv[1])):
        out = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", pseudonym, out, flags=re.IGNORECASE)
    owners: dict[str, set[str]] = {}
    for pseudonym, name in names.items():
        for part in name.split():
            if len(part) >= 3:
                owners.setdefault(part.casefold(), set()).add(pseudonym)
    for part, who in sorted(owners.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(
            rf"(?<!\w){re.escape(part)}(?!\w)",
            " or ".join(sorted(who)),
            out,
            flags=re.IGNORECASE,
        )
    return out


def names_left(text: str, names: dict[str, str]) -> list[str]:
    """Any learner name, or part of one (three letters or more), still in `text`."""
    parts = {p for n in names.values() for p in (n, *n.split()) if len(p) >= 3}
    return sorted(
        p for p in parts if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.IGNORECASE)
    )
