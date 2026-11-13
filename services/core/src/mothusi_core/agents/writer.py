"""The Writer: drafts the teacher's paperwork around numbers the rules have already computed.

Design: docs/architecture/design/marking-pipeline.md §4 steps 23-24 and §6.1; the `writer` contract.
The model
writes one three-part note per learner and the worked corrections for the three weakest questions.
Every number comes from code: the class sheet wraps `class_feedback_md`, the moderation report is
`moderation_report_md` verbatim, the catch-up plan is a table of the checked plan, and the memo
answer beside each correction is copied from the memo. No learner name reaches this agent; the
Executor joins names to notes in the bundle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    Brief,
    CatchUpItem,
    Contract,
    Disagreement,
    ItemStat,
    LearnerNote,
    MarkedScript,
    Memo,
    Result,
    ResultStatus,
    Script,
)
from mothusi_core.agents.analyst import Misconception
from mothusi_core.agents.loop import ask, refuse, wrong_role
from mothusi_llm import Provider, Usage
from mothusi_rules import class_feedback_md, moderation_report_md

WEAKEST = 3

NOTE_SYSTEM = """You are Mothusi's Writer: you draft one learner's feedback note for their teacher
to approve. The note has exactly four parts:
- outcome: what this test assessed, in words a Grade 10 learner understands.
- mark_with_quote: one mark this learner was given (as awarded/max for one question, or the total
  awarded/max), with the learner's own work quoted in double quotes, copied exactly from their
  answers below.
- next_task: one concrete thing to practise next, aimed at a question where marks were lost.
- self_assessment_prompt: one question the learner asks themselves about their own working.

Rules you never break:
1. Quote only the learner's own words, character for character. Never tidy them.
2. State only marks that appear in the marking below, and ATP weeks only from the catch-up plan
   below; if there is no plan, name no week.
3. Describe the work, never the learner: no labels (weak, lazy, slow, careless), no comparisons
   with other learners, no praise without a quote.
4. The learner is identified only by the pseudonym given. Never guess or use a name.
5. Reply with JSON only, matching the requested schema. No prose outside the JSON."""

CORRECTIONS_SYSTEM = """You are Mothusi's Writer: for each question listed, the weakest in the
class,
write the common error and a short worked correction a teacher can put on the board.

Rules you never break:
1. Write one correction for every question listed, and only for those.
2. The correction works towards the memo answer given. Do not state a different final answer.
3. Describe the error, never learners. No names, no counts of your own.
4. Reply with JSON only, matching the requested schema. No prose outside the JSON."""


class NoteReply(Contract):
    """What the model writes for one learner. The pseudonym is set by code."""

    outcome: str = Field(min_length=1)
    mark_with_quote: str = Field(min_length=1)
    next_task: str = Field(min_length=1)
    self_assessment_prompt: str = Field(min_length=1)


class Correction(Contract):
    question_id: str = Field(min_length=1)
    common_error: str = Field(min_length=1)
    worked_correction: str = Field(min_length=1)


class CorrectionsReply(Contract):
    corrections: tuple[Correction, ...] = Field(min_length=1)


class ModerationReportData(Contract):
    """What `moderation_report_md` needs; the agreement rate is computed by the renderer."""

    title: str = Field(min_length=1)
    sample: tuple[str, ...]
    disagreements: tuple[Disagreement, ...] = ()
    resolutions: dict[str, str] = Field(default_factory=dict)
    questions_compared: int = Field(gt=0)
    moderator: str = Field(min_length=1)


def weakest(item_stats: Sequence[ItemStat], n: int = WEAKEST) -> tuple[ItemStat, ...]:
    """The n weakest items, in class_feedback_md's order: facility, then question id."""
    return tuple(sorted(item_stats, key=lambda s: (s.facility_percent, s.question_id))[:n])


def _sent_back(teacher_note: str | None) -> list[str]:
    if not teacher_note:
        return []
    return ["", "The teacher sent the last draft back with this note; act on it:", teacher_note]


def note_prompts(
    memo: Memo,
    script: Script,
    marked: MarkedScript,
    misconceptions: Sequence[Misconception],
    *,
    catch_up_plan: Sequence[CatchUpItem] = (),
    reason: str | None = None,
    teacher_note: str | None = None,
    previous_failure: str | None = None,
) -> tuple[str, str]:
    """The (system, user) pair for one learner's note. Deterministic: replay keys on it.

    The checked catch-up plan is included so a note (or a teacher's request) can name the ATP
    week of the class's re-teaching from the plan, never from the model's own idea of the ATP.
    """
    items = {i.question_id: i for i in memo.items}
    answers = {a.question_id: a.text for a in script.answers}
    total = sum(m.max_marks for m in marked.marks)
    lines = [
        f"Learner: {script.learner_pseudonym} (script {script.script_id})",
        f"Total: {marked.total}/{total}",
        "",
        "Marking (question: awarded/max, principle):",
    ]
    for m in marked.marks:
        lines.append(f"[{m.question_id}] {m.awarded}/{m.max_marks}, {m.principle}")
        lines.append(f"  question: {items[m.question_id].question}")
    lines.extend(("", "The learner's answers (verbatim; quote from these exactly):"))
    for m in marked.marks:
        text = answers.get(m.question_id)
        lines.append(f"[{m.question_id}]" + (" (no answer)" if text is None else ""))
        if text is not None:
            lines.append(text)
    lost = {m.question_id for m in marked.marks if m.awarded < m.max_marks}
    relevant = [c for c in misconceptions if c.question_id in lost]
    if relevant:
        lines.extend(("", "What the class got wrong on the questions this learner lost marks on:"))
        lines.extend(f"[{c.question_id}] {c.pattern}" for c in relevant)
    if catch_up_plan:
        lines.extend(("", "The class's checked catch-up plan (name a week only from here):"))
        lines.extend(
            f"ATP week {i.week}: {i.topic}: {i.activity} ({i.atp_source})"
            for i in sorted(catch_up_plan, key=lambda i: (i.week, i.topic))
        )
    if previous_failure:
        lines.extend(("", "The last attempt failed; fix what applies to this note:"))
        lines.append(previous_failure)
    if reason:
        lines.extend(("", "The Reviewer rejected your last note for this learner because:", reason))
    lines.extend(_sent_back(teacher_note))
    lines.extend(
        (
            "",
            'Reply as JSON: {"outcome", "mark_with_quote", "next_task", "self_assessment_prompt"}',
        )
    )
    return NOTE_SYSTEM, "\n".join(lines)


def corrections_prompts(
    memo: Memo,
    weakest_items: Sequence[ItemStat],
    misconceptions: Sequence[Misconception],
    *,
    teacher_note: str | None = None,
    previous_failure: str | None = None,
) -> tuple[str, str]:
    """The (system, user) pair for the worked corrections. Deterministic: replay keys on it."""
    items = {i.question_id: i for i in memo.items}
    patterns = {m.question_id: m for m in misconceptions}
    lines = ["The weakest questions in the class:"]
    for stat in weakest_items:
        item = items[stat.question_id]
        lines.append(f"[{item.question_id}] (facility {stat.facility_percent}%) {item.question}")
        lines.append(f"  memo answer: {item.answer}")
        if item.marking_notes:
            lines.append(f"  marking notes: {item.marking_notes}")
        if stat.question_id in patterns:
            lines.append(f"  misconception: {patterns[stat.question_id].pattern}")
    lines.extend(_sent_back(teacher_note))
    if previous_failure:
        lines.extend(("", "Your previous answer failed these checks; fix exactly these:"))
        lines.append(previous_failure)
    lines.extend(
        (
            "",
            'Reply as JSON: {"corrections": [{"question_id", "common_error", '
            '"worked_correction"}, ...]}',
        )
    )
    return CORRECTIONS_SYSTEM, "\n".join(lines)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


NO_PASSAGE = "\u2013"  # an en dash: the item cites no passage (knowledge.md §6.7)


def catch_up_plan_md(plan: Sequence[CatchUpItem]) -> str:
    """The checked plan as a table, in week order. The weeks and sources are the ATP's."""
    lines = ["# Catch-up plan", ""]
    if not plan:
        lines.append("No item needs re-teaching: no catch-up work is planned.")
        return "\n".join(lines) + "\n"
    lines.extend(
        (
            "| ATP week | topic | activity | ATP source | Source passage |",
            "| --- | --- | --- | --- | --- |",
        )
    )
    for item in sorted(plan, key=lambda i: (i.week, i.topic)):
        lines.append(
            f"| {item.week} | {_cell(item.topic)} | {_cell(item.activity)} | "
            f"{_cell(item.atp_source)} | {_cell(item.citation) or NO_PASSAGE} |"
        )
    return "\n".join(lines) + "\n"


def class_sheet_md(
    memo: Memo,
    item_stats: Sequence[ItemStat],
    misconceptions: Sequence[Misconception],
    corrections: Sequence[Correction],
) -> str:
    """class_feedback_md verbatim, then the Analyst's patterns, then the worked corrections."""
    items = {i.question_id: i for i in memo.items}
    facility = {s.question_id: s.facility_percent for s in item_stats}
    lines = [class_feedback_md(item_stats, WEAKEST).rstrip("\n")]
    if misconceptions:
        lines.extend(("", "## What the scripts show", ""))
        for m in misconceptions:
            quotes = "; ".join(f'"{_cell(q)}"' for q in m.evidence_quotes)
            noun = "script" if m.scripts == 1 else "scripts"
            lines.append(f"- **{m.question_id}**: {m.pattern} ({m.scripts} {noun}: {quotes})")
    lines.extend(("", "## Worked corrections"))
    for c in corrections:
        lines.extend(
            (
                "",
                f"### Question {c.question_id} (facility {facility[c.question_id]}%)",
                "",
                f"**Memo answer:** {items[c.question_id].answer}",
                "",
                f"**Common error:** {c.common_error}",
                "",
                f"**Worked correction:** {c.worked_correction}",
            )
        )
    return "\n".join(lines) + "\n"


class _Inputs(Contract):
    memo: Memo
    marked: tuple[MarkedScript, ...] = Field(min_length=1)
    scripts: tuple[Script, ...] = Field(min_length=1)
    item_stats: tuple[ItemStat, ...] = Field(min_length=1)
    misconceptions: tuple[Misconception, ...] = ()
    catch_up_plan: tuple[CatchUpItem, ...] = ()
    moderation_report_data: ModerationReportData
    notes: tuple[LearnerNote, ...] = ()
    rewrite: dict[str, str] = Field(default_factory=dict)
    teacher_note: str | None = None
    previous_failure: str | None = None
    class_sheet_reason: str | None = None


def _check_corrections(reply: CorrectionsReply, wanted: Sequence[ItemStat]) -> list[str]:
    want = [s.question_id for s in wanted]
    got = [c.question_id for c in reply.corrections]
    failures = [f"{qid}: not one of the weakest questions {want}" for qid in got if qid not in want]
    failures.extend(f"{qid}: no worked correction" for qid in want if qid not in got)
    failures.extend(f"{qid}: more than one correction" for qid in set(got) if got.count(qid) > 1)
    return failures


def _to_draft(
    inputs: _Inputs,
) -> tuple[list[tuple[Script, MarkedScript, str | None]], dict[str, LearnerNote]] | str:
    """The notes to draft, with each one's rejection reason, and those carried over unchanged."""
    scripts = {s.script_id: s for s in inputs.scripts}
    missing = [m.script_id for m in inputs.marked if m.script_id not in scripts]
    if missing:
        return f"no script for marked {missing}"
    previous = {n.pseudonym: n for n in inputs.notes}
    if inputs.rewrite and not inputs.notes:
        return "rewrite needs the previous notes"
    unknown = [p for p in inputs.rewrite if p not in previous]
    if unknown:
        return f"rewrite names notes not drafted: {unknown}"
    if inputs.notes and not inputs.teacher_note:
        absent = [
            m.script_id
            for m in inputs.marked
            if scripts[m.script_id].learner_pseudonym not in previous
        ]
        if absent:
            return f"previous notes miss {absent}"
    draft: list[tuple[Script, MarkedScript, str | None]] = []
    kept: dict[str, LearnerNote] = {}
    for m in inputs.marked:
        script = scripts[m.script_id]
        pseudonym = script.learner_pseudonym
        if inputs.notes and not inputs.teacher_note and pseudonym not in inputs.rewrite:
            kept[pseudonym] = previous[pseudonym]
        else:
            draft.append((script, m, inputs.rewrite.get(pseudonym)))
    return draft, kept


async def write(brief: Brief, provider: Provider) -> Result:
    """One Writer pass: the notes it must draft, the corrections, and the rendered documents."""
    role = AgentRole.WRITER
    if (refused := wrong_role(brief, role)) is not None:
        return refused
    try:
        inputs = _Inputs.model_validate(brief.inputs)
    except ValidationError as error:
        return refuse(brief, role, f"brief inputs do not fit the writer contract: {error}")
    planned = _to_draft(inputs)
    if isinstance(planned, str):
        return refuse(brief, role, planned)
    draft, kept = planned
    weak = weakest(inputs.item_stats)
    calls = [
        ask(
            provider,
            brief,
            role,
            note_prompts(
                inputs.memo,
                script,
                marked,
                inputs.misconceptions,
                catch_up_plan=inputs.catch_up_plan,
                reason=reason,
                teacher_note=inputs.teacher_note,
                previous_failure=inputs.previous_failure,
            ),
            NoteReply,
        )
        for script, marked, reason in draft
    ]
    corrections_call = ask(
        provider,
        brief,
        role,
        corrections_prompts(
            inputs.memo,
            weak,
            inputs.misconceptions,
            teacher_note=inputs.teacher_note,
            previous_failure="; ".join(
                x
                for x in (
                    inputs.previous_failure,
                    inputs.class_sheet_reason
                    and f"the Reviewer rejected the class sheet: {inputs.class_sheet_reason}",
                )
                if x
            )
            or None,
        ),
        CorrectionsReply,
    )
    note_replies, corrections_reply = await asyncio.gather(asyncio.gather(*calls), corrections_call)
    usages: list[Usage] = []
    errors: list[Result] = []
    notes = dict(kept)
    for (script, _, _), asked in zip(draft, note_replies, strict=True):
        if isinstance(asked, Result):
            errors.append(
                asked.model_copy(
                    update={"reason": f"note {script.learner_pseudonym}: {asked.reason}"}
                )
            )
            continue
        note, usage = asked
        usages.append(usage)
        notes[script.learner_pseudonym] = LearnerNote(
            pseudonym=script.learner_pseudonym, **note.model_dump()
        )
    corrections: CorrectionsReply | None = None
    if isinstance(corrections_reply, Result):
        errors.append(
            corrections_reply.model_copy(
                update={"reason": f"corrections: {corrections_reply.reason}"}
            )
        )
    else:
        corrections, usage = corrections_reply
        usages.append(usage)
    cost = sum((u.cost_zar for u in usages), Decimal(0))
    if errors or corrections is None:
        blocked = any(e.status is ResultStatus.BLOCKED for e in errors)
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED if blocked else ResultStatus.RETRY,
            reason="; ".join(sorted({e.reason for e in errors})),
            cost_zar=cost,
        )
    failures = _check_corrections(corrections, weak)
    if failures:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY,
            reason="; ".join(failures),
            cost_zar=cost,
        )
    mod = inputs.moderation_report_data
    return Result(
        brief_id=brief.brief_id,
        agent=role,
        status=ResultStatus.DONE,
        outputs={
            "class_sheet_md": class_sheet_md(
                inputs.memo, inputs.item_stats, inputs.misconceptions, corrections.corrections
            ),
            "learner_notes": [notes[p].model_dump(mode="json") for p in sorted(notes)],
            "catch_up_plan_md": catch_up_plan_md(inputs.catch_up_plan),
            "moderation_report_md": moderation_report_md(
                mod.title,
                mod.sample,
                mod.disagreements,
                mod.resolutions,
                mod.questions_compared,
                mod.moderator,
            ),
        },
        evidence=tuple(f"{s.question_id}: facility {s.facility_percent}%" for s in weak),
        cost_zar=cost,
    )


def writer_brief(
    assessment_id: str,
    *,
    memo: Memo,
    marked: Sequence[MarkedScript],
    scripts: Sequence[Script],
    item_stats: Sequence[ItemStat],
    misconceptions: Sequence[Misconception],
    catch_up_plan: Sequence[CatchUpItem],
    moderation: ModerationReportData,
    attempt: int = 1,
    notes: Sequence[LearnerNote] = (),
    rewrite: Mapping[str, str] | None = None,
    teacher_note: str | None = None,
    previous_failure: str | None = None,
    budget_tokens: int | None = None,
    review_round: int = 1,
    class_sheet_reason: str | None = None,
) -> Brief:
    inputs: dict[str, Any] = {
        "memo": memo.model_dump(mode="json"),
        "marked": [m.model_dump(mode="json") for m in marked],
        "scripts": [s.model_dump(mode="json") for s in scripts],
        "item_stats": [s.model_dump(mode="json") for s in item_stats],
        "misconceptions": [m.model_dump(mode="json") for m in misconceptions],
        "catch_up_plan": [i.model_dump(mode="json") for i in catch_up_plan],
        "moderation_report_data": moderation.model_dump(mode="json"),
    }
    if notes:
        inputs["notes"] = [n.model_dump(mode="json") for n in notes]
    if rewrite:
        inputs["rewrite"] = dict(rewrite)
    if teacher_note:
        inputs["teacher_note"] = teacher_note
    if previous_failure:
        inputs["previous_failure"] = previous_failure
    if class_sheet_reason:
        inputs["class_sheet_reason"] = class_sheet_reason
    return Brief(
        brief_id=f"{assessment_id}/class/writer/{_round(review_round)}{attempt}",
        assessment_id=assessment_id,
        agent=AgentRole.WRITER,
        task="Draft the learner notes and the class sheet around the computed numbers",
        inputs=inputs,
        attempt=attempt,
        budget_tokens=budget_tokens,
    )


def _round(review_round: int) -> str:
    """Brief ids stay unique across review rounds: round 2's first attempt is r2-1."""
    return f"r{review_round}-" if review_round > 1 else ""
