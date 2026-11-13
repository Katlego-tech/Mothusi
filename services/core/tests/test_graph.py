"""The Planner graph. marking-pipeline.md §5 (router), §6.2 (nodes, interrupts), §9.

The planted 6-script set runs end to end in replay mode with §5's state sequence and every trap
caught; the router has no path to `delivered` that skips `approved`; a rejection, a budget halt and
a script the checks never pass each reach the teacher and resume.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import AssessmentState as S
from mothusi_contracts import Contract, EventKind
from mothusi_core.graph.planner import Planner
from mothusi_core.graph.router import ALLOWED, IllegalTransitionError, check_transition, reachable
from mothusi_core.providers import free
from mothusi_core.smoke import ASSESSMENT, REPLAY, check_planted, run_planted
from mothusi_llm import ReplayProvider, Usage

from .conftest import ROOT

sys.path.insert(0, str(ROOT / "services/core/fixtures"))
from scripted_model import ScriptedModel

DRAWN = {  # marking-pipeline.md §5, edge by edge
    ("draft", "memo_check"),
    ("draft", "reading_check"),
    ("reading_check", "memo_check"),
    ("memo_check", "marking"),
    ("memo_check", "draft"),
    ("marking", "moderating"),
    ("moderating", "re_marking"),
    ("moderating", "analysing"),
    ("re_marking", "moderating"),
    ("analysing", "writing"),
    ("writing", "reviewing"),
    ("reviewing", "writing"),
    ("reviewing", "awaiting_approval"),
    ("awaiting_approval", "approved"),
    ("awaiting_approval", "rejected"),
    ("rejected", "writing"),
    ("approved", "delivered"),
}


# ------------------------------------------------------------------------------------ router


def test_the_router_has_exactly_the_drawn_edges() -> None:
    assert {(a.value, b.value) for a, b in ALLOWED} == DRAWN


def test_no_path_reaches_delivered_without_approved() -> None:
    """SPEC US5 acceptance: the state machine has no path to delivered that skips approved."""
    assert S.DELIVERED not in reachable(S.DRAFT, avoiding=S.APPROVED)
    assert {a for a, b in ALLOWED if b is S.DELIVERED} == {S.APPROVED}
    assert {a for a, b in ALLOWED if b is S.APPROVED} == {S.AWAITING_APPROVAL}


@pytest.mark.parametrize("to", [s for s in S if (S.MARKING, s) not in ALLOWED])
def test_an_undrawn_transition_is_refused(to: S) -> None:
    with pytest.raises(IllegalTransitionError):
        check_transition(S.MARKING, to)


# -------------------------------------------------------------------------------- replay run


def test_the_planted_set_runs_end_to_end_in_replay_mode(tmp_path: Path) -> None:
    """§5's state sequence, the memo error, L-014's quote and L-021's over-mark."""
    state, events, names = asyncio.run(run_planted(ReplayProvider(REPLAY, free), tmp_path))
    assert check_planted(state, events, names) == []
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].kind is EventKind.CREATED
    bundle = tmp_path / ASSESSMENT / "bundle"
    assert (bundle / "mark-sheet.csv").is_file() and (bundle / "notes/L-007.md").is_file()
    delivered = next(e for e in events if e.kind is EventKind.DELIVERED)
    assert str(bundle / "moderation-report.md") in delivered.payload["written"]  # type: ignore[operator]
    # The bundle is the one place names appear.
    assert names["L-007"] in (bundle / "notes/L-007.md").read_text(encoding="utf-8")


# ------------------------------------------------------------- paths the planted run avoids


class Priced:
    """The scripted model at R1 a call, so the budget question can fire."""

    name = "priced"

    def __init__(self) -> None:
        self._inner = ScriptedModel()

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        reply, usage = await self._inner.complete(system, user, output)
        return reply, usage.model_copy(update={"cost_zar": Decimal(1)})


class StubbornOnL014:
    """Never corrects L-014's tidied quote, so its script reaches the teacher."""

    name = "stubborn"

    def __init__(self) -> None:
        self._inner = ScriptedModel()

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        if "script L-014" in user and "Your previous attempt failed" in user:
            user = user.split("\n\nYour previous attempt failed")[0]
        return await self._inner.complete(system, user, output)


def planner(tmp_path: Path, primary: Any, batch_size: int = 8) -> Planner:
    return Planner(
        primary=primary,
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
        batch_size=batch_size,
    )


def test_a_rejection_reaches_the_writer_and_leaves_the_marks(tmp_path: Path) -> None:
    """SPEC US5 scenario 2: rejected -> writing, the note in the Writer's next Brief."""
    p = planner(tmp_path, ScriptedModel())

    async def run() -> Any:
        from mothusi_core.graph.planner import new_run
        from mothusi_core.smoke import FIXTURE, GOAL, PACK, PLANTED
        from mothusi_intake import load_memo, load_scripts
        from mothusi_rules import load_atp_weeks

        scripts, by_name = load_scripts(FIXTURE / "scripts.csv")
        names = {v: k for k, v in by_name.items() if v in PLANTED}
        state = await p.start(
            new_run(
                p,
                assessment_id="rejected-run",
                goal=GOAL,
                memo=load_memo(FIXTURE / "memo.toml"),
                scripts=[x for x in scripts if x.script_id in PLANTED],
                names=names,
                pack_path=PACK,
                poa_task="Test",
                budget_zar=Decimal("40"),
                atp_weeks=load_atp_weeks(FIXTURE / "atp-weeks.toml"),
            )
        )
        state = await p.resume("rejected-run", {"decision": "keep_memo", "question_id": "2.3"})
        assert state["state"] == "awaiting_approval"
        marks_before = state.get("marked")
        note = "Please name the ATP week in each learner's next task."
        state = await p.resume("rejected-run", {"decision": "reject", "note": note})
        assert state["state"] == "awaiting_approval"  # rewritten, reviewed, back for approval
        assert state.get("marked") == marks_before
        state = await p.resume("rejected-run", {"decision": "approve"})
        return state, note

    state, note = asyncio.run(run())
    assert state["state"] == "delivered"
    events = p.sink("rejected-run").read()
    moves = [(e.payload["from"], e.payload["to"]) for e in events if e.kind is EventKind.STATE]
    assert ("awaiting_approval", "rejected") in moves and ("rejected", "writing") in moves
    assert ("memo_check", "marking") in moves  # keep_memo: no amendment, straight on
    assert any(e.kind is EventKind.TEACHER and note in e.summary for e in events)
    assert state.get("teacher_steps") == 3  # memo, reject, approve


def test_a_budget_halt_asks_the_teacher_and_resumes(tmp_path: Path) -> None:
    from mothusi_core.graph.planner import new_run
    from mothusi_core.smoke import FIXTURE, GOAL, PACK
    from mothusi_intake import load_memo, load_scripts

    p = planner(tmp_path, Priced(), batch_size=2)
    scripts, by_name = load_scripts(FIXTURE / "scripts.csv")
    some = [x for x in scripts if x.script_id in ("L-001", "L-002", "L-003", "L-004")]
    names = {v: k for k, v in by_name.items() if v in {x.script_id for x in some}}

    async def run() -> Any:
        state = await p.start(
            new_run(
                p,
                assessment_id="budget-run",
                goal=GOAL,
                memo=load_memo(FIXTURE / "memo.toml"),
                scripts=some,
                names=names,
                pack_path=PACK,
                poa_task="Test",
                budget_zar=Decimal("1"),
            )
        )
        state = await p.resume("budget-run", {"decision": "use_proposed", "question_id": "2.3"})
        question = state.get("question")
        assert question is not None and question["topic"] == "budget"
        assert question["scripts_settled"] == 2 and question["scripts_total"] == 4
        assert len(state.get("marked", {})) == 2  # everything marked so far is kept
        return await p.resume("budget-run", {"raise_to_zar": "100"})

    state = asyncio.run(run())
    assert len(state.get("marked", {})) == 4
    assert state["state"] == "awaiting_approval"
    events = p.sink("budget-run").read()
    assert any(e.kind is EventKind.DECISION and "no ATP weeks given" in e.summary for e in events)


def test_a_script_the_checks_never_pass_goes_to_the_teacher(tmp_path: Path) -> None:
    from mothusi_core.graph.planner import new_run
    from mothusi_core.smoke import FIXTURE, GOAL, PACK
    from mothusi_intake import load_memo, load_scripts

    p = planner(tmp_path, StubbornOnL014())
    scripts, by_name = load_scripts(FIXTURE / "scripts.csv")
    some = [x for x in scripts if x.script_id in ("L-001", "L-014")]
    names = {v: k for k, v in by_name.items() if v in ("L-001", "L-014")}
    memo = load_memo(FIXTURE / "memo.toml")

    async def run() -> Any:
        await p.start(
            new_run(
                p,
                assessment_id="script-run",
                goal=GOAL,
                memo=memo,
                scripts=some,
                names=names,
                pack_path=PACK,
                poa_task="Test",
                budget_zar=Decimal("40"),
            )
        )
        state = await p.resume("script-run", {"decision": "keep_memo", "question_id": "2.3"})
        question = state.get("question")
        assert question is not None and question["topic"] == "script"
        assert question["script_id"] == "L-014" and len(question["attempts"]) == 3
        assert question["question_id"] == "3.1"
        marks = [{"question_id": i.question_id, "awarded": 0} for i in memo.items]
        marks[6] = {"question_id": "3.1", "awarded": 8}
        return await p.resume("script-run", {"decision": "set_marks", "marks": marks})

    state = asyncio.run(run())
    l014 = state.get("marked", {})["L-014"]
    assert l014["marker"] == "teacher" and l014["total"] == 8
    assert {m["principle"] for m in l014["marks"]} == {"teacher's decision"}
    events = p.sink("script-run").read()
    assert any("exempt from check_evidence" in e.summary for e in events)


# ---------------------------------------------------------------------------- Postgres saver


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("POSTGRES_URL"), reason="POSTGRES_URL is not set")
def test_the_planted_run_checkpoints_in_postgres(tmp_path: Path) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async def run() -> Any:
        async with AsyncPostgresSaver.from_conn_string(os.environ["POSTGRES_URL"]) as saver:
            await saver.setup()
            p = Planner(
                primary=ReplayProvider(REPLAY, free),
                fallback=None,
                data_dir=tmp_path,
                checkpointer=saver,
            )
            return await run_planted(p.primary, tmp_path, planner=p)

    state, events, names = asyncio.run(run())
    assert check_planted(state, events, names) == []


# ------------------------------------------------------------------- Codex review, round 1


class Wrapped:
    """The scripted model with one behaviour changed by `hook` (returns a reply or None)."""

    name = "wrapped"

    def __init__(self, hook: Any) -> None:
        self._inner = ScriptedModel()
        self._hook = hook

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        changed = self._hook(system, user, output.__name__)
        if changed is not None:
            return output.model_validate(changed), Usage(
                model="scripted", input_tokens=1, output_tokens=1, cost_zar=Decimal(0)
            )
        return await self._inner.complete(system, user, output)


async def start_small(
    p: Planner,
    aid: str,
    ids: tuple[str, ...],
    budget: str = "40",
    memo_answer: str | None = "keep_memo",
    pack_path: Path | None = None,
) -> Any:
    """Start a small run; answer the memo question with `memo_answer` (None: leave it pending)."""
    from mothusi_core.graph.planner import new_run
    from mothusi_core.smoke import FIXTURE, GOAL, PACK
    from mothusi_intake import load_memo, load_scripts
    from mothusi_rules import load_atp_weeks

    scripts, by_name = load_scripts(FIXTURE / "scripts.csv")
    await p.start(
        new_run(
            p,
            assessment_id=aid,
            goal=GOAL,
            memo=load_memo(FIXTURE / "memo.toml"),
            scripts=[x for x in scripts if x.script_id in ids],
            names={v: k for k, v in by_name.items() if v in ids},
            pack_path=pack_path or PACK,
            poa_task="Test",
            budget_zar=Decimal(budget),
            atp_weeks=load_atp_weeks(FIXTURE / "atp-weeks.toml"),
        )
    )
    if memo_answer is None:
        return await p.snapshot(aid)
    return await p.resume(aid, {"decision": memo_answer, "question_id": "2.3"})


def test_an_unfinished_review_marks_every_note_not_reviewed(tmp_path: Path) -> None:
    """Codex P1: a Reviewer that never finishes must not read as every note passing."""
    from mothusi_llm import ProviderError

    def hook(system: str, user: str, output: str) -> Any:
        if output == "ToneReply":
            raise ProviderError("reviewer down", retryable=False)
        return None

    p = planner(tmp_path, Wrapped(hook))
    state = asyncio.run(start_small(p, "review-down", ("L-001", "L-003")))
    assert state["state"] == "awaiting_approval"
    notes = state.get("bundle", {})["learner_notes"]
    assert notes and all(n["review"]["passed"] is False for n in notes)
    assert all("not reviewed" in n["review"]["reason"] for n in notes)
    assert state.get("bundle", {})["class_sheet_review"]["passed"] is False


def test_a_failed_blind_moderation_goes_to_the_teacher_not_to_agreement(tmp_path: Path) -> None:
    """Codex P1: a sampled script the Moderator could not mark is never silently dropped."""
    from mothusi_core.agents.marker import MODERATOR_PREAMBLE
    from mothusi_llm import ProviderError

    def hook(system: str, user: str, output: str) -> Any:
        if system.startswith(MODERATOR_PREAMBLE) and "script L-001" in user:
            raise ProviderError("moderator down", retryable=False)
        return None

    p = planner(tmp_path, Wrapped(hook))
    ids = ("L-001", "L-003", "L-007", "L-010", "L-014", "L-021")
    state = asyncio.run(start_small(p, "mod-down", ids, memo_answer="use_proposed"))
    question = state.get("question")
    assert question is not None and question["topic"] == "script"
    assert question["script_id"] == "L-001" and question["stage"] == "moderation"
    assert state["state"] == "moderating"
    assert not any(e.kind is EventKind.DISAGREEMENT for e in p.sink("mod-down").read())
    first = state.get("marked", {})["L-001"]
    marks = [{"question_id": m["question_id"], "awarded": m["awarded"]} for m in first["marks"]]
    state = asyncio.run(p.resume("mod-down", {"decision": "set_marks", "marks": marks}))
    assert state.get("moderated", {})["L-001"]["marker"] == "teacher"
    assert state.get("marked", {})["L-001"] == first  # the first marking is untouched
    assert state["state"] == "awaiting_approval"
    assert state.get("bundle", {})["summary"]["moderated"] == 6


def test_a_rejected_class_sheet_is_sent_back_with_its_reason(tmp_path: Path) -> None:
    """Codex P2: the Reviewer's class-sheet verdict counts."""
    calls: list[str] = []

    def hook(system: str, user: str, output: str) -> Any:
        if output == "CorrectionsReply":
            calls.append(user)
        if output == "ToneReply" and len([c for c in calls]) == 1:
            import re

            names = re.findall(r"^<(L-\d+)>$", user, re.MULTILINE)
            return {
                "verdicts": [{"pseudonym": n, "passed": True, "reason": ""} for n in names],
                "class_sheet_passed": False,
                "class_sheet_reason": "singles out the weakest scripts by name",
            }
        return None

    p = planner(tmp_path, Wrapped(hook))
    state = asyncio.run(start_small(p, "sheet-back", ("L-001", "L-003")))
    assert state["state"] == "awaiting_approval"
    assert len(calls) == 2 and "singles out the weakest scripts" in calls[1]
    assert state.get("bundle", {})["class_sheet_review"]["passed"] is True
    events = p.sink("sheet-back").read()
    assert any(e.kind is EventKind.DECISION and "class sheet rejected" in e.summary for e in events)


def test_the_budget_is_checked_after_the_last_batch_too(tmp_path: Path) -> None:
    """Codex P2: a run of one batch still stops at its budget before the next paid stage."""
    p = planner(tmp_path, Priced())
    state = asyncio.run(start_small(p, "one-batch", ("L-001", "L-003"), budget="1"))
    question = state.get("question")
    assert question is not None and question["topic"] == "budget"
    assert state["state"] == "moderating" and len(state.get("marked", {})) == 2
    state = asyncio.run(p.resume("one-batch", {"raise_to_zar": "100"}))
    assert state["state"] == "awaiting_approval"


def test_a_memo_that_does_not_fit_the_task_is_refused(tmp_path: Path) -> None:
    """Codex P2: a 50-mark memo on a 100-mark task would halve every percentage."""
    from mothusi_core.graph.planner import new_run
    from mothusi_core.smoke import FIXTURE, GOAL, PACK
    from mothusi_intake import load_memo, load_scripts

    p = planner(tmp_path, ScriptedModel())
    scripts, _ = load_scripts(FIXTURE / "scripts.csv")
    with pytest.raises(ValueError, match="totals 50 marks but Mid-year examination is out of 100"):
        new_run(
            p,
            assessment_id="misfit",
            goal=GOAL,
            memo=load_memo(FIXTURE / "memo.toml"),
            scripts=scripts[:1],
            names={},
            pack_path=PACK,
            poa_task="Mid-year examination",
            budget_zar=Decimal("40"),
        )


def test_keeping_an_amended_answer_the_setter_still_doubts_goes_on_to_marking(
    tmp_path: Path,
) -> None:
    """Codex P2: the amendment flag belongs to one Setter pass, not to the whole run."""
    from scripted_model import setter

    def hook(system: str, user: str, output: str) -> Any:
        if output != "SetterReply":
            return None
        answers = setter(user)["answers"]
        for a in answers:
            if a["question_id"] == "2.3":
                a["answer"], a["working"] = "x = -5", "a Setter that keeps disagreeing"
        return {"answers": answers}

    p = planner(tmp_path, Wrapped(hook))

    async def run() -> Any:
        await start_small(p, "keep-amended", ("L-001", "L-003"), memo_answer=None)
        amend = {"decision": "amend", "question_id": "2.3", "answer": "x = -4"}
        state = await p.resume("keep-amended", amend)
        question = state.get("question")
        assert question is not None and question["memo_answer"] == "x = -4"  # asked again
        return await p.resume("keep-amended", {"decision": "keep_memo", "question_id": "2.3"})

    state = asyncio.run(run())
    assert state["state"] == "awaiting_approval"
    item = next(i for i in state["memo"]["items"] if i["question_id"] == "2.3")
    assert item["answer"] == "x = -4"


def test_an_unpriced_run_says_so_and_skips_the_budget_check(tmp_path: Path) -> None:
    """Codex P2: no sourced prices is a fact not in hand: logged and skipped, never 'free'."""
    p = Planner(
        primary=Priced(),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
        priced=False,
    )
    state = asyncio.run(start_small(p, "unpriced", ("L-001", "L-003"), budget="1"))
    assert state["state"] == "awaiting_approval" and state.get("question") is None
    events = p.sink("unpriced").read()
    assert any(
        e.kind is EventKind.DECISION and "budget check is skipped" in e.summary for e in events
    )
    usage = [e for e in events if e.kind is EventKind.USAGE]
    assert usage and all(e.payload["priced"] is False for e in usage)
    assert state.get("bundle", {})["summary"]["priced"] is False


def test_a_pack_without_a_grid_logs_the_skip_and_runs_no_grid_check(tmp_path: Path) -> None:
    """SPEC US7 scenario 2: a missing slot is logged as skipped, never guessed."""
    from mothusi_core.smoke import PACK

    start, rest = PACK.read_text().split("[cognitive]", 1)
    no_grid = tmp_path / "packs" / "no-grid.toml"
    no_grid.parent.mkdir()
    no_grid.write_text(start + "[moderation]" + rest.split("[moderation]", 1)[1])
    p = planner(tmp_path, ScriptedModel())
    asyncio.run(start_small(p, "with-grid", ("L-001",)))
    asyncio.run(start_small(p, "no-grid", ("L-001",), pack_path=no_grid))

    def grid_events(aid: str) -> tuple[bool, bool]:
        events = p.sink(aid).read()
        skipped = any(
            e.kind is EventKind.DECISION
            and e.summary == "skip check: no cognitive-level grid in this pack; check skipped"
            for e in events
        )
        checked = any(
            e.kind is EventKind.CHECK and e.payload["check"] == "cognitive_grid" for e in events
        )
        return skipped, checked

    assert grid_events("with-grid") == (False, True)
    assert grid_events("no-grid") == (True, False)


def test_a_dispute_the_teacher_settles_is_recorded_as_the_teachers(tmp_path: Path) -> None:
    """Codex P2: the moderation report must not still say 'with the teacher'."""
    from mothusi_llm import ProviderError

    def hook(system: str, user: str, output: str) -> Any:
        if "A blind second marker disagreed" in user:
            raise ProviderError("re-mark down", retryable=False)
        return None

    p = planner(tmp_path, Wrapped(hook))
    ids = ("L-001", "L-003", "L-007", "L-010", "L-014", "L-021")
    state = asyncio.run(start_small(p, "settled", ids, memo_answer="use_proposed"))
    question = state.get("question")
    assert question is not None and question["script_id"] == "L-021"
    assert state.get("resolutions", {})["L-021/4"] == "with the teacher"
    first = state.get("marked", {})["L-021"]
    marks = [
        {"question_id": m["question_id"], "awarded": 2 if m["question_id"] == "4" else m["awarded"]}
        for m in first["marks"]
    ]
    state = asyncio.run(p.resume("settled", {"decision": "set_marks", "marks": marks}))
    assert state.get("resolutions", {})["L-021/4"] == "teacher's decision: 2"
    assert "teacher's decision: 2" in state.get("bundle", {})["moderation_report_md"]


def test_memo_items_code_could_not_compare_reach_the_bundle(tmp_path: Path) -> None:
    """Codex P2: unverified memo items are in the approval bundle, not only the log."""
    from scripted_model import setter

    def hook(system: str, user: str, output: str) -> Any:
        if output != "SetterReply":
            return None
        answers = setter(user)["answers"]
        for a in answers:
            if a["question_id"] == "4":
                a["answer"] = "the width is fifteen and the length thirty-three"
        return {"answers": answers}

    p = planner(tmp_path, Wrapped(hook))
    state = asyncio.run(
        start_small(p, "unverified", ("L-001", "L-003"), memo_answer="use_proposed")
    )
    unverified = state.get("bundle", {})["memo_unverified"]
    assert [u["question_id"] for u in unverified] == ["4"]


class SlowOnL003:
    """The scripted model, but L-003's marking takes a while."""

    name = "slow"

    def __init__(self) -> None:
        self._inner = ScriptedModel()

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        if output.__name__ == "MarkerReply":  # a real provider awaits the network
            await asyncio.sleep(0.3 if "script L-003" in user else 0.01)
        return await self._inner.complete(system, user, output)


def test_marking_events_are_logged_as_they_happen_not_after_the_batch(tmp_path: Path) -> None:
    """Codex P2 (SPEC US8): a slow script does not hide the ones that finished first."""
    p = planner(tmp_path, SlowOnL003())
    asyncio.run(start_small(p, "live", ("L-001", "L-003")))
    events = p.sink("live").read()

    def seq(kind: EventKind, sid: str) -> int:
        return next(
            e.seq
            for e in events
            if e.kind is kind
            and e.payload.get("script_id") == sid
            and e.agent is not None
            and e.agent.value == "marker"
        )

    assert seq(EventKind.BRIEF, "L-003") < seq(EventKind.RESULT, "L-001")
    assert seq(EventKind.RESULT, "L-001") < seq(EventKind.RESULT, "L-003")


# ------------------------------------------------------------------- Codex review, round 7


def test_names_in_the_teachers_note_become_pseudonyms() -> None:
    from mothusi_core.graph.planner import names_left, pseudonymise_text

    names = {"L-001": "Amogelang Khumalo", "L-002": "Bokang Mokoena", "L-003": "Bokang Dlamini"}
    note = "Revise amogelang khumalo's next task; Khumalo needs 2.3. Bokang too."
    out = pseudonymise_text(note, names)
    assert out == "Revise L-001's next task; L-001 needs 2.3. L-002 or L-003 too."
    assert names_left(out, names) == []  # two Bokangs: both candidates, no name left


def test_a_rejection_naming_a_learner_reaches_no_model_and_no_event(tmp_path: Path) -> None:
    """Codex P1: the teacher writes names; the Writer and the log only ever see pseudonyms."""
    seen: list[str] = []

    def hook(system: str, user: str, output: str) -> Any:
        seen.append(user)
        return None

    p = planner(tmp_path, Wrapped(hook))

    async def run() -> Any:
        state = await start_small(p, "named-note", ("L-001", "L-003"))
        name = state["names"]["L-001"]
        note = f"Please make {name}'s next task about question 2.3."
        state = await p.resume("named-note", {"decision": "reject", "note": note})
        return state, name

    state, name = asyncio.run(run())
    assert state["state"] == "awaiting_approval"
    assert not any(name.casefold() in u.casefold() for u in seen)
    assert any("Please make L-001's next task" in u for u in seen)  # the note did reach it
    raw = "\n".join(e.model_dump_json() for e in p.sink("named-note").read())
    assert name not in raw and name.split()[0] not in raw


def test_marking_batch_matches_what_the_model_host_serves_at_once() -> None:
    # marking-pipeline.md: batches of 4, the host's OLLAMA_NUM_PARALLEL (deployment-aws.md §6.2).
    from mothusi_core.graph.planner import BATCH

    assert BATCH == 4


class CountsInFlight:
    """The scripted model, recording the most calls ever in flight at once."""

    name = "counts"

    def __init__(self) -> None:
        self._inner = ScriptedModel()
        self.now = 0
        self.most = 0

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.now += 1
        self.most = max(self.most, self.now)
        try:
            await asyncio.sleep(0)  # let every call the stage launched start before any ends
            return await self._inner.complete(system, user, output)
        finally:
            self.now -= 1


def test_no_stage_sends_the_host_more_calls_than_the_batch(tmp_path: Path) -> None:
    """2026-11-12 eval: the Moderator sent its whole sample at once (and on widening, every other
    script), so calls queued on the host past the 180 s client timeout. Marking already batched."""
    model = CountsInFlight()
    p = planner(tmp_path, model, batch_size=2)
    state, _events, _names = asyncio.run(run_planted(model, tmp_path, planner=p))
    assert state["state"] == "delivered"
    assert len(state.get("sample") or []) > 2  # the moderation sample is bigger than one batch
    assert model.most <= 2
