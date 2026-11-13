"""Load sourced ATP weeks and check that catch-up activities fit those remaining."""

import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from mothusi_contracts import AtpWeek, CatchUpItem, CheckResult

CATCH_UP = "catch_up_weeks"


def load_atp_weeks(path: Path) -> tuple[AtpWeek, ...]:
    """Validate ATP week tables, reject duplicates, and return chronological order."""
    with path.open("rb") as source:
        data = tomllib.load(source)
    if "week" not in data:
        raise ValueError("ATP file must contain week tables")
    weeks = TypeAdapter(tuple[AtpWeek, ...]).validate_python(data["week"])
    seen: set[int] = set()
    for week in weeks:
        if week.week in seen:
            raise ValueError(f"duplicate ATP week {week.week}")
        seen.add(week.week)
    return tuple(sorted(weeks, key=lambda week: week.week))


def check_catch_up(plan: Sequence[CatchUpItem], remaining: Sequence[AtpWeek]) -> CheckResult:
    """Report every unavailable week or mismatched source without moving activities."""
    weeks = {week.week: week for week in remaining}
    failures: list[str] = []
    for item in plan:
        if item.week not in weeks:
            failures.append(f"{item.topic}: week {item.week} is not in the remaining ATP")
        elif item.atp_source != weeks[item.week].source:
            failures.append(f"{item.topic}: source does not match week {item.week}")
    return CheckResult(
        check=CATCH_UP,
        about="catch-up plan",
        passed=not failures,
        detail="; ".join(failures)
        or f"{len(plan)} items placed in {len(remaining)} remaining weeks",
    )
