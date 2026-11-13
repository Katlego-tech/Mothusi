"""Learner contracts follow learner-section.md §6.4, including every invariant."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from mothusi_contracts import (
    Answer,
    AtpWeek,
    Attempt,
    ClassGroup,
    ConsentRecord,
    LearnerAccount,
    LearnerCode,
    Memo,
    MemoItem,
    PracticeQuestionResult,
    PracticeResult,
    PracticeTest,
    new_learner_code,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)
CODE = "01AZHJKM"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def consent() -> ConsentRecord:
    return ConsentRecord(
        recorded_by="teacher-sub",
        recorded_at=NOW,
        basis="signed parental consent form held by the school",
    )


def question(**changes: Any) -> PracticeQuestionResult:
    data = dict(
        question_id="1",
        question="Solve x + 1 = 2",
        awarded=1,
        max_marks=2,
        evidence=("x = 1",),
        worked_answer="x = 1",
    )
    return PracticeQuestionResult.model_validate(data | changes)


def result(**changes: Any) -> PracticeResult:
    return PracticeResult.model_validate(
        dict(test_title="Practice", total=1, max_total=2, questions=(question(),)) | changes
    )


def practice(**changes: Any) -> PracticeTest:
    return PracticeTest.model_validate(
        dict(
            test_id="test-1",
            class_id="class-1",
            atp_week=AtpWeek(week=1, topic="Algebra", source="test fixture ATP row"),
            memo=Memo(
                assessment_id="test-1",
                total_marks=2,
                items=(
                    MemoItem(
                        question_id="1",
                        question="Solve x + 1 = 2",
                        max_marks=2,
                        answer="x = 1",
                        level="knowledge",
                    ),
                ),
            ),
            state="drafting",
        )
        | changes
    )


def attempt(**changes: Any) -> Attempt:
    return Attempt.model_validate(
        dict(
            attempt_id="attempt-1",
            test_id="test-1",
            code=CODE,
            answers=(Answer(question_id="1", text="x = 1"),),
            state="submitted",
        )
        | changes
    )


@pytest.mark.parametrize("code", [CODE, "00000000", "ZZZZZZZZ", "NPQRSTVW", "XYZ01234"])
def test_learner_code_accepts_crockford_characters(code: str) -> None:
    assert TypeAdapter(LearnerCode).validate_python(code) == code


@pytest.mark.parametrize(
    "code",
    [
        "I0000000",
        "L0000000",
        "O0000000",
        "U0000000",
        "abcdefgh",
        "1234567",
        "123456789",
        "!0000000",
        "1234567\n",
    ],
)
def test_learner_code_rejects_invalid_characters_and_lengths(code: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(LearnerCode).validate_python(code)


@pytest.mark.parametrize("state", ["consented", "active"])
def test_consent_required_for_enabled_accounts(state: str) -> None:
    with pytest.raises(ValidationError, match="consent"):
        LearnerAccount.model_validate(dict(code=CODE, class_id="c", state=state))
    assert (
        LearnerAccount.model_validate(
            dict(
                code=CODE,
                class_id="c",
                state=state,
                consent=consent(),
            )
        ).consent
        == consent()
    )


def test_invited_account_requires_no_consent() -> None:
    assert LearnerAccount(code=CODE, class_id="c", state="invited").consent is None
    with pytest.raises(ValidationError, match="consent"):
        LearnerAccount(code=CODE, class_id="c", state="invited", consent=consent())


@pytest.mark.parametrize("record", [None, consent()])
def test_disabled_account_allows_either_consent_state(record: ConsentRecord | None) -> None:
    assert (
        LearnerAccount(code=CODE, class_id="c", state="disabled", consent=record).state
        == "disabled"
    )


@pytest.mark.parametrize("state", ["approved", "open", "closed"])
@pytest.mark.parametrize(
    "approval",
    [{}, {"approved_by": "teacher"}, {"approved_at": NOW}, {"approved_by": "", "approved_at": NOW}],
)
def test_published_test_requires_both_approval_fields(state: str, approval: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="approv"):
        practice(state=state, **approval)
    assert practice(state=state, approved_by="teacher", approved_at=NOW).approved_at == NOW


@pytest.mark.parametrize("state", ["drafting", "checked", "refused"])
def test_unpublished_test_needs_no_approval(state: str) -> None:
    assert practice(state=state).approved_by is None
    assert practice(state=state).citations == ()


def test_credit_needs_a_quote_and_a_zero_needs_none() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        question(evidence=())
    with pytest.raises(ValidationError, match="evidence"):
        question(awarded=0, evidence=("x = 1",))
    assert question(awarded=0, evidence=()).evidence == ()
    with pytest.raises(ValidationError, match="evidence"):
        question(evidence=("  ",))  # blank after stripping: no evidence at all
    # One Marker decision can award several marks with a single quote (Mark.evidence_quote).
    assert question(awarded=2, evidence=("x = 1",)).awarded == 2


def test_question_award_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="max_marks"):
        question(awarded=3, evidence=("a", "b", "c"))
    assert question(awarded=2, evidence=("a", "b")).awarded == 2


def test_result_total_is_sum_of_awards() -> None:
    with pytest.raises(ValidationError, match="total"):
        result(total=0)
    assert result().total == 1


def test_result_maximum_is_sum_of_maxima() -> None:
    with pytest.raises(ValidationError, match="max_total"):
        result(max_total=3)


def test_result_question_ids_are_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        result(total=2, max_total=4, questions=(question(), question()))
    combined = result(
        total=1,
        max_total=4,
        questions=(
            question(),
            question(question_id="2", awarded=0, evidence=()),
        ),
    )
    assert len(combined.questions) == 2


def test_marked_attempt_requires_result() -> None:
    with pytest.raises(ValidationError, match="result"):
        attempt(state="marked")
    assert attempt(state="marked", result=result()).result == result()


@pytest.mark.parametrize("state", ["submitted", "needs_teacher"])
def test_other_attempt_states_forbid_result(state: str) -> None:
    with pytest.raises(ValidationError, match="result"):
        attempt(state=state, result=result())
    assert attempt(state=state).result is None


@pytest.mark.parametrize(
    "model,changes",
    [
        (ConsentRecord, {"recorded_by": ""}),
        (ConsentRecord, {"basis": "verbal consent"}),
        (LearnerAccount, {"class_id": ""}),
        (LearnerAccount, {"code": "I0000000"}),
        (LearnerAccount, {"state": "unknown"}),
        (ClassGroup, {"class_id": ""}),
        (ClassGroup, {"teacher_sub": ""}),
        (ClassGroup, {"pack_id": ""}),
        (ClassGroup, {"label": ""}),
        (ClassGroup, {"label": "x" * 61}),
        (PracticeTest, {"test_id": ""}),
        (PracticeTest, {"class_id": ""}),
        (PracticeTest, {"state": "unknown"}),
        (PracticeQuestionResult, {"question_id": ""}),
        (PracticeQuestionResult, {"awarded": -1}),
        (PracticeQuestionResult, {"max_marks": 0}),
        (PracticeQuestionResult, {"worked_answer": ""}),
        (PracticeResult, {"test_title": ""}),
        (PracticeResult, {"total": -1}),
        (PracticeResult, {"max_total": 0}),
        (PracticeResult, {"questions": ()}),
        (Attempt, {"attempt_id": ""}),
        (Attempt, {"test_id": ""}),
        (Attempt, {"code": "U0000000"}),
        (Attempt, {"answers": ()}),
        (Attempt, {"state": "unknown"}),
    ],
)
def test_declared_field_constraints(model: Any, changes: dict[str, Any]) -> None:
    examples = {
        ConsentRecord: consent(),
        LearnerAccount: LearnerAccount(code=CODE, class_id="c", state="invited"),
        ClassGroup: ClassGroup(class_id="c", teacher_sub="t", pack_id="p", label="10B"),
        PracticeTest: practice(),
        PracticeQuestionResult: question(),
        PracticeResult: result(),
        Attempt: attempt(),
    }
    with pytest.raises(ValidationError):
        model.model_validate(examples[model].model_dump() | changes)


def test_result_exposes_only_the_approved_learner_fields() -> None:
    visible = result().model_dump(mode="json")
    assert set(visible) == {"test_title", "total", "max_total", "questions"}
    assert set(visible["questions"][0]) == {
        "question_id",
        "question",
        "awarded",
        "max_marks",
        "evidence",
        "worked_answer",
    }
    with pytest.raises(ValidationError):
        result(rationale="model prose")
    with pytest.raises(ValidationError):
        question(feedback="model prose")
    with pytest.raises(ValidationError):
        result().total = 0  # type: ignore[misc]
    assert PracticeResult.model_validate_json(result().model_dump_json()) == result()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (bytes(5), "00000000"),
        (b"\xff" * 5, "ZZZZZZZZ"),
        (b"\x00\x44\x32\x14\xc7", "01234567"),
    ],
)
def test_new_code_uses_five_bytes_in_crockford_order(raw: bytes, expected: str) -> None:
    calls: list[int] = []

    def rng(size: int) -> bytes:
        calls.append(size)
        return raw

    assert new_learner_code(rng) == expected
    assert calls == [5]


def test_secret_codes_do_not_repeat_in_ten_thousand_draws() -> None:
    codes = [new_learner_code() for _ in range(10_000)]
    assert len(set(codes)) == 10_000
    assert all(len(code) == 8 and set(code) <= set(ALPHABET) for code in codes)
