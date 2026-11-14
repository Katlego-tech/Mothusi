"""The Analyst: names the misconception behind each item the class must be re-taught.

Design: docs/architecture/design/marking-pipeline.md §4 step 21 and §6.1; the `analyst` contract.
The model
reads the answers that lost marks on each `re-teach` item and names the pattern, quoting the
learners' own work. Code decides the rest: exactly one misconception per `re-teach` item, every
quote verbatim from a script that lost marks there, and how many scripts show it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    Brief,
    Contract,
    ItemStat,
    MarkedScript,
    Memo,
    Result,
    ResultStatus,
    Script,
)
from mothusi_core.agents.loop import ask, normalised, refuse, wrong_role
from mothusi_llm import Provider

MIN_QUOTE = 3  # shorter quotes must be a whole answer: as substrings they match nearly anything

ANALYST_SYSTEM = """You are Mothusi's Analyst: you read a class's wrong answers the way a head of
department does after a test, to find what the class misunderstood, not who got it wrong.

Rules you never break:
1. For every question listed, name ONE misconception: the reasoning error most of these answers
   share, in one plain sentence a teacher can re-teach from.
2. evidence_quotes are exact, character-for-character substrings of the answers shown for that
   question. Copy them; never tidy or paraphrase. Two or three short quotes are enough.
3. language_load_flags lists the question ids where the errors start at reading the question
   (a misread word or condition) rather than at the mathematics. Leave it empty if none do.
4. Learners appear only as script ids. Never guess or use a name.
5. Reply with JSON only, matching the requested schema. No prose outside the JSON."""


class MisconceptionDraft(Contract):
    """What the model names for one item."""

    question_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    evidence_quotes: tuple[str, ...] = Field(min_length=1)


class AnalystReply(Contract):
    misconceptions: tuple[MisconceptionDraft, ...] = ()
    language_load_flags: tuple[str, ...] = ()


class Misconception(Contract):
    """A misconception the code has checked: quotes verbatim, `scripts` counted by code."""

    question_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    evidence_quotes: tuple[str, ...] = Field(min_length=1)
    scripts: int = Field(ge=1, description="Scripts that lost marks here and contain a quote")


def _reteach(item_stats: Sequence[ItemStat]) -> list[ItemStat]:
    return [s for s in item_stats if s.flag == "re-teach"]


def _losing_answers(
    question_id: str, marked: Sequence[MarkedScript], scripts: Sequence[Script]
) -> list[tuple[str, str]]:
    """(script_id, answer) for every script that lost marks on the question and answered it."""
    lost = {
        m.script_id
        for m in marked
        for k in m.marks
        if k.question_id == question_id and k.awarded < k.max_marks
    }
    found: list[tuple[str, str]] = []
    for script in sorted(scripts, key=lambda s: s.script_id):
        if script.script_id not in lost:
            continue
        for answer in script.answers:
            if answer.question_id == question_id and answer.text.strip():
                found.append((script.script_id, answer.text))
    return found


def analyst_prompts(
    memo: Memo,
    item_stats: Sequence[ItemStat],
    marked: Sequence[MarkedScript],
    scripts: Sequence[Script],
    previous_failure: str | None = None,
) -> tuple[str, str]:
    """The (system, user) pair. Deterministic: the replay fixtures key on it."""
    items = {i.question_id: i for i in memo.items}
    lines: list[str] = [f"Class set: {len(scripts)} scripts. Questions to re-teach:", ""]
    for stat in _reteach(item_stats):
        item = items[stat.question_id]
        lines.append(
            f"[{item.question_id}] ({item.max_marks} marks, facility {stat.facility_percent}%) "
            f"{item.question}".rstrip()
        )
        lines.append(f"  memo answer: {item.answer}")
        if item.marking_notes:
            lines.append(f"  marking notes: {item.marking_notes}")
        lines.append("  answers that lost marks (verbatim; quote from these exactly):")
        for script_id, text in _losing_answers(item.question_id, marked, scripts):
            lines.append(f"  <{script_id}>")
            lines.extend(f"    {line}" for line in text.splitlines())
        lines.append("")
    if previous_failure:
        lines.append("Your previous answer failed these checks; fix exactly these:")
        lines.append(previous_failure)
        lines.append("")
    lines.append(
        'Reply as JSON: {"misconceptions": [{"question_id", "pattern", "evidence_quotes": '
        '[str, ...]}, ...], "language_load_flags": [question_id, ...]}'
    )
    return ANALYST_SYSTEM, "\n".join(lines)


def check_misconceptions(
    reply: AnalystReply,
    memo: Memo,
    item_stats: Sequence[ItemStat],
    marked: Sequence[MarkedScript],
    scripts: Sequence[Script],
) -> tuple[tuple[Misconception, ...], list[str]]:
    """The checked misconceptions in item order, and every failure found (empty when clean)."""
    wanted = [s.question_id for s in _reteach(item_stats)]
    failures: list[str] = []
    drafts: dict[str, MisconceptionDraft] = {}
    for draft in reply.misconceptions:
        if draft.question_id not in wanted:
            failures.append(f"{draft.question_id}: not an item flagged re-teach")
        elif draft.question_id in drafts:
            failures.append(f"{draft.question_id}: more than one misconception")
        else:
            drafts[draft.question_id] = draft
    failures.extend(f"{qid}: no misconception named" for qid in wanted if qid not in drafts)
    memo_ids = {i.question_id for i in memo.items}
    failures.extend(
        f"language_load_flags: {qid} is not a memo question"
        for qid in reply.language_load_flags
        if qid not in memo_ids
    )
    checked: list[Misconception] = []
    for qid in wanted:
        draft = drafts.get(qid)
        if draft is None:
            continue
        answers = [(sid, normalised(text)) for sid, text in _losing_answers(qid, marked, scripts)]
        showing: set[str] = set()
        for quote in draft.evidence_quotes:
            span = normalised(quote)
            if not span:
                failures.append(f"{qid}: an empty quote is not evidence")
                continue
            # A short quote (say "4") would match nearly any answer as a substring, so it counts
            # only where it is a learner's whole answer.
            hits = {
                sid
                for sid, text in answers
                if (span in text if len(span) >= MIN_QUOTE else span == text)
            }
            if not hits:
                failures.append(f'{qid}: quote not in any script that lost marks: "{quote}"')
            showing |= hits
        if showing:
            checked.append(
                Misconception(
                    question_id=qid,
                    pattern=draft.pattern,
                    evidence_quotes=draft.evidence_quotes,
                    scripts=len(showing),
                )
            )
    return tuple(checked), failures


def _inputs(
    brief: Brief,
) -> tuple[Memo, tuple[ItemStat, ...], tuple[MarkedScript, ...], tuple[Script, ...], str | None]:
    memo = Memo.model_validate(brief.inputs["memo"])
    stats = tuple(ItemStat.model_validate(s) for s in brief.inputs["item_stats"])
    marked = tuple(MarkedScript.model_validate(m) for m in brief.inputs["marked"])
    scripts = tuple(Script.model_validate(s) for s in brief.inputs["scripts"])
    previous = brief.inputs.get("previous_failure")
    return memo, stats, marked, scripts, (str(previous) if previous else None)


async def analyse(brief: Brief, provider: Provider) -> Result:
    """One Analyst pass. A failed check is `retry` with its detail; the loop re-briefs."""
    role = AgentRole.ANALYST
    if (refused := wrong_role(brief, role)) is not None:
        return refused
    try:
        memo, stats, marked, scripts, previous = _inputs(brief)
    except (KeyError, TypeError, ValidationError) as error:
        return refuse(brief, role, f"brief inputs do not fit the analyst contract: {error}")
    unknown = sorted({s.question_id for s in stats} - {i.question_id for i in memo.items})
    if unknown:
        return refuse(brief, role, f"item stats for questions not in the memo: {unknown}")
    if not _reteach(stats):
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.DONE,
            outputs={"misconceptions": [], "language_load_flags": []},
            reason="",
        )
    asked = await ask(
        provider,
        brief,
        role,
        analyst_prompts(memo, stats, marked, scripts, previous),
        AnalystReply,
    )
    if isinstance(asked, Result):
        return asked
    reply, usage = asked
    found, failures = check_misconceptions(reply, memo, stats, marked, scripts)
    if failures:
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
            "misconceptions": [m.model_dump(mode="json") for m in found],
            "language_load_flags": list(reply.language_load_flags),
        },
        evidence=tuple(q for m in found for q in m.evidence_quotes),
        cost_zar=usage.cost_zar,
    )


def analyst_brief(
    assessment_id: str,
    memo: Memo,
    item_stats: Sequence[ItemStat],
    marked: Sequence[MarkedScript],
    scripts: Sequence[Script],
    attempt: int = 1,
    previous_failure: str | None = None,
    budget_tokens: int | None = None,
) -> Brief:
    inputs: dict[str, Any] = {
        "memo": memo.model_dump(mode="json"),
        "item_stats": [s.model_dump(mode="json") for s in item_stats],
        "marked": [m.model_dump(mode="json") for m in marked],
        "scripts": [s.model_dump(mode="json") for s in scripts],
    }
    if previous_failure:
        inputs["previous_failure"] = previous_failure
    return Brief(
        brief_id=f"{assessment_id}/class/analyst/{attempt}",
        assessment_id=assessment_id,
        agent=AgentRole.ANALYST,
        task="Name the misconception behind each re-teach item, quoting the learners' own work",
        inputs=inputs,
        attempt=attempt,
        budget_tokens=budget_tokens,
    )
