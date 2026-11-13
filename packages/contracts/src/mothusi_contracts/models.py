"""The domain model: curriculum packs, assessments, memos, scripts, marks and checks.

Design: docs/architecture/design/domain-model.md. Every field here is one the design names; a field that is
not here must not be invented downstream. Models are frozen and reject unknown fields so that an
agent's structured output either fits the contract or fails loudly.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    """Base for every contract model: immutable, strict about fields, trims strings."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- curriculum pack


class PoATask(Contract):
    """One formal task in the programme of assessment (Circular S8 of 2023, Annexure)."""

    term: int = Field(ge=1, le=4)
    name: str = Field(min_length=1)
    kind: Literal["test", "project", "assignment", "exam", "case_study", "presentation", "report"]
    max_marks: int = Field(gt=0)
    weight_percent: Decimal = Field(ge=0, le=100, description="Share of the SBA component")


class ExamPapers(Contract):
    """The end-of-year examination component: how many papers, marks each."""

    papers: int = Field(gt=0)
    marks_each: int = Field(gt=0)
    hours_each: Decimal = Field(gt=0)


class CognitiveSpread(Contract):
    """Required share of marks per cognitive level, in percent, and the tolerance allowed."""

    levels: dict[str, Decimal] = Field(min_length=1)
    tolerance_points: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _levels_sum_to_100(self) -> Self:
        total = sum(self.levels.values(), Decimal(0))
        if total != 100:
            raise ValueError(f"cognitive levels must sum to 100, got {total}")
        return self


class ModerationRule(Contract):
    """How the Moderator samples scripts for a blind re-mark, and when it widens to all of them."""

    sample_percent: Decimal = Field(gt=0, le=100)
    min_scripts: int = Field(ge=1)
    spread: Literal["top-middle-bottom", "random"]
    widen_above_gap_percent: Decimal = Field(
        ge=0, le=100, description="Re-mark every script when the disagreement rate exceeds this"
    )


class SubjectPack(Contract):
    """Everything about one subject and grade that depends on the curriculum, not on Mothusi."""

    pack_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    grade: int = Field(ge=8, le=12)
    sba_percent: int = Field(ge=0, le=100)
    exam_percent: int = Field(ge=0, le=100)
    poa: tuple[PoATask, ...] = Field(min_length=1)
    exam: ExamPapers
    cognitive: CognitiveSpread | None = Field(
        default=None,
        description="None when the curriculum publishes no grid: the check is skipped and logged",
    )
    moderation: ModerationRule
    conventions: tuple[str, ...] = ()
    sources: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _components_sum_to_100(self) -> Self:
        if self.sba_percent + self.exam_percent != 100:
            raise ValueError("sba_percent and exam_percent must sum to 100")
        return self


class AtpWeek(Contract):
    """One week of the annual teaching plan still to come: where a catch-up topic may be placed."""

    week: int = Field(ge=1, le=52)
    topic: str = Field(min_length=1)
    source: str = Field(min_length=1, description="The ATP document and row this week comes from")


class PassageKind(StrEnum):
    """Kinds of curriculum sources (knowledge.md §6.1)."""

    CAPS = "caps"
    ATP = "atp"
    POA = "poa"
    CIRCULAR = "circular"
    POLICY = "policy"
    PACK = "pack"


class Passage(Contract):
    """One source passage with its exact PDF page or pack locator."""

    passage_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    kind: PassageKind
    subject: str | None = None
    grade: int | None = Field(default=None, ge=1, le=12)
    page: int | None = Field(default=None, ge=1)
    locator: str = Field(min_length=1)
    text: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)

    @property
    def citation(self) -> str:
        return f"{self.source}, {self.locator}"


class CatchUpItem(Contract):
    """One re-teaching activity, placed in a week the ATP has left."""

    week: int = Field(ge=1, le=52)
    topic: str = Field(min_length=1)
    activity: str = Field(min_length=1)
    atp_source: str = Field(min_length=1)
    citation: str = ""


class LearnerNote(Contract):
    """The Writer's three-part note for one learner, plus a self-assessment line (idea.md)."""

    pseudonym: str = Field(min_length=1)
    outcome: str = Field(min_length=1, description="What the task tested, in the learner's terms")
    mark_with_quote: str = Field(
        min_length=1, description="The mark, with the learner's own work quoted as the evidence"
    )
    next_task: str = Field(min_length=1)
    self_assessment_prompt: str = Field(min_length=1)


class LedgerLine(Contract):
    """One line of a learner's ledger, statement or reconciliation, as marked (Accounting)."""

    line_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    amount: Decimal | None = None
    foreign: bool = Field(description="An entry that does not belong on this statement")
    marks_scored: int = Field(ge=0, description="Marks the learner earned on this line")


# --------------------------------------------------------------------------- the assessment


class MemoItem(Contract):
    """One question's marking guideline: the answer, its marks, and its cognitive level."""

    question_id: str = Field(min_length=1)
    question: str = Field(default="", description="The question as printed on the paper")
    max_marks: int = Field(gt=0)
    answer: str = Field(min_length=1)
    marking_notes: str = ""
    level: str = Field(min_length=1)


class Memo(Contract):
    """The marking guideline for one assessment. The ground truth the Marker marks against."""

    assessment_id: str = Field(min_length=1)
    total_marks: int = Field(gt=0)
    items: tuple[MemoItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _items_add_up_and_are_unique(self) -> Self:
        ids = [item.question_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("memo question_ids must be unique")
        total = sum(item.max_marks for item in self.items)
        if total != self.total_marks:
            raise ValueError(f"memo items add up to {total}, not total_marks {self.total_marks}")
        return self


class TranscribedLine(Contract):
    """One line read from an answer crop, with the reader's uncertainty flag."""

    text: str
    uncertain: bool


class Transcription(Contract):
    """The page reader's output, retaining the order of the handwritten lines."""

    lines: tuple[TranscribedLine, ...]


class PageImage(Contract):
    """A deskewed page whose name band has already been blanked (intake.md §11.6)."""

    assessment_id: str = Field(min_length=1)
    learner_pseudonym: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    source_key: str = Field(min_length=1)


class AnswerCrop(Contract):
    """One answer box cropped from a page, for the page reader alone."""

    learner_pseudonym: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    png: bytes


class Answer(Contract):
    """What one learner wrote for one question, as transcribed."""

    question_id: str = Field(min_length=1)
    text: str
    uncertain_lines: tuple[int, ...] = ()
    unreadable: bool = False

    @model_validator(mode="before")
    @classmethod
    def _shift_lines_past_stripped_ends(cls, data: Any) -> Any:
        """Keep uncertain_lines on the right lines when Contract strips the text's ends.

        `str_strip_whitespace` drops leading and trailing blank lines from `text`, which would move
        every later line index. Re-base the indices onto the stripped text first; an index that
        pointed at a dropped blank line is dropped with it (there is nothing left to read there).
        """
        if not isinstance(data, dict):
            return data
        raw = cast(dict[str, Any], data)
        text, lines = raw.get("text"), raw.get("uncertain_lines")
        if not isinstance(text, str) or not lines:
            return raw
        parts = text.split("\n")
        lead = 0
        while lead < len(parts) and not parts[lead].strip():
            lead += 1
        trail = len(parts)
        while trail > lead and not parts[trail - 1].strip():
            trail -= 1
        if lead == len(parts) or (lead == 0 and trail == len(parts)):
            return raw  # all blank (one line once stripped) or nothing stripped: indices stand
        indices = cast(Sequence[Any], lines)
        kept = tuple(i - lead for i in indices if isinstance(i, int) and lead <= i < trail)
        return {**raw, "uncertain_lines": kept}

    @model_validator(mode="after")
    def _reading_flags_are_consistent(self) -> Self:
        if self.unreadable and (self.text or self.uncertain_lines):
            raise ValueError("an unreadable answer must have empty text and no uncertain_lines")
        if tuple(sorted(set(self.uncertain_lines))) != self.uncertain_lines:
            raise ValueError("uncertain_lines must be sorted and unique")
        line_count = len(self.text.split("\n"))
        if any(index < 0 or index >= line_count for index in self.uncertain_lines):
            raise ValueError("uncertain_lines must be in range of text.split('\\n')")
        return self


class ReadingAnswer(Contract):
    """The teacher's confirmation or correction of a flagged reading (intake.md §11.6)."""

    kind: Literal["reading"]
    script_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    line_no: int | None = Field(ge=0)
    action: Literal["confirm", "correct"]
    text: str = ""

    @model_validator(mode="after")
    def _unreadable_requires_correction(self) -> Self:
        if self.line_no is None and self.action != "correct":
            raise ValueError("line_no None requires action 'correct' for an unreadable answer")
        return self


class Script(Contract):
    """One learner's answers. Pseudonymised at intake: the Marker and Moderator never see a name."""

    script_id: str = Field(min_length=1)
    learner_pseudonym: str = Field(min_length=1)
    answers: tuple[Answer, ...]

    @model_validator(mode="after")
    def _one_answer_per_question(self) -> Self:
        ids = [answer.question_id for answer in self.answers]
        if len(set(ids)) != len(ids):
            raise ValueError("a script holds one answer per question_id")
        return self


class Mark(Contract):
    """One mark decision. A mark above zero must quote the part of the answer that earned it."""

    question_id: str = Field(min_length=1)
    awarded: int = Field(ge=0)
    max_marks: int = Field(gt=0)
    evidence_quote: str = ""
    memo_line: str = Field(min_length=1, description="The memo item or note applied, verbatim")
    principle: str = Field(min_length=1, description="The marking principle, e.g. 'CA marking'")

    @model_validator(mode="after")
    def _evidence_for_any_credit(self) -> Self:
        if self.awarded > self.max_marks:
            raise ValueError(f"awarded {self.awarded} exceeds max_marks {self.max_marks}")
        if self.awarded > 0 and not self.evidence_quote:
            raise ValueError("a mark above zero needs an evidence_quote")
        return self


class MarkedScript(Contract):
    """A script after marking, by whichever agent or person marked it."""

    script_id: str = Field(min_length=1)
    marker: str = Field(min_length=1)
    marks: tuple[Mark, ...] = Field(min_length=1)
    total: int = Field(ge=0)


class Disagreement(Contract):
    """Where the blind re-mark differs from the first mark on one question."""

    script_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    first: int = Field(ge=0)
    second: int = Field(ge=0)
    reason: str = ""

    @property
    def gap(self) -> int:
        return abs(self.first - self.second)


class CheckResult(Contract):
    """The outcome of one deterministic check. `about` names what was checked (a script, a memo)."""

    check: str = Field(min_length=1)
    about: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class ConvertedMark(Contract):
    """A raw mark carried onto the mark sheet: scaled to the task's weight, then to the year."""

    script_id: str = Field(min_length=1)
    raw: int = Field(ge=0)
    out_of: int = Field(gt=0)
    sba_points: Decimal = Field(ge=0, description="Out of the task's weight_percent")
    year_points: Decimal = Field(ge=0, description="Out of weight_percent x sba_percent / 100")


class ItemStat(Contract):
    """Item analysis for one question across the class."""

    question_id: str = Field(min_length=1)
    max_marks: int = Field(gt=0)
    scripts: int = Field(ge=1)
    mean_awarded: Decimal = Field(ge=0)
    facility_percent: Decimal = Field(ge=0, le=100)
    flag: Literal["re-teach", "review-item"] | None = None


class AssessmentState(StrEnum):
    """The lifecycle of one assessment through Mothusi. Transitions: marking-pipeline.md §5."""

    DRAFT = "draft"
    READING_CHECK = "reading_check"
    MEMO_CHECK = "memo_check"
    MARKING = "marking"
    MODERATING = "moderating"
    RE_MARKING = "re_marking"
    ANALYSING = "analysing"
    WRITING = "writing"
    REVIEWING = "reviewing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DELIVERED = "delivered"
    REJECTED = "rejected"


class Assessment(Contract):
    """One class's formal task, from the teacher's goal to the approved bundle."""

    assessment_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    grade: int = Field(ge=8, le=12)
    term: int = Field(ge=1, le=4)
    title: str = Field(min_length=1)
    poa_task_name: str = Field(min_length=1, description="Must match a PoATask.name in the pack")
    memo: Memo
    scripts: tuple[Script, ...]
    state: AssessmentState = AssessmentState.DRAFT

    @model_validator(mode="after")
    def _memo_is_this_assessments(self) -> Self:
        if self.memo.assessment_id != self.assessment_id:
            raise ValueError("memo.assessment_id must equal assessment_id")
        return self
