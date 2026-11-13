"""Learner names become L-001, L-002, ... before any agent reads a script.

Design: docs/architecture/design/intake.md §3. The order is the teacher's class-list order, so the
mapping is
reproducible from the same list, and the mapping itself is kept only on the teacher's side.
"""

from __future__ import annotations

from collections.abc import Sequence


def pseudonymise(names: Sequence[str]) -> dict[str, str]:
    """Map each name to `L-nnn` in the order given. Duplicate or blank names are a ValueError."""
    cleaned = [" ".join(name.split()) for name in names]
    if any(not name for name in cleaned):
        raise ValueError("every learner needs a name")
    if len(set(cleaned)) != len(cleaned):
        dupes = sorted({n for n in cleaned if cleaned.count(n) > 1})
        raise ValueError(f"duplicate learner names: {', '.join(dupes)}")
    width = max(3, len(str(len(cleaned))))
    return {name: f"L-{index:0{width}d}" for index, name in enumerate(cleaned, start=1)}
