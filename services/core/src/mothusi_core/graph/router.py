"""The transitions of docs/design/marking-pipeline.md §5, and nothing else.

Every node of the Planner graph changes state only through `transition`, which refuses an edge §5
does not draw. In particular nothing reaches `delivered` except from `approved`, and nothing
reaches `approved` except the teacher's approval at `awaiting_approval` (Non-negotiable I).
"""

from __future__ import annotations

from mothusi_contracts import AssessmentState as S

ALLOWED: frozenset[tuple[S, S]] = frozenset(
    {
        (S.DRAFT, S.MEMO_CHECK),
        (S.DRAFT, S.READING_CHECK),
        (S.READING_CHECK, S.MEMO_CHECK),
        (S.MEMO_CHECK, S.MARKING),
        (S.MEMO_CHECK, S.DRAFT),
        (S.MARKING, S.MODERATING),
        (S.MODERATING, S.RE_MARKING),
        (S.MODERATING, S.ANALYSING),
        (S.RE_MARKING, S.MODERATING),
        (S.ANALYSING, S.WRITING),
        (S.WRITING, S.REVIEWING),
        (S.REVIEWING, S.WRITING),
        (S.REVIEWING, S.AWAITING_APPROVAL),
        (S.AWAITING_APPROVAL, S.APPROVED),
        (S.AWAITING_APPROVAL, S.REJECTED),
        (S.REJECTED, S.WRITING),
        (S.APPROVED, S.DELIVERED),
    }
)


class IllegalTransitionError(ValueError):
    """An edge marking-pipeline.md §5 does not draw."""


def check_transition(current: S, to: S) -> None:
    if (current, to) not in ALLOWED:
        raise IllegalTransitionError(f"no transition {current.value} -> {to.value} in §5")


def reachable(start: S, avoiding: S) -> set[S]:
    """States reachable from start without passing through `avoiding` (for the gate's proof)."""
    seen: set[S] = set()
    frontier = [start]
    while frontier:
        state = frontier.pop()
        for a, b in ALLOWED:
            if a is state and b is not avoiding and b not in seen:
                seen.add(b)
                frontier.append(b)
    return seen
