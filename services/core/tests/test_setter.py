"""US6: the planted memo error is caught before marking; a clean memo raises nothing."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from mothusi_contracts import AgentRole, Brief, Memo, SubjectPack
from mothusi_core.agents.setter import (
    SetterAnswer,
    SetterReply,
    compare_with_memo,
    setter_prompts,
    sit_test,
)
from mothusi_intake import load_memo
from mothusi_llm import ReplayProvider, record
from mothusi_rules import load_pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "services/core/fixtures/gr10-maths-t1"


def no_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return Decimal(0)


@pytest.fixture(scope="module")
def memo() -> Memo:
    return load_memo(FIXTURES / "memo.toml")


@pytest.fixture(scope="module")
def pack() -> SubjectPack:
    return load_pack(ROOT / "packs/dbe-caps/mathematics-gr10.toml")


def honest_reply(memo: Memo) -> SetterReply:
    """The memo's own answers, except 2.3 where the Setter works it correctly: x = -4 (traps.md)."""
    answers = [
        SetterAnswer(
            question_id=i.question_id,
            answer="x = -4" if i.question_id == "2.3" else i.answer,
            working="worked",
        )
        for i in memo.items
    ]
    return SetterReply(answers=tuple(answers))


def test_the_planted_memo_error_is_the_one_issue(memo: Memo) -> None:
    issues = compare_with_memo(memo, honest_reply(memo))
    assert [(i.question_id, i.kind, i.setter_answer) for i in issues] == [
        ("2.3", "disagrees", "x = -4")
    ]
    assert (
        issues[0].memo_answer == memo.items[[i.question_id for i in memo.items].index("2.3")].answer
    )


def test_a_memo_that_holds_raises_no_issue(memo: Memo) -> None:
    agreeing = SetterReply(
        answers=tuple(SetterAnswer(question_id=i.question_id, answer=i.answer) for i in memo.items)
    )
    assert compare_with_memo(memo, agreeing) == ()


def test_unparsable_and_missing_answers_are_reported_not_passed(memo: Memo) -> None:
    reply = honest_reply(memo)
    answers = [a for a in reply.answers if a.question_id != "1.2"]
    answers = [
        a.model_copy(update={"answer": "see working ???"}) if a.question_id == "1.1" else a
        for a in answers
    ]
    issues = compare_with_memo(memo, SetterReply(answers=tuple(answers)))
    kinds = {i.question_id: i.kind for i in issues}
    assert (
        kinds["1.1"] == "not_comparable"
        and kinds["1.2"] == "unanswered"
        and kinds["2.3"] == "disagrees"
    )


def test_sit_test_through_the_replay_provider(
    tmp_path: Path, memo: Memo, pack: SubjectPack
) -> None:
    system, user = setter_prompts(memo, pack.conventions)
    assert "[2.3]" in user and "memo answer" not in user  # the Setter never sees the memo's answers
    record(
        tmp_path / "calls.jsonl",
        system,
        user,
        "SetterReply",
        "replay-model",
        honest_reply(memo).model_dump(mode="json"),
        900,
        400,
    )
    brief = Brief(
        brief_id="b",
        assessment_id="gr10-maths-t1-test",
        agent=AgentRole.SETTER,
        task="sit the test",
        inputs={"memo": memo.model_dump(mode="json"), "pack_conventions": list(pack.conventions)},
    )
    result = asyncio.run(sit_test(brief, ReplayProvider(tmp_path / "calls.jsonl", no_cost)))
    assert result.status.value == "done"
    assert [i["question_id"] for i in result.outputs["issues"]] == ["2.3"]
    wrong = brief.model_copy(update={"agent": AgentRole.MARKER})
    assert (
        asyncio.run(sit_test(wrong, ReplayProvider(tmp_path / "calls.jsonl", no_cost))).status.value
        == "blocked"
    )
