"""The Setter: sits the test itself and compares its answers with the memo, before any marking.

Design: docs/architecture/design/marking-pipeline.md §4 steps 4-7; the `setter` contract. The model answers the
paper; code decides agreement with `equivalent` (rules-engine.md §3 numeric.py). An item the code
cannot compare is reported as such, never silently passed. A non-empty issues list stops the run at
`memo_check` until the teacher confirms or amends the memo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    AtpWeek,
    Brief,
    Contract,
    Memo,
    MemoItem,
    Passage,
    Result,
    ResultStatus,
)
from mothusi_llm import Provider, ProviderError
from mothusi_rules import equivalent

SETTER_SYSTEM = """You are Mothusi's Setter: you sit this test as a strong Grade 10 learner would,
before anyone marks with its memo. For every question give your final answer in the memo's own
notation (an expression, a value, or a solution set such as "x = 3 or x = -2") and show your
working briefly.
Do not look for the memo's answer; work the question. Reply with JSON only, matching the schema."""


class SetterAnswer(Contract):
    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    working: str = ""


class SetterReply(Contract):
    answers: tuple[SetterAnswer, ...] = Field(min_length=1)


class MemoIssue(Contract):
    """One memo item the Setter's answer did not reproduce, for the teacher to confirm or amend."""

    question_id: str
    memo_answer: str
    setter_answer: str
    working: str
    kind: Literal["disagrees", "not_comparable", "unanswered"]


def setter_prompts(memo: Memo, conventions: tuple[str, ...]) -> tuple[str, str]:
    lines: list[str] = []
    if conventions:
        lines.append("Subject conventions:")
        lines.extend(f"- {c}" for c in conventions)
        lines.append("")
    lines.append(f"The paper ({memo.total_marks} marks). Answer every question:")
    for item in memo.items:
        lines.append(f"[{item.question_id}] ({item.max_marks} marks) {item.question}".rstrip())
    lines.append("")
    lines.append(
        'Reply as JSON: {"answers": [{"question_id": str, "answer": str, "working": str}, ...]}'
    )
    return SETTER_SYSTEM, "\n".join(lines)


def _text(answer: str) -> str:
    return " ".join(answer.split()).casefold()


def blocking(issues: tuple[MemoIssue, ...]) -> tuple[MemoIssue, ...]:
    """The issues that stop the run at memo_check: a wrong or missing memo answer."""
    return tuple(issue for issue in issues if issue.kind != "not_comparable")


def compare_with_memo(memo: Memo, reply: SetterReply) -> tuple[MemoIssue, ...]:
    """Code decides: equivalent is fine; not equivalent disagrees; unparsable is text-matched.

    Only "disagrees" and "unanswered" stop the run (blocking()); "not_comparable" is reported to
    the teacher as unverified, because code could not tell either way.
    """
    answers = {a.question_id: a for a in reply.answers}
    issues: list[MemoIssue] = []
    for item in memo.items:
        given = answers.get(item.question_id)
        if given is None:
            issues.append(
                MemoIssue(
                    question_id=item.question_id,
                    memo_answer=item.answer,
                    setter_answer="",
                    working="",
                    kind="unanswered",
                )
            )
            continue
        try:
            same = equivalent(item.answer, given.answer)
        except ValueError:
            if _text(item.answer) == _text(given.answer):
                continue
            issues.append(
                MemoIssue(
                    question_id=item.question_id,
                    memo_answer=item.answer,
                    setter_answer=given.answer,
                    working=given.working,
                    kind="not_comparable",
                )
            )
            continue
        if not same:
            issues.append(
                MemoIssue(
                    question_id=item.question_id,
                    memo_answer=item.answer,
                    setter_answer=given.answer,
                    working=given.working,
                    kind="disagrees",
                )
            )
    return tuple(issues)


async def sit_test(brief: Brief, provider: Provider) -> Result:
    """One Setter pass. outputs["issues"] block the run; outputs["unverified"] go to the teacher."""
    if brief.agent is not AgentRole.SETTER:
        return Result(
            brief_id=brief.brief_id,
            agent=AgentRole.SETTER,
            status=ResultStatus.BLOCKED,
            reason=f"brief is for {brief.agent.value}, not the setter",
        )
    try:
        memo = Memo.model_validate(brief.inputs["memo"])
        conventions = tuple(str(c) for c in brief.inputs.get("pack_conventions", []))
    except (KeyError, TypeError, ValidationError) as error:
        return Result(
            brief_id=brief.brief_id,
            agent=AgentRole.SETTER,
            status=ResultStatus.BLOCKED,
            reason=f"brief inputs do not fit the setter contract: {error}",
        )
    system, user = setter_prompts(memo, conventions)
    try:
        reply, usage = await provider.complete(
            system, user, SetterReply, budget_tokens=brief.budget_tokens
        )
    except ProviderError as error:
        return Result(
            brief_id=brief.brief_id,
            agent=AgentRole.SETTER,
            status=ResultStatus.RETRY if error.retryable else ResultStatus.BLOCKED,
            reason=f"{provider.name}: {error}",
        )
    found = compare_with_memo(memo, reply)
    issues = blocking(found)
    unverified = tuple(issue for issue in found if issue.kind == "not_comparable")
    return Result(
        brief_id=brief.brief_id,
        agent=AgentRole.SETTER,
        status=ResultStatus.DONE,
        outputs={
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "unverified": [issue.model_dump(mode="json") for issue in unverified],
            "answers": [a.model_dump(mode="json") for a in reply.answers],
        },
        evidence=tuple(f"{a.question_id}: {a.working}" for a in reply.answers if a.working),
        cost_zar=usage.cost_zar,
    )


# ------------------------------------------------------------------ memo from a PDF or Word file
# intake.md §12: the Setter copies a typed memo's items into rows the teacher checks and edits at
# set-task. It copies, it does not solve; the draft is a Memo only once the teacher confirms it.

MEMO_DRAFT_SYSTEM = """You are Mothusi's Setter, copying a teacher's typed memo (marking guideline)
into rows so the teacher can check them. Copy, do not solve and do not improve: every field comes
from the memo text. One row per marked question or sub-question, in the memo's order, with the
question number exactly as printed (for example "1.1" or "2.3(b)"). max_marks is the marks the memo
gives that question. answer is the memo's answer and working as written; marking_notes is where each
mark is earned, if the memo says. level is one of the allowed levels only when the memo states it;
otherwise "". total_marks is the paper's total as printed on the memo, or null if it does not say.
Reply with JSON only, matching the schema."""

MEMO_DRAFT_LIMIT = 60_000  # characters of memo text one drafting call takes (a ~15-page memo)


class DraftItem(Contract):
    """One drafted memo row; empty or zero fields are the teacher's to fill (intake.md §12.2)."""

    question_id: str = Field(min_length=1)
    question: str = ""
    max_marks: int = 0
    answer: str = ""
    marking_notes: str = ""
    level: str = ""


class MemoDraftReply(Contract):
    total_marks: int | None = None
    items: tuple[DraftItem, ...] = Field(min_length=1)


class MemoDraftError(Exception):
    """The draft could not be made; the message is a sentence for the teacher."""


@dataclass(frozen=True)
class MemoDraftResult:
    total_marks: int | None
    items: tuple[DraftItem, ...]
    problems: tuple[str, ...]
    cost_zar: Decimal


def memo_too_long(characters: int) -> str:
    return (
        f"the memo's text is {characters:,} characters, over the {MEMO_DRAFT_LIMIT:,} one draft "
        "can read without dropping questions; upload it in parts, or as TOML"
    )


def memo_draft_prompts(text: str, levels: Sequence[str]) -> tuple[str, str]:
    allowed = ", ".join(levels) if levels else "any level the memo names"
    user = "\n".join(
        [
            f"Allowed levels: {allowed}.",
            "",
            "The memo:",
            text,
            "",
            'Reply as JSON: {"total_marks": int | null, "items": [{"question_id": str, '
            '"question": str, "max_marks": int, "answer": str, "marking_notes": str, '
            '"level": str}, ...]}',
        ]
    )
    return MEMO_DRAFT_SYSTEM, user


def draft_problems(
    total_marks: int | None, items: Sequence[DraftItem], levels: Sequence[str]
) -> tuple[tuple[DraftItem, ...], tuple[str, ...]]:
    """Blank any level outside the pack's list, and name everything the teacher must fix."""
    problems: list[str] = []
    fixed: list[DraftItem] = []
    seen: set[str] = set()
    for item in items:
        if item.question_id in seen:
            problems.append(f"{item.question_id} appears twice; keep one row")
        seen.add(item.question_id)
        if item.max_marks <= 0:
            problems.append(f"{item.question_id}: no marks found; give its marks")
        if not item.answer.strip():
            problems.append(f"{item.question_id}: no answer found; type the memo's answer")
        if levels and item.level not in levels:
            if item.level:
                problems.append(f"{item.question_id}: level {item.level!r} is not the pack's")
            else:
                problems.append(f"{item.question_id}: the memo gives no level; choose one")
            item = item.model_copy(update={"level": ""})
        elif not item.level:
            problems.append(f"{item.question_id}: the memo gives no level; type one")
        fixed.append(item)
    total = sum(max(0, i.max_marks) for i in fixed)
    if total_marks is None:
        problems.append(f"the memo's total was not found; the rows add up to {total}")
    elif total != total_marks:
        problems.append(f"memo items add up to {total}, not the {total_marks} the memo states")
    return tuple(fixed), tuple(problems)


async def draft_memo(text: str, provider: Provider, levels: Sequence[str]) -> MemoDraftResult:
    """One drafting call; a reply that fails MemoDraftReply is retried once, naming the error.

    A memo over MEMO_DRAFT_LIMIT is refused, never cut: a cut memo would lose questions silently
    (the endpoint refuses it first, with the same sentence)."""
    if len(text) > MEMO_DRAFT_LIMIT:
        raise MemoDraftError(memo_too_long(len(text)))
    system, user = memo_draft_prompts(text, levels)
    cost = Decimal(0)
    last = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else f"{user}\n\nYour last reply was rejected: {last}"
        try:
            reply, usage = await provider.complete(system, prompt, MemoDraftReply)
        except ProviderError as error:
            if not error.retryable:
                raise MemoDraftError(f"the memo could not be read: {error}") from error
            last = str(error)
            continue
        cost += usage.cost_zar
        items, problems = draft_problems(reply.total_marks, reply.items, levels)
        return MemoDraftResult(reply.total_marks, items, problems, cost)
    raise MemoDraftError(
        "the memo could not be read into rows after two tries; try again, or upload the TOML"
    )


# ------------------------------------------------------------ a practice test from an ATP week
# learner-section.md §4.1: the Setter sets a short test and memo for one ATP week, from the
# retrieved CAPS/ATP passages; code checks it (Memo, cognitive grid, citations) and the Setter then
# sits it blind like any memo. Assessments only: no notes, lesson plans or explanations (§8).

PRACTICE_SYSTEM = """You are Mothusi's Setter, setting a short practice test and its memo for one
week of the annual teaching plan (ATP).

Rules you never break:
1. Every question assesses the listed ATP week's topic, at the listed grade, and nothing else.
2. Each question's citation is copied exactly from the square brackets of one listed passage.
   Never invent a citation.
3. The marks add up to exactly the total asked for.
4. level is one of the allowed levels; spread the marks close to the level targets.
5. answer is the memo's full worked answer; marking_notes says where each mark is earned.
6. Write questions and a memo only: no notes, explanations, lesson plans or hints.
Reply with JSON only, matching the requested schema."""


class PracticeItem(Contract):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    max_marks: int = Field(gt=0)
    answer: str = Field(min_length=1)
    marking_notes: str = ""
    level: str = Field(min_length=1)
    citation: str = Field(min_length=1)


class PracticeReply(Contract):
    items: tuple[PracticeItem, ...] = Field(min_length=1)


def practice_prompts(
    week: AtpWeek,
    passages: Sequence[Passage],
    targets: Mapping[str, Decimal],
    marks: int,
    subject: str,
    grade: int,
    previous_failure: str | None = None,
) -> tuple[str, str]:
    lines = [
        f"Grade {grade} {subject}. Total marks: {marks}.",
        f"ATP week {week.week}: {week.topic} [{week.source}]",
        "",
        "Allowed levels and their target share of the marks:",
        *(f"- {level}: {share}%" for level, share in targets.items()),
        "",
        "Curriculum passages ([citation] text):",
        *(f"[{p.citation}] {' '.join(p.text.split())}" for p in passages),
    ]
    if previous_failure:
        lines.extend(
            ("", "Your previous test failed these checks; fix exactly these:", previous_failure)
        )
    lines.extend(
        (
            "",
            'Reply as JSON: {"items": [{"question_id", "question", "max_marks", "answer", '
            '"marking_notes", "level", "citation"}, ...]}',
        )
    )
    return PRACTICE_SYSTEM, "\n".join(lines)


async def set_practice_test(brief: Brief, provider: Provider) -> Result:
    """One setting pass: outputs["memo"] and outputs["citations"], or RETRY naming what failed."""
    role = AgentRole.SETTER
    try:
        week = AtpWeek.model_validate(brief.inputs["atp_week"])
        passages = [Passage.model_validate(p) for p in brief.inputs.get("passages", [])]
        targets = {str(k): Decimal(str(v)) for k, v in dict(brief.inputs["targets"]).items()}
        marks = int(brief.inputs["marks"])
        subject, grade = str(brief.inputs["subject"]), int(brief.inputs["grade"])
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED,
            reason=f"brief inputs do not fit the practice-test contract: {error}",
        )
    if not passages:  # §8: every question cites a retrieved passage; none means no test
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED,
            reason="no curriculum passages were retrieved for this ATP week",
        )
    previous = brief.inputs.get("previous_failure")
    system, user = practice_prompts(
        week, passages, targets, marks, subject, grade, str(previous) if previous else None
    )
    try:
        reply, usage = await provider.complete(system, user, PracticeReply)
    except ProviderError as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY if error.retryable else ResultStatus.BLOCKED,
            reason=f"{provider.name}: {error}",
        )
    allowed = {p.citation for p in passages}
    failures = [
        f"{i.question_id}: citation not a listed passage"
        for i in reply.items
        if i.citation not in allowed
    ]
    failures.extend(
        f"{i.question_id}: level {i.level!r} is not allowed"
        for i in reply.items
        if targets and i.level not in targets
    )
    try:
        memo = Memo(
            assessment_id=brief.assessment_id,
            total_marks=marks,
            items=tuple(
                MemoItem(
                    question_id=i.question_id,
                    question=i.question,
                    max_marks=i.max_marks,
                    answer=i.answer,
                    marking_notes=i.marking_notes,
                    level=i.level,
                )
                for i in reply.items
            ),
        )
    except ValidationError as error:
        failures.append(f"memo: {error.errors()[0]['msg']}")
        memo = None
    if failures or memo is None:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY,
            reason="; ".join(failures),
            cost_zar=usage.cost_zar,
        )
    return Result(
        brief_id=brief.brief_id,
        agent=role,
        status=ResultStatus.DONE,
        outputs={
            "memo": memo.model_dump(mode="json"),
            "citations": sorted({i.citation for i in reply.items}),
        },
        cost_zar=usage.cost_zar,
    )
