"""Learner accounts and practice results: learner-section.md §6.4."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from mothusi_contracts.models import Answer, AtpWeek, Contract, Memo

LearnerCode = Annotated[str, Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{8}$")]


class ConsentRecord(Contract):
    """The teacher's record that the school holds the parental consent form."""

    recorded_by: str = Field(min_length=1)
    recorded_at: datetime
    basis: Literal["signed parental consent form held by the school"]


class LearnerAccount(Contract):
    """A code-only learner identity, with consent required before activation."""

    code: LearnerCode
    class_id: str = Field(min_length=1)
    state: Literal["invited", "consented", "active", "disabled"]
    consent: ConsentRecord | None = None

    @model_validator(mode="after")
    def _consent_matches_state(self) -> Self:
        if self.state in ("consented", "active") and self.consent is None:
            raise ValueError("consented and active accounts require consent")
        if self.state == "invited" and self.consent is not None:
            raise ValueError("invited accounts require consent is None")
        return self


class ClassGroup(Contract):
    """A teacher's class and its curriculum pack."""

    class_id: str = Field(min_length=1)
    teacher_sub: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=60)


class PracticeTest(Contract):
    """An ATP-grounded memo and its teacher approval."""

    test_id: str = Field(min_length=1)
    class_id: str = Field(min_length=1)
    atp_week: AtpWeek
    memo: Memo
    citations: tuple[str, ...] = ()
    state: Literal["drafting", "checked", "approved", "open", "closed", "refused"]
    approved_by: str | None = None
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def _published_test_has_approval(self) -> Self:
        if self.state in ("approved", "open", "closed") and (
            not self.approved_by or self.approved_at is None
        ):
            raise ValueError("approved, open and closed tests require approved_by and approved_at")
        return self


class PracticeQuestionResult(Contract):
    """Checked marks, learner quotes and the approved memo answer for one question."""

    question_id: str = Field(min_length=1)
    question: str
    awarded: int = Field(ge=0)
    max_marks: int = Field(gt=0)
    # A blank quote is no evidence (stripped to "" it would satisfy "credit needs a quote").
    evidence: tuple[Annotated[str, Field(min_length=1)], ...] = ()
    worked_answer: str = Field(min_length=1)

    @model_validator(mode="after")
    def _award_within_maximum(self) -> Self:
        if self.awarded > self.max_marks:
            raise ValueError("awarded exceeds max_marks")
        return self

    @model_validator(mode="after")
    def _evidence_for_any_credit(self) -> Self:
        # The Marker gives one decision per question, with one quote however many marks it awards
        # (Mark.evidence_quote): credit needs at least one quote, a zero needs none.
        if self.awarded > 0 and not self.evidence:
            raise ValueError("evidence needs a quote when marks are awarded")
        if self.awarded == 0 and self.evidence:
            raise ValueError("evidence must be empty when no mark is awarded")
        return self


class PracticeResult(Contract):
    """The whole learner-visible result, with no model feedback prose."""

    test_title: str = Field(min_length=1)
    total: int = Field(ge=0)
    max_total: int = Field(gt=0)
    questions: tuple[PracticeQuestionResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _total_matches_awards(self) -> Self:
        if self.total != sum(question.awarded for question in self.questions):
            raise ValueError("total must equal the sum of awarded marks")
        return self

    @model_validator(mode="after")
    def _maximum_matches_questions(self) -> Self:
        if self.max_total != sum(question.max_marks for question in self.questions):
            raise ValueError("max_total must equal the sum of max_marks")
        return self

    @model_validator(mode="after")
    def _question_ids_unique(self) -> Self:
        ids = [question.question_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question_ids must be unique")
        return self


class Attempt(Contract):
    """A submission carries a learner-visible result only after marking succeeds."""

    attempt_id: str = Field(min_length=1)
    test_id: str = Field(min_length=1)
    code: LearnerCode
    answers: tuple[Answer, ...] = Field(min_length=1)
    state: Literal["submitted", "marked", "needs_teacher"]
    result: PracticeResult | None = None

    @model_validator(mode="after")
    def _result_matches_state(self) -> Self:
        if self.state == "marked" and self.result is None:
            raise ValueError("marked attempts require result")
        if self.state != "marked" and self.result is not None:
            raise ValueError("only marked attempts may carry result")
        return self


def new_learner_code(rng: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """Encode five random bytes as eight Crockford base32 characters."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    bits = int.from_bytes(rng(5), "big")
    return "".join(alphabet[(bits >> shift) & 31] for shift in range(35, -1, -5))
