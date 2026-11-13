"""The retry loop around the Analyst, Support and Writer (marking-pipeline.md §6.1).

An agent's entry point runs its own code checks and returns `retry` with the failed check's detail
as `reason`; this loop re-briefs with that detail as `previous_failure`. Three attempts in all
("two retries", §4), then the teacher decides. Nothing is silently accepted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from mothusi_contracts import AgentRole, Brief, Contract, Result, ResultStatus
from mothusi_core.agents.marker import Attempt
from mothusi_llm import Provider, ProviderError, Usage

type BriefFor = Callable[[int, str | None], Brief]
"""(attempt, previous_failure) -> the Brief for that attempt."""

type Run = Callable[[Brief], Awaitable[Result]]


class AgentOutcome(Contract):
    """The loop's verdict for one piece of agent work."""

    status: Literal["done", "needs_teacher", "blocked"]
    attempts: tuple[Attempt, ...] = Field(min_length=1)
    outputs: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    cost_zar: Decimal = Field(ge=0, default=Decimal(0))


def refuse(brief: Brief, role: AgentRole, reason: str) -> Result:
    """A `blocked` Result: the Brief cannot be worked as given."""
    return Result(brief_id=brief.brief_id, agent=role, status=ResultStatus.BLOCKED, reason=reason)


def wrong_role(brief: Brief, role: AgentRole) -> Result | None:
    if brief.agent is role:
        return None
    return refuse(brief, role, f"brief is for {brief.agent.value}, not the {role.value}")


async def ask[T: Contract](
    provider: Provider, brief: Brief, role: AgentRole, prompts: tuple[str, str], output: type[T]
) -> tuple[T, Usage] | Result:
    """One model call; a ProviderError becomes `retry` (retryable) or `blocked`."""
    system, user = prompts
    try:
        return await provider.complete(system, user, output, budget_tokens=brief.budget_tokens)
    except ProviderError as error:
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.RETRY if error.retryable else ResultStatus.BLOCKED,
            reason=f"{provider.name}: {error}",
        )


def normalised(text: str) -> str:
    """Whitespace-normalised text, the way check_evidence compares quotes."""
    return " ".join(text.split())


async def run_with_retries(
    brief_for: BriefFor,
    run: Run,
    *,
    max_attempts: int = 3,
    on_brief: Callable[[Brief], None] | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> AgentOutcome:
    """Run, feed a `retry` reason into the next Brief, stop on done or blocked or at the limit."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    attempts: list[Attempt] = []
    cost = Decimal(0)
    previous: str | None = None
    for n in range(1, max_attempts + 1):
        brief = brief_for(n, previous)
        if on_brief is not None:
            on_brief(brief)
        result = await run(brief)
        cost += result.cost_zar
        attempts.append(Attempt(brief=brief, result=result))
        if on_attempt is not None:
            on_attempt(attempts[-1])
        if result.status is ResultStatus.DONE:
            return AgentOutcome(
                status="done", attempts=tuple(attempts), outputs=result.outputs, cost_zar=cost
            )
        if result.status is ResultStatus.BLOCKED:
            return AgentOutcome(
                status="blocked", attempts=tuple(attempts), reason=result.reason, cost_zar=cost
            )
        if result.status is ResultStatus.NEEDS_TEACHER:
            return AgentOutcome(
                status="needs_teacher",
                attempts=tuple(attempts),
                reason=result.reason,
                cost_zar=cost,
            )
        previous = result.reason
    agent = attempts[-1].brief.agent.value
    return AgentOutcome(
        status="needs_teacher",
        attempts=tuple(attempts),
        reason=(
            f"{agent}: {max_attempts} attempts did not pass the checks; last failure: {previous}"
        ),
        cost_zar=cost,
    )
