"""The Marker: marks one script against the memo, quoting the learner's own words for every mark.

Design: docs/design/marking-pipeline.md §4 steps 9-12 and §6; the `marker` contract. Two entry
points: `mark(brief, provider)` does one attempt and returns a Result; `mark_script(...)` is the
retry loop the Planner runs around it: the rules check every attempt, a failed check is fed back
into the next Brief, and after `max_attempts` the script goes to the teacher, never silently in.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    Brief,
    CheckResult,
    Contract,
    Mark,
    MarkedScript,
    Memo,
    Result,
    ResultStatus,
    Script,
    SubjectPack,
)
from mothusi_llm import Provider, ProviderError
from mothusi_rules import check_evidence, check_memo_lines, check_script

MARKER_NAME = "marker"
MODERATOR_NAME = "moderator"

MODERATOR_PREAMBLE = """You are Mothusi's Moderator: a second, independent marker. You have NOT
seen the first marking and must not guess at it; mark this script on its own merits against the
memo, as a head of department re-marks a sample. Everything below applies to you as to the Marker.

"""

MARKER_SYSTEM = """You are Mothusi's Marker: you mark one learner's script against the teacher's
memo.

Rules you never break:
1. Mark every memo question exactly once, in whole marks from 0 to its maximum.
2. For every mark above zero, evidence_quote is an exact, character-for-character substring of the
   learner's answer to that question. Copy it; never tidy, reorder, correct or paraphrase it. If you
   cannot quote the learner's own words for a mark, do not award it.
3. memo_line is copied verbatim from that question's memo answer or marking notes; a line that is
   not in the memo is refused. principle names the marking
   principle in a few words (for example "answer matches memo", "method mark", "CA marking:
   follows the learner's earlier error", "no attempt").
4. A question the learner did not answer gets 0, principle "no attempt", empty evidence_quote.
5. The learner is identified only by the pseudonym given. Never guess or use a name.
6. Reply with JSON only, matching the requested schema. No prose outside the JSON."""


class MarkerReply(Contract):
    """What the model returns. Identity (script_id, marker) and the total are set by code.

    The model gives marks only: a total it wrote disagreed with its own marks on 24 of 32 scripts
    in the 2026-11-12 eval, and totals are rules in code (non-negotiable 8).
    """

    marks: tuple[Mark, ...] = Field(min_length=1)


class Attempt(Contract):
    """One pass of the loop: what was asked, what came back, what the rules said."""

    brief: Brief
    result: Result
    checks: tuple[CheckResult, ...] = ()


class MarkingOutcome(Contract):
    """The loop's verdict for one script."""

    status: Literal["done", "needs_teacher", "blocked"]
    script_id: str = Field(min_length=1)
    marked: MarkedScript | None = None
    attempts: tuple[Attempt, ...] = Field(min_length=1)
    reason: str = ""
    cost_zar: Decimal = Field(ge=0, default=Decimal(0))


def marker_prompts(
    memo: Memo,
    script: Script,
    conventions: tuple[str, ...],
    previous_failure: str | None = None,
    moderation: tuple[MarkedScript, MarkedScript] | None = None,
    role: AgentRole = AgentRole.MARKER,
) -> tuple[str, str]:
    """The (system, user) pair for one attempt. Deterministic: the replay fixtures key on it."""
    if role is AgentRole.MODERATOR and moderation is not None:
        raise ValueError("a moderator never sees the first marking")
    answers = {answer.question_id: answer.text for answer in script.answers}
    lines: list[str] = [f"Learner: {script.learner_pseudonym} (script {script.script_id})", ""]
    if conventions:
        lines.append("Marking conventions for this subject:")
        lines.extend(f"- {c}" for c in conventions)
        lines.append("")
    lines.append(f"Memo ({memo.total_marks} marks):")
    for item in memo.items:
        lines.append(f"[{item.question_id}] ({item.max_marks} marks) {item.question}".rstrip())
        lines.append(f"  memo answer: {item.answer}")
        if item.marking_notes:
            lines.append(f"  marking notes: {item.marking_notes}")
    lines.append("")
    lines.append("The learner's answers (verbatim; quote from these exactly):")
    for item in memo.items:
        text = answers.get(item.question_id)
        if text is None:
            lines.append(f"[{item.question_id}] (no answer)")
        else:
            lines.append(f"[{item.question_id}]")
            lines.append(text)
    if moderation is not None:
        first, second = moderation
        lines.append("")
        lines.append(
            "A blind second marker disagreed with the first marking. Both are below; re-mark"
        )
        lines.append("the disputed questions against the memo and say in principle why.")
        for label, marked in (("first", first), ("second", second)):
            lines.append(f"{label} marking:")
            lines.extend(
                f"  [{m.question_id}] {m.awarded}/{m.max_marks}: {m.principle}"
                for m in marked.marks
            )
    if previous_failure:
        lines.append("")
        lines.append(
            "Your previous attempt failed these checks; fix exactly these and nothing else:"
        )
        lines.append(previous_failure)
    lines.append("")
    lines.append(
        'Reply as JSON: {"marks": [{"question_id", "awarded", "max_marks", '
        '"evidence_quote", "memo_line", "principle"}, ...]}'
    )
    system = MARKER_SYSTEM if role is AgentRole.MARKER else MODERATOR_PREAMBLE + MARKER_SYSTEM
    return system, "\n".join(lines)


def _inputs(
    brief: Brief,
) -> tuple[Memo, Script, tuple[str, ...], str | None, tuple[MarkedScript, MarkedScript] | None]:
    memo = Memo.model_validate(brief.inputs["memo"])
    script = Script.model_validate(brief.inputs["script"])
    conventions = tuple(str(c) for c in brief.inputs.get("pack_conventions", []))
    previous = brief.inputs.get("previous_failure")
    moderation = None
    if "moderation" in brief.inputs:
        raw = brief.inputs["moderation"]
        moderation = (
            MarkedScript.model_validate(raw["first"]),
            MarkedScript.model_validate(raw["second"]),
        )
    return memo, script, conventions, (str(previous) if previous else None), moderation


async def mark(brief: Brief, provider: Provider) -> Result:
    """One attempt, as Marker or Moderator (brief.agent decides). The caller runs the rules.

    A Moderator brief that carries first marks is refused outright: blindness is the contract.
    """
    role = brief.agent
    if role not in (AgentRole.MARKER, AgentRole.MODERATOR):
        return Result(
            brief_id=brief.brief_id,
            agent=AgentRole.MARKER,
            status=ResultStatus.BLOCKED,
            reason=f"brief is for {brief.agent.value}, not the marker",
        )
    if role is AgentRole.MODERATOR and ("moderation" in brief.inputs or "marked" in brief.inputs):
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED,
            reason="a moderator brief must not carry the first marking",
        )
    try:
        memo, script, conventions, previous, moderation = _inputs(brief)
    except (KeyError, TypeError, ValidationError) as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED,
            reason=f"brief inputs do not fit the {role.value} contract: {error}",
        )
    system, user = marker_prompts(memo, script, conventions, previous, moderation, role)
    try:
        reply, usage = await provider.complete(
            system, user, MarkerReply, budget_tokens=brief.budget_tokens
        )
    except ProviderError as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY if error.retryable else ResultStatus.BLOCKED,
            reason=f"{provider.name}: {error}",
        )
    try:
        marked = MarkedScript(
            script_id=script.script_id,
            marker=MARKER_NAME if role is AgentRole.MARKER else MODERATOR_NAME,
            marks=reply.marks,
            total=sum(m.awarded for m in reply.marks),
        )
    except ValidationError as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY,
            reason=f"reply does not fit MarkedScript: {error}",
            cost_zar=usage.cost_zar,
        )
    return Result(
        brief_id=brief.brief_id,
        agent=role,
        status=ResultStatus.DONE,
        outputs={"marked": marked.model_dump(mode="json")},
        evidence=tuple(m.memo_line for m in marked.marks if m.awarded > 0),
        cost_zar=usage.cost_zar,
    )


def _brief(
    assessment_id: str,
    memo: Memo,
    script: Script,
    pack: SubjectPack,
    attempt: int,
    previous_failure: str | None,
    budget_tokens: int | None,
    role: AgentRole = AgentRole.MARKER,
    moderation: tuple[MarkedScript, MarkedScript] | None = None,
) -> Brief:
    inputs: dict[str, Any] = {
        "memo": memo.model_dump(mode="json"),
        "script": script.model_dump(mode="json"),
        "pack_conventions": list(pack.conventions),
    }
    if previous_failure:
        inputs["previous_failure"] = previous_failure
    if moderation is not None:
        first, second = moderation
        inputs["moderation"] = {
            "first": first.model_dump(mode="json"),
            "second": second.model_dump(mode="json"),
        }
    verb = "Mark" if role is AgentRole.MARKER else "Blind re-mark of"
    stage = role.value if moderation is None else "re-mark"
    if moderation is not None:
        verb = "Re-mark, with both markings,"
    return Brief(
        brief_id=f"{assessment_id}/{script.script_id}/{stage}/{attempt}",
        assessment_id=assessment_id,
        agent=role,
        task=f"{verb} script {script.script_id} against the memo, quoting the learner's own words",
        inputs=inputs,
        attempt=attempt,
        budget_tokens=budget_tokens,
    )


marker_brief = _brief
"""The Brief mark_script dispatches for an attempt: the Planner runs blind_check on it first."""


async def mark_script(
    memo: Memo,
    script: Script,
    pack: SubjectPack,
    provider: Provider,
    *,
    assessment_id: str,
    max_attempts: int = 3,
    budget_tokens: int | None = None,
    role: AgentRole = AgentRole.MARKER,
    moderation: tuple[MarkedScript, MarkedScript] | None = None,
    on_brief: Callable[[Brief], None] | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> MarkingOutcome:
    """Mark, check, and retry with the failure fed back; after max_attempts, the teacher decides.

    max_attempts = 3 is "two retries" (marking-pipeline.md §4): the first attempt and two more.
    role = MODERATOR runs the same loop blind (no `moderation` allowed); `moderation` is the
    Marker's re-mark after a disagreement, with both markings in the Brief. `on_brief` is called
    as each Brief is dispatched and `on_attempt` as each attempt is checked, so the live log
    (US8) shows the work as it happens, not after the batch.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if role not in (AgentRole.MARKER, AgentRole.MODERATOR):
        raise ValueError("mark_script runs the marker or the moderator")
    if role is AgentRole.MODERATOR and moderation is not None:
        raise ValueError("a moderator never sees the first marking")
    attempts: list[Attempt] = []

    def keep(attempt: Attempt) -> None:
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)

    cost = Decimal(0)
    previous: str | None = None
    for n in range(1, max_attempts + 1):
        brief = _brief(
            assessment_id, memo, script, pack, n, previous, budget_tokens, role, moderation
        )
        if on_brief is not None:
            on_brief(brief)
        result = await mark(brief, provider)
        cost += result.cost_zar
        if result.status is ResultStatus.BLOCKED:
            keep(Attempt(brief=brief, result=result))
            return MarkingOutcome(
                status="blocked",
                script_id=script.script_id,
                attempts=tuple(attempts),
                reason=result.reason,
                cost_zar=cost,
            )
        if result.status is not ResultStatus.DONE:
            keep(Attempt(brief=brief, result=result))
            previous = result.reason
            continue
        marked = MarkedScript.model_validate(result.outputs["marked"])
        checks = (
            *check_script(marked, memo),
            check_evidence(marked, script),
            check_memo_lines(marked, memo),
        )
        keep(Attempt(brief=brief, result=result, checks=checks))
        failed = [c for c in checks if not c.passed]
        if not failed:
            return MarkingOutcome(
                status="done",
                script_id=script.script_id,
                marked=marked,
                attempts=tuple(attempts),
                cost_zar=cost,
            )
        previous = "; ".join(f"{c.check}: {c.detail}" for c in failed)
    return MarkingOutcome(
        status="needs_teacher",
        script_id=script.script_id,
        attempts=tuple(attempts),
        reason=(
            f"{script.script_id}: {max_attempts} attempts did not pass the checks; "
            f"last failure: {previous}"
        ),
        cost_zar=cost,
    )
