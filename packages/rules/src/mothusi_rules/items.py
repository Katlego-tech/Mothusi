"""Class item statistics, in memo order, with teacher review flags."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from mothusi_contracts import ItemStat, MarkedScript, Memo


def item_analysis(marked: Sequence[MarkedScript], memo: Memo) -> tuple[ItemStat, ...]:
    if not marked:
        raise ValueError("item analysis needs at least one marked script")
    awards: dict[str, list[int]] = {item.question_id: [] for item in memo.items}
    for script in marked:
        by_question = {mark.question_id: mark.awarded for mark in script.marks}
        for qid, awarded in by_question.items():
            if qid not in awards:
                raise ValueError(f"{script.script_id}: unknown question {qid}")
            awards[qid].append(awarded)
    stats: list[ItemStat] = []
    for item in memo.items:
        values = awards[item.question_id]
        if not values:
            raise ValueError(f"{item.question_id}: no marked scripts for this question")
        mean = (Decimal(sum(values)) / len(values)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        facility = (mean / item.max_marks * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        stats.append(
            ItemStat(
                question_id=item.question_id,
                max_marks=item.max_marks,
                scripts=len(values),
                mean_awarded=mean,
                facility_percent=facility,
                flag="re-teach" if facility < 40 else "review-item" if facility > 95 else None,
            )
        )
    return tuple(stats)
