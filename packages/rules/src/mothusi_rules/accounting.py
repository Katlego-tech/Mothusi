"""Recompute the deterministic portion of the Accounting memo's marking rules."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from mothusi_contracts import LedgerLine


def foreign_entry_penalty(lines: Sequence[LedgerLine], cap: int) -> int:
    """Deduct at most one earned mark per foreign entry, subject to the memo's cap."""
    if cap < 0:
        raise ValueError("foreign-entry cap must be non-negative")
    return min(sum(min(1, line.marks_scored) for line in lines if line.foreign), cap)


def one_part_correct(
    expected: Decimal, parts: Sequence[Decimal], operation: Literal["sum", "difference"]
) -> bool:
    """Check the final figure against the supplied parts, to half a cent."""
    if not parts:
        raise ValueError("one-part-correct needs at least one part")
    if operation == "sum":
        computed = sum(parts, Decimal(0))
    elif operation == "difference":
        computed = parts[0] - sum(parts[1:], Decimal(0))
    else:
        raise ValueError(f"unknown operation {operation!r}")
    return abs(expected - computed) <= Decimal("0.005")
