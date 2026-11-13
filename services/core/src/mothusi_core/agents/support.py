"""Support: places re-teaching for each misconception in a week the ATP still has left.

Design: docs/architecture/design/marking-pipeline.md §4 step 22 and §6.1; the `support` contract.
The model
chooses the week, topic and activity; `check_catch_up` (rules) refuses a week already taught or a
source not copied from that week, and code requires every misconception to get some catch-up work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    AtpWeek,
    Brief,
    CatchUpItem,
    Contract,
    Passage,
    Result,
    ResultStatus,
)
from mothusi_core.agents.analyst import Misconception
from mothusi_core.agents.loop import ask, refuse, wrong_role
from mothusi_llm import Provider
from mothusi_rules import check_catch_up, check_citations

SUPPORT_SYSTEM = """You are Mothusi's Support colleague: you plan catch-up teaching for what a class
misunderstood, inside the weeks the annual teaching plan (ATP) still has left.

Rules you never break:
1. Plan at least one activity for every misconception listed.
2. week is one of the remaining ATP weeks listed, and atp_source is that week's source, copied
   exactly. Never use a week that is not listed.
3. Prefer a week whose ATP topic builds on the misconception; otherwise use revision time. The
   activity is short and concrete (a starter, a worked example, a few practice items), so it fits
   beside that week's own topic.
4. Reply with JSON only, matching the requested schema. No prose outside the JSON."""

# Added only when retrieval returned passages, so a run without the knowledge service sends the
# exact prompts the replay recording keys on (knowledge.md §6.7).
CITATION_RULE = """
5. citation is "" or copied exactly from a passage listed under that item's question, in the form
   shown in its square brackets. Never cite a passage listed under another question."""


class SupportItem(Contract):
    question_id: str = Field(min_length=1)
    week: int = Field(ge=1, le=52)
    topic: str = Field(min_length=1)
    activity: str = Field(min_length=1)
    atp_source: str = Field(min_length=1)
    citation: str = ""


class SupportReply(Contract):
    items: tuple[SupportItem, ...] = Field(min_length=1)


def support_prompts(
    misconceptions: Sequence[Misconception],
    weeks: Sequence[AtpWeek],
    previous_failure: str | None = None,
    passages_by_question: Mapping[str, Sequence[Passage]] | None = None,
) -> tuple[str, str]:
    """The (system, user) pair. Deterministic: the replay fixtures key on it. With no passages it
    is exactly the pre-retrieval pair."""
    passages = {q: list(ps) for q, ps in (passages_by_question or {}).items() if ps}
    lines = ["Misconceptions to re-teach:"]
    for m in misconceptions:
        lines.append(f"[{m.question_id}] {m.pattern} ({m.scripts} scripts)")
    lines.extend(("", "Remaining ATP weeks (week | topic | source):"))
    lines.extend(f"{w.week} | {w.topic} | {w.source}" for w in weeks)
    if passages:
        lines.extend(("", "Curriculum passages per question ([citation] text):"))
        for question_id, found in passages.items():
            lines.append(f"Question {question_id}:")
            lines.extend(f"[{p.citation}] {' '.join(p.text.split())}" for p in found)
    if previous_failure:
        lines.extend(("", "Your previous plan failed these checks; fix exactly these:"))
        lines.append(previous_failure)
    lines.extend(
        (
            "",
            'Reply as JSON: {"items": [{"question_id", "week", "topic", "activity", '
            '"atp_source"' + (', "citation"' if passages else "") + "}, ...]}",
        )
    )
    return SUPPORT_SYSTEM + (CITATION_RULE if passages else ""), "\n".join(lines)


async def plan_catch_up(brief: Brief, provider: Provider) -> Result:
    """One Support pass. No misconceptions: no model call and an empty plan."""
    role = AgentRole.SUPPORT
    if (refused := wrong_role(brief, role)) is not None:
        return refused
    try:
        found = tuple(Misconception.model_validate(m) for m in brief.inputs["misconceptions"])
        weeks = tuple(AtpWeek.model_validate(w) for w in brief.inputs["atp_weeks_remaining"])
        passages = {
            str(q): [Passage.model_validate(p) for p in ps]
            for q, ps in dict(brief.inputs.get("passages_by_question", {})).items()
        }
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return refuse(brief, role, f"brief inputs do not fit the support contract: {error}")
    if not found:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.DONE,
            outputs={"catch_up_plan": []},
        )
    if not weeks:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.NEEDS_TEACHER,
            reason="no ATP weeks remain this year to place catch-up work in",
        )
    previous = brief.inputs.get("previous_failure")
    asked = await ask(
        provider,
        brief,
        role,
        support_prompts(found, weeks, str(previous) if previous else None, passages),
        SupportReply,
    )
    if isinstance(asked, Result):
        return asked
    reply, usage = asked
    plan = tuple(
        CatchUpItem(
            week=i.week,
            topic=i.topic,
            activity=i.activity,
            atp_source=i.atp_source,
            citation=i.citation,
        )
        for i in reply.items
    )
    check = check_catch_up(plan, weeks)
    failures = [] if check.passed else [check.detail]
    cited = check_citations(
        plan,
        {q: [p.citation for p in ps] for q, ps in passages.items()},
        [i.question_id for i in reply.items],
    )
    if not cited.passed:
        failures.append(f"citations: {cited.detail}")
    covered = {i.question_id for i in reply.items}
    failures.extend(
        f"{m.question_id}: no catch-up work planned" for m in found if m.question_id not in covered
    )
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
        outputs={"catch_up_plan": [item.model_dump(mode="json") for item in plan]},
        evidence=(check.detail,),
        cost_zar=usage.cost_zar,
    )


def support_brief(
    assessment_id: str,
    misconceptions: Sequence[Misconception],
    weeks: Sequence[AtpWeek],
    attempt: int = 1,
    previous_failure: str | None = None,
    budget_tokens: int | None = None,
    passages_by_question: Mapping[str, Sequence[Passage]] | None = None,
) -> Brief:
    inputs: dict[str, Any] = {
        "misconceptions": [m.model_dump(mode="json") for m in misconceptions],
        "atp_weeks_remaining": [w.model_dump(mode="json") for w in weeks],
    }
    passages = {q: ps for q, ps in (passages_by_question or {}).items() if ps}
    if passages:
        inputs["passages_by_question"] = {
            q: [p.model_dump(mode="json") for p in ps] for q, ps in passages.items()
        }
    if previous_failure:
        inputs["previous_failure"] = previous_failure
    return Brief(
        brief_id=f"{assessment_id}/class/support/{attempt}",
        assessment_id=assessment_id,
        agent=AgentRole.SUPPORT,
        task="Place catch-up teaching for each misconception in a remaining ATP week",
        inputs=inputs,
        attempt=attempt,
        budget_tokens=budget_tokens,
    )
