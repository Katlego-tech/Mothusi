"""The Moderator: a blind second marking of a sample, the way a head of department moderates.

Design: docs/design/marking-pipeline.md §4 steps 14-19; the `moderator` contract. The Moderator is
the Marker's loop run under a different role: same rules checks, same retries, but its Brief never
carries first marks (check_brief_blind is run on every Brief before dispatch) and it signs as
"moderator". After the sample, `compare` turns the pairs into Disagreements and the widening call.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mothusi_contracts import (
    AgentRole,
    Brief,
    CheckResult,
    Disagreement,
    MarkedScript,
    Memo,
    Script,
    SubjectPack,
)
from mothusi_core.agents.marker import REPLY_BUDGET_TOKENS, Attempt, MarkingOutcome, mark_script
from mothusi_llm import Provider
from mothusi_rules import check_brief_blind, disagreements, moderation_sample, should_widen


def sample_for_moderation(
    marked: Sequence[MarkedScript], pack: SubjectPack, *, seed: int = 0
) -> tuple[str, ...]:
    """Which scripts the Moderator re-marks first (rules-engine.md §3 moderation_sample)."""
    return moderation_sample(marked, pack.moderation, seed)


def blind_check(brief: Brief, names: Sequence[str]) -> CheckResult:
    """The guard the Planner runs before dispatching a moderator Brief."""
    return check_brief_blind(brief, names)


async def moderate_script(
    memo: Memo,
    script: Script,
    pack: SubjectPack,
    provider: Provider,
    *,
    assessment_id: str,
    max_attempts: int = 3,
    budget_tokens: int | None = REPLY_BUDGET_TOKENS,
    on_brief: Callable[[Brief], None] | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> MarkingOutcome:
    """Blind second marking of one script: the Marker's loop under the moderator role."""
    return await mark_script(
        memo,
        script,
        pack,
        provider,
        assessment_id=assessment_id,
        max_attempts=max_attempts,
        budget_tokens=budget_tokens,
        role=AgentRole.MODERATOR,
        on_brief=on_brief,
        on_attempt=on_attempt,
    )


def compare(
    first: Sequence[MarkedScript], second: Sequence[MarkedScript], pack: SubjectPack
) -> tuple[tuple[Disagreement, ...], int, bool]:
    """Disagreements across the sample, how many questions were compared, and whether to widen.

    `first` and `second` are matched by script_id; a second marking with no first (or the reverse)
    is a ValueError, as is a pair that marks different questions (rules-engine.md §3).
    """
    by_id = {m.script_id: m for m in first}
    found: list[Disagreement] = []
    compared = 0
    for re_marked in second:
        original = by_id.get(re_marked.script_id)
        if original is None:
            raise ValueError(f"{re_marked.script_id}: moderated but never marked")
        found.extend(disagreements(original, re_marked))
        compared += len(re_marked.marks)
    widen = bool(found) and should_widen(found, compared, pack.moderation)
    return tuple(found), compared, widen
