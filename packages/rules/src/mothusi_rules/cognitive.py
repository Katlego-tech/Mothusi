"""Compare the memo's cognitive spread with the curriculum pack."""

from decimal import ROUND_HALF_UP, Decimal

from mothusi_contracts import CheckResult, Memo, SubjectPack

COGNITIVE_GRID = "cognitive_grid"


def check_cognitive_grid(memo: Memo, pack: SubjectPack) -> CheckResult:
    # A pack without a grid cannot be checked; the Planner logs "check skipped" instead of calling.
    if pack.cognitive is None:
        raise ValueError(f"pack {pack.pack_id}/{pack.subject}/gr{pack.grade} has no cognitive grid")
    details: list[str] = []
    passed = True
    for level, required in pack.cognitive.levels.items():
        marks = sum(item.max_marks for item in memo.items if item.level == level)
        share = (Decimal(marks) / memo.total_marks * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        off = abs(share - required) > pack.cognitive.tolerance_points
        passed = passed and not off
        details.append(f"{level}: {share}% (target {required}%)" + (" OFF" if off else ""))
    for item in memo.items:
        if item.level not in pack.cognitive.levels:
            passed = False
            details.append(f"unknown level '{item.level}' on {item.question_id}")
    return CheckResult(
        check=COGNITIVE_GRID, about=memo.assessment_id, passed=passed, detail="; ".join(details)
    )
