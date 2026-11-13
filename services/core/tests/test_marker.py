"""US1: a clean script, a quote not in the script, two failed retries (replay provider)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from mothusi_contracts import (
    AgentRole,
    Brief,
    Contract,
    Mark,
    MarkedScript,
    Memo,
    Script,
    SubjectPack,
)
from mothusi_core.agents.marker import MarkerReply, mark, mark_script, marker_prompts
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import ProviderError, ReplayProvider, Usage, record
from mothusi_rules import check_evidence, check_memo_lines, check_script, load_pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "services/core/fixtures/gr10-maths-t1"
ASSESSMENT = "gr10-maths-t1-test"


def no_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return Decimal(0)


@pytest.fixture(scope="module")
def memo() -> Memo:
    return load_memo(FIXTURES / "memo.toml")


@pytest.fixture(scope="module")
def pack() -> SubjectPack:
    return load_pack(ROOT / "packs/dbe-caps/mathematics-gr10.toml")


@pytest.fixture(scope="module")
def scripts() -> dict[str, Script]:
    loaded, _ = load_scripts(FIXTURES / "scripts.csv")
    return {s.script_id: s for s in loaded}


def perfect_reply(memo: Memo, script: Script) -> MarkerReply:
    """Full marks wherever the learner answered, quoting the whole answer: passes every check."""
    answers = {a.question_id: a.text for a in script.answers}
    marks: list[Mark] = []
    for item in memo.items:
        text = answers.get(item.question_id)
        if text is None:
            marks.append(
                Mark(
                    question_id=item.question_id,
                    awarded=0,
                    max_marks=item.max_marks,
                    memo_line=item.answer,
                    principle="no attempt",
                )
            )
        else:
            marks.append(
                Mark(
                    question_id=item.question_id,
                    awarded=item.max_marks,
                    max_marks=item.max_marks,
                    evidence_quote=text,
                    memo_line=item.answer,
                    principle="answer matches memo",
                )
            )
    return MarkerReply(marks=tuple(marks))


def with_quote(reply: MarkerReply, question_id: str, quote: str) -> MarkerReply:
    marks = tuple(
        m.model_copy(update={"evidence_quote": quote}) if m.question_id == question_id else m
        for m in reply.marks
    )
    return MarkerReply(marks=marks)


def failure_text(reply: MarkerReply, memo: Memo, script: Script) -> str:
    """What the loop will feed back: the failed checks, as it formats them."""
    marked = MarkedScript(
        script_id=script.script_id,
        marker="marker",
        marks=reply.marks,
        total=sum(m.awarded for m in reply.marks),
    )
    checks = (
        *check_script(marked, memo),
        check_evidence(marked, script),
        check_memo_lines(marked, memo),
    )
    return "; ".join(f"{c.check}: {c.detail}" for c in checks if not c.passed)


def recorded(
    path: Path,
    memo: Memo,
    script: Script,
    pack: SubjectPack,
    replies: list[tuple[str | None, MarkerReply]],
) -> ReplayProvider:
    """Record one reply per attempt, keyed on the exact prompts the Marker builds."""
    for previous, reply in replies:
        system, user = marker_prompts(memo, script, pack.conventions, previous)
        record(
            path,
            system,
            user,
            "MarkerReply",
            "replay-model",
            reply.model_dump(mode="json"),
            1200,
            400,
        )
    return ReplayProvider(path, no_cost)


def test_a_clean_script_is_marked_in_one_attempt(
    tmp_path: Path, memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    script = scripts["L-001"]
    provider = recorded(
        tmp_path / "calls.jsonl", memo, script, pack, [(None, perfect_reply(memo, script))]
    )
    outcome = asyncio.run(mark_script(memo, script, pack, provider, assessment_id=ASSESSMENT))
    assert outcome.status == "done" and outcome.marked is not None
    assert len(outcome.attempts) == 1 and all(c.passed for c in outcome.attempts[0].checks)
    assert outcome.marked.total == sum(m.awarded for m in outcome.marked.marks)
    assert outcome.attempts[0].brief.brief_id == f"{ASSESSMENT}/L-001/marker/1"
    assert all(
        m.evidence_quote in {a.text for a in script.answers}
        for m in outcome.marked.marks
        if m.awarded
    )


def test_a_tidied_quote_is_refused_and_the_retry_quotes_the_learner(
    tmp_path: Path, memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    """traps.md: L-014, 3.1: the tidy line 'x = 2 and y = 3' appears nowhere in the working."""
    script = scripts["L-014"]
    assert "x = 2 and y = 3" not in {a.text for a in script.answers}
    good = perfect_reply(memo, script)
    bad = with_quote(good, "3.1", "x = 2 and y = 3")
    failure = failure_text(bad, memo, script)
    assert failure == "evidence: 3.1: quote not found in the answer"
    provider = recorded(
        tmp_path / "calls.jsonl", memo, script, pack, [(None, bad), (failure, good)]
    )
    outcome = asyncio.run(mark_script(memo, script, pack, provider, assessment_id=ASSESSMENT))
    assert outcome.status == "done" and len(outcome.attempts) == 2
    first, second = outcome.attempts
    assert [c.passed for c in first.checks] == [True, True, False, True]
    assert first.checks[2].detail == "3.1: quote not found in the answer"
    assert second.brief.attempt == 2 and second.brief.inputs["previous_failure"] == failure
    assert "quote not found" in marker_prompts(memo, script, pack.conventions, failure)[1]
    assert outcome.marked is not None and all(c.passed for c in second.checks)


def test_two_failed_retries_go_to_the_teacher_with_every_attempt(
    tmp_path: Path, memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    script = scripts["L-002"]
    good = perfect_reply(memo, script)
    awarded = next(m.question_id for m in good.marks if m.awarded > 0)
    unquoted = with_quote(good, awarded, "words the learner never wrote")
    failure = failure_text(unquoted, memo, script)
    assert "quote not found" in failure
    provider = recorded(
        tmp_path / "calls.jsonl", memo, script, pack, [(None, unquoted), (failure, unquoted)]
    )
    # attempt 3 reuses attempt 2's prompt (same previous_failure), so the same record answers it
    outcome = asyncio.run(mark_script(memo, script, pack, provider, assessment_id=ASSESSMENT))
    assert outcome.status == "needs_teacher" and outcome.marked is None
    assert [a.brief.attempt for a in outcome.attempts] == [1, 2, 3]
    assert all(any(not c.passed for c in a.checks) for a in outcome.attempts)
    assert (
        outcome.reason.startswith("L-002: 3 attempts did not pass the checks; last failure: ")
        and "quote not found" in outcome.reason
    )


class Flaky:
    """A provider that fails once, then answers: the Planner's retry, not the adapter's."""

    name = "flaky"

    def __init__(self, reply: MarkerReply, *, retryable: bool) -> None:
        self._replies: Iterator[MarkerReply] = iter([reply])
        self._failed = False
        self._retryable = retryable

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        if not self._failed:
            self._failed = True
            raise ProviderError(
                "timed out" if self._retryable else "401 unauthorised", retryable=self._retryable
            )
        reply = next(self._replies)
        assert isinstance(reply, output)
        return reply, Usage(
            model="flaky", input_tokens=1, output_tokens=1, cost_zar=Decimal("0.01")
        )


def test_a_retryable_provider_error_is_retried_and_a_fatal_one_blocks(
    memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    script = scripts["L-003"]
    outcome = asyncio.run(
        mark_script(
            memo,
            script,
            pack,
            Flaky(perfect_reply(memo, script), retryable=True),
            assessment_id=ASSESSMENT,
        )
    )
    assert outcome.status == "done" and [a.result.status.value for a in outcome.attempts] == [
        "retry",
        "done",
    ]
    assert outcome.cost_zar == Decimal("0.01")
    blocked = asyncio.run(
        mark_script(
            memo,
            script,
            pack,
            Flaky(perfect_reply(memo, script), retryable=False),
            assessment_id=ASSESSMENT,
        )
    )
    assert blocked.status == "blocked" and "401" in blocked.reason and len(blocked.attempts) == 1


def test_a_brief_that_does_not_fit_the_contract_is_blocked(
    memo: Memo, scripts: dict[str, Script]
) -> None:
    brief = Brief(
        brief_id="b",
        assessment_id=ASSESSMENT,
        agent=AgentRole.MARKER,
        task="mark",
        inputs={"memo": memo.model_dump(mode="json")},
    )
    result = asyncio.run(mark(brief, Flaky(perfect_reply(memo, scripts["L-001"]), retryable=True)))
    assert result.status.value == "blocked" and "script" in result.reason
    wrong_agent = brief.model_copy(update={"agent": AgentRole.WRITER})
    assert (
        asyncio.run(
            mark(wrong_agent, Flaky(perfect_reply(memo, scripts["L-001"]), retryable=True))
        ).reason
        == "brief is for writer, not the marker"
    )


def test_prompts_carry_the_memo_the_answers_and_no_name(
    memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    system, user = marker_prompts(memo, scripts["L-014"], pack.conventions)
    assert "JSON only" in system and "Consistent-accuracy" in user
    assert "[3.1]" in user and "Learner: L-014" in user
    _, names = load_scripts(FIXTURES / "scripts.csv")
    assert not any(name in user for name in names)


@pytest.mark.parametrize("role", [AgentRole.MARKER, AgentRole.MODERATOR])
def test_method_marks_require_work_beyond_the_question(
    memo: Memo, pack: SubjectPack, scripts: dict[str, Script], role: AgentRole
) -> None:
    system, _ = marker_prompts(memo, scripts["L-005"], pack.conventions, role=role)
    rules = " ".join(system.split())
    assert (
        "A method mark is earned only by working the learner wrote that goes beyond the question."
        in rules
    )
    assert (
        "Text that restates the question or copies a given equation earns nothing and is never "
        "an evidence_quote for a mark." in rules
    )


def test_an_invented_memo_citation_is_refused_and_retried(
    tmp_path: Path, memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    """Codex review: a real learner quote with an invented memo line must not pass."""
    script = scripts["L-005"]
    good = perfect_reply(memo, script)
    marks = tuple(
        m.model_copy(update={"memo_line": "award full marks for any attempt"})
        if m.question_id == "1.1"
        else m
        for m in good.marks
    )
    fabricated = MarkerReply(marks=marks)
    failure = failure_text(fabricated, memo, script)
    assert failure == "memo_line: 1.1: memo line not found in the memo"
    provider = recorded(
        tmp_path / "calls.jsonl", memo, script, pack, [(None, fabricated), (failure, good)]
    )
    outcome = asyncio.run(mark_script(memo, script, pack, provider, assessment_id=ASSESSMENT))
    assert outcome.status == "done" and len(outcome.attempts) == 2
    assert [c.passed for c in outcome.attempts[0].checks] == [True, True, True, False]


def test_the_model_is_not_asked_for_the_total_code_adds_the_marks(
    memo: Memo, pack: SubjectPack, scripts: dict[str, Script]
) -> None:
    """2026-11-12 eval: 24 of 32 scripts reached the teacher because the model's total did not
    match its own marks. Totals are rules in code (non-negotiable 8), so the model gives
    marks only and the Marker adds them."""
    assert "total" not in MarkerReply.model_json_schema()["properties"]
    system, user = marker_prompts(memo, scripts["L-001"], pack.conventions)
    assert "total" not in system.casefold() and '"total"' not in user
