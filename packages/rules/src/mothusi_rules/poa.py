"""Convert raw marks to SBA and year points using the pack's weights."""

from decimal import ROUND_HALF_UP, Decimal

from mothusi_contracts import ConvertedMark, PoATask, SubjectPack


def find_task(pack: SubjectPack, name: str) -> PoATask:
    for task in pack.poa:
        if task.name == name:
            return task
    raise ValueError(
        f"unknown task {name!r}; available tasks: {', '.join(task.name for task in pack.poa)}"
    )


def convert_mark(script_id: str, raw: int, task: PoATask, pack: SubjectPack) -> ConvertedMark:
    if raw < 0 or raw > task.max_marks:
        raise ValueError(f"raw mark {raw} must be between 0 and {task.max_marks}")
    sba = (Decimal(raw) / task.max_marks * task.weight_percent).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    year = (sba * pack.sba_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ConvertedMark(
        script_id=script_id, raw=raw, out_of=task.max_marks, sba_points=sba, year_points=year
    )
