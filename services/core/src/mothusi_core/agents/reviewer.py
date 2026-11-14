"""The Reviewer: refuses any learner note that is not grounded, then reviews tone.

Design: docs/architecture/design/marking-pipeline.md §4 steps 25-26 and §6.1; the `reviewer`
contract. Grounding
is code's job: `check_note_grounding` (rules) refuses a quote not in the script, a mark not in the
marked script, a label or a comparison, and praise without a quote; `check_note_weeks` refuses an
ATP week that is not in the checked catch-up plan. Only notes that pass go to the
model, which reviews tone in one call and can only add a rejection: no note code refused can pass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    Brief,
    CatchUpItem,
    Contract,
    LearnerNote,
    MarkedScript,
    Result,
    ResultStatus,
    Script,
)
from mothusi_core.agents.loop import ask, refuse, wrong_role
from mothusi_llm import Provider
from mothusi_rules import check_note_grounding

REVIEWER_SYSTEM = """You are Mothusi's Reviewer: you read feedback notes before a teacher approves
them, for tone only. Every quote and mark in them has already been checked against the scripts.

Reject a note (passed false, with a one-line reason) if it:
- labels the learner rather than describing the work (in any words, not only "weak" or "lazy");
- compares the learner with others, or with an expected kind of learner;
- discourages, or predicts that the learner will keep failing;
- praises without pointing at the work.
Otherwise pass it. Give a verdict for every note listed, by pseudonym, and one for the class sheet
on the same terms (it must describe questions and work, never single out learners).
Reply with JSON only, matching the requested schema. No prose outside the JSON."""


MAX_WEEK = 52

# "week 11", "weeks 11 and 12", "weeks 11, 12 or 14", "weeks 11-13", "weeks 11 to 13".
_WEEK = re.compile(
    r"\bweeks?\s+(\d+(?:\s*(?:,|&|\band\b|\bor\b|\bto\b|-|\u2013|\u2014)\s*\d+)*)",
    re.IGNORECASE,
)
_RANGE = re.compile(r"(\d+)\s*(?:\bto\b|-|\u2013|\u2014)\s*(\d+)", re.IGNORECASE)


def _weeks_named(span: str) -> set[int]:
    """Every week a reference covers: each number, and every week inside a range."""
    weeks = {int(n) for n in re.findall(r"\d+", span)}
    for low, high in _RANGE.findall(span):
        a, b = sorted((int(low), int(high)))
        # Expand only inside the year: an endpoint outside 1-52 is already named above and
        # reported, and a model-written "weeks 1-999999999" must not allocate a billion weeks.
        weeks.update(range(max(a, 1), min(b, MAX_WEEK) + 1))
    return weeks


def check_note_weeks(note: LearnerNote, plan: Sequence[CatchUpItem]) -> list[str]:
    """Every week a note names must be a week of the checked catch-up plan (the grounding rule)."""
    planned = {item.week for item in plan}
    unplanned: set[int] = set()
    for text in (note.outcome, note.mark_with_quote, note.next_task, note.self_assessment_prompt):
        for match in _WEEK.finditer(text):
            unplanned |= _weeks_named(match.group(1)) - planned
    return [f"week {w} is not in the checked catch-up plan" for w in sorted(unplanned)]


class ToneVerdict(Contract):
    pseudonym: str = Field(min_length=1)
    passed: bool
    reason: str = ""


class ToneReply(Contract):
    verdicts: tuple[ToneVerdict, ...] = ()
    class_sheet_passed: bool
    class_sheet_reason: str = ""


def reviewer_prompts(notes: Sequence[LearnerNote], class_sheet_md: str) -> tuple[str, str]:
    """The (system, user) pair for the tone pass. Deterministic: replay keys on it."""
    lines = ["Notes to review:"]
    for note in notes:
        lines.extend(
            (
                "",
                f"<{note.pseudonym}>",
                f"outcome: {note.outcome}",
                f"mark_with_quote: {note.mark_with_quote}",
                f"next_task: {note.next_task}",
                f"self_assessment_prompt: {note.self_assessment_prompt}",
            )
        )
    lines.extend(("", "Class sheet:", class_sheet_md.strip()))
    lines.append(
        'Reply as JSON: {"verdicts": [{"pseudonym", "passed", "reason"}, ...], '
        '"class_sheet_passed": bool, "class_sheet_reason": str}'
    )
    return REVIEWER_SYSTEM, "\n".join(lines)


class _Inputs(Contract):
    learner_notes: tuple[LearnerNote, ...] = Field(min_length=1)
    marked: tuple[MarkedScript, ...]
    scripts: tuple[Script, ...]
    class_sheet_md: str = Field(min_length=1)
    catch_up_plan: tuple[CatchUpItem, ...] = ()


def _grounding(inputs: _Inputs) -> dict[str, tuple[bool, str]] | str:
    """pseudonym -> (passed, detail) from check_note_grounding, or why the inputs don't fit."""
    scripts = {s.learner_pseudonym: s for s in inputs.scripts}
    marked = {m.script_id: m for m in inputs.marked}
    found: dict[str, tuple[bool, str]] = {}
    for note in inputs.learner_notes:
        if note.pseudonym in found:
            return f"two notes for {note.pseudonym}"
        script = scripts.get(note.pseudonym)
        if script is None or script.script_id not in marked:
            return f"{note.pseudonym}: no script or marking to check the note against"
        check = check_note_grounding(note, script, marked[script.script_id])
        weeks = check_note_weeks(note, inputs.catch_up_plan)
        if weeks:
            detail = "; ".join([*([] if check.passed else [check.detail]), *weeks])
            found[note.pseudonym] = (False, detail)
        else:
            found[note.pseudonym] = (check.passed, check.detail)
    return found


async def review(brief: Brief, provider: Provider) -> Result:
    """One Reviewer pass. Rejections are verdicts (status done); `retry` is a malformed reply."""
    role = AgentRole.REVIEWER
    if (refused := wrong_role(brief, role)) is not None:
        return refused
    try:
        inputs = _Inputs.model_validate(brief.inputs)
    except ValidationError as error:
        return refuse(brief, role, f"brief inputs do not fit the reviewer contract: {error}")
    grounding = _grounding(inputs)
    if isinstance(grounding, str):
        return refuse(brief, role, grounding)
    to_model = [n for n in inputs.learner_notes if grounding[n.pseudonym][0]]
    asked = await ask(
        provider, brief, role, reviewer_prompts(to_model, inputs.class_sheet_md), ToneReply
    )
    if isinstance(asked, Result):
        return asked
    reply, usage = asked
    sent = {n.pseudonym for n in to_model}
    named = [v.pseudonym for v in reply.verdicts]
    tone = {v.pseudonym: v for v in reply.verdicts}
    failures = [f"more than one tone verdict for {p}" for p in sorted(tone) if named.count(p) > 1]
    failures.extend(f"no tone verdict for {p}" for p in sorted(sent - tone.keys()))
    failures.extend(f"a verdict for {p}, which was not sent" for p in sorted(tone.keys() - sent))
    if failures:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY,
            reason="; ".join(failures),
            cost_zar=usage.cost_zar,
        )
    verdicts: list[dict[str, Any]] = []
    for note in inputs.learner_notes:
        grounded, detail = grounding[note.pseudonym]
        if not grounded:
            verdicts.append({"pseudonym": note.pseudonym, "passed": False, "reason": detail})
            continue
        v = tone[note.pseudonym]
        reason = v.reason if v.passed else (v.reason or "rejected on tone")
        verdicts.append({"pseudonym": note.pseudonym, "passed": v.passed, "reason": reason})
    sheet_reason = reply.class_sheet_reason
    if not reply.class_sheet_passed and not sheet_reason:
        sheet_reason = "rejected on tone"
    passed = reply.class_sheet_passed and all(v["passed"] for v in verdicts)
    return Result(
        brief_id=brief.brief_id,
        agent=role,
        status=ResultStatus.DONE,
        outputs={
            "verdicts": verdicts,
            "class_sheet": {"passed": reply.class_sheet_passed, "reason": sheet_reason},
            "passed": passed,
        },
        evidence=tuple(f"{v['pseudonym']}: {v['reason']}" for v in verdicts if not v["passed"]),
        cost_zar=usage.cost_zar,
    )


def reviewer_brief(
    assessment_id: str,
    learner_notes: Sequence[LearnerNote],
    marked: Sequence[MarkedScript],
    scripts: Sequence[Script],
    class_sheet_md: str,
    catch_up_plan: Sequence[CatchUpItem] = (),
    attempt: int = 1,
    budget_tokens: int | None = None,
    review_round: int = 1,
) -> Brief:
    return Brief(
        brief_id=f"{assessment_id}/class/reviewer/{_round(review_round)}{attempt}",
        assessment_id=assessment_id,
        agent=AgentRole.REVIEWER,
        task="Refuse any note not grounded in the learner's script, then review the tone",
        inputs={
            "learner_notes": [n.model_dump(mode="json") for n in learner_notes],
            "marked": [m.model_dump(mode="json") for m in marked],
            "scripts": [s.model_dump(mode="json") for s in scripts],
            "class_sheet_md": class_sheet_md,
            "catch_up_plan": [i.model_dump(mode="json") for i in catch_up_plan],
        },
        attempt=attempt,
        budget_tokens=budget_tokens,
    )


def _round(review_round: int) -> str:
    """Brief ids stay unique across review rounds: round 2's first attempt is r2-1."""
    return f"r{review_round}-" if review_round > 1 else ""
