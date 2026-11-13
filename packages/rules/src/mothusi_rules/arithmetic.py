"""Level-A checks on a marked script: the totals add up and every mark is in range.

Design: docs/design/rules-engine.md §3 (check_totals, check_bounds). Pure functions over the
contracts; they never change a mark, they only say whether one is wrong and why.
"""

from __future__ import annotations

from mothusi_contracts import CheckResult, MarkedScript, Memo

TOTALS = "totals"
BOUNDS = "bounds"


def check_totals(marked: MarkedScript) -> CheckResult:
    """The recorded total equals the sum of the marks awarded."""
    summed = sum(mark.awarded for mark in marked.marks)
    if summed == marked.total:
        return CheckResult(
            check=TOTALS,
            about=marked.script_id,
            passed=True,
            detail=f"{len(marked.marks)} marks add up to the recorded total of {marked.total}",
        )
    return CheckResult(
        check=TOTALS,
        about=marked.script_id,
        passed=False,
        detail=f"marks add up to {summed} but the recorded total is {marked.total}",
    )


def check_bounds(marked: MarkedScript, memo: Memo) -> CheckResult:
    """Every memo question is marked once, within the memo maximum, and nothing else is."""
    expected = {item.question_id: item.max_marks for item in memo.items}
    seen: dict[str, int] = {}
    problems: list[str] = []
    for mark in marked.marks:
        seen[mark.question_id] = seen.get(mark.question_id, 0) + 1
        if mark.question_id not in expected:
            problems.append(f"{mark.question_id}: not in the memo")
            continue
        if mark.max_marks != expected[mark.question_id]:
            problems.append(
                f"{mark.question_id}: marked out of {mark.max_marks}, "
                f"memo says {expected[mark.question_id]}"
            )
        if mark.awarded > expected[mark.question_id]:
            problems.append(
                f"{mark.question_id}: awarded {mark.awarded} of {expected[mark.question_id]}"
            )
    problems.extend(f"{qid}: marked {n} times" for qid, n in seen.items() if n > 1)
    problems.extend(f"{qid}: not marked" for qid in expected if qid not in seen)
    if problems:
        return CheckResult(
            check=BOUNDS, about=marked.script_id, passed=False, detail="; ".join(sorted(problems))
        )
    return CheckResult(
        check=BOUNDS,
        about=marked.script_id,
        passed=True,
        detail=f"all {len(expected)} memo questions are marked once, within their maximums",
    )


def check_script(marked: MarkedScript, memo: Memo) -> tuple[CheckResult, CheckResult]:
    """Both arithmetic checks, in the order a moderator reads them: bounds first, then totals."""
    return check_bounds(marked, memo), check_totals(marked)
