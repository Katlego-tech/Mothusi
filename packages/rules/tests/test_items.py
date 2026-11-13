"""Contract acceptance tests for the delegated rules task."""

from decimal import Decimal

import pytest

from mothusi_contracts import Mark, MarkedScript, Memo, MemoItem
from mothusi_rules import item_analysis


def one(awarded: int, maximum: int = 10000) -> tuple[Memo, MarkedScript]:
    memo = Memo(
        assessment_id="a",
        total_marks=maximum,
        items=(MemoItem(question_id="q", max_marks=maximum, answer="a", level="k"),),
    )
    marked = MarkedScript(
        script_id="s",
        marker="m",
        total=awarded,
        marks=(
            Mark(
                question_id="q",
                awarded=awarded,
                max_marks=maximum,
                evidence_quote="a" if awarded else "",
                memo_line="m",
                principle="p",
            ),
        ),
    )
    return memo, marked


@pytest.mark.parametrize(
    ("awarded", "facility", "flag"),
    [
        (4000, "40.00", None),
        (3999, "39.99", "re-teach"),
        (9500, "95.00", None),
        (9501, "95.01", "review-item"),
        (0, "0.00", "re-teach"),
        (10000, "100.00", "review-item"),
    ],
)
def test_thresholds(awarded: int, facility: str, flag: str | None) -> None:
    memo, marked = one(awarded)
    (stat,) = item_analysis((marked,), memo)
    assert stat.facility_percent == Decimal(facility)
    assert stat.flag == flag
    assert stat.scripts == 1 and stat.max_marks == 10000


def test_memo_order_and_partial_scripts(marked: MarkedScript, memo: Memo) -> None:
    partial = marked.model_copy(
        update={"marks": (marked.marks[0].model_copy(update={"awarded": 1}),)}
    )
    stats = item_analysis((marked, partial), memo)
    assert [s.question_id for s in stats] == ["1.1", "1.2"]
    assert [s.scripts for s in stats] == [2, 1]
    assert [s.mean_awarded for s in stats] == [Decimal("2.50"), Decimal("6.00")]
    assert stats[0].facility_percent == Decimal("62.50")


def test_rounding_stages() -> None:
    memo, marked = one(1, 3)
    zero = marked.model_copy(update={"marks": (marked.marks[0].model_copy(update={"awarded": 0}),)})
    (stat,) = item_analysis((marked, zero, zero, zero, zero, zero), memo)
    assert stat.mean_awarded == Decimal("0.17")
    assert stat.facility_percent == Decimal("5.67")


def test_empty_and_unknown(marked: MarkedScript, memo: Memo) -> None:
    with pytest.raises(ValueError):
        item_analysis((), memo)
    unknown = marked.marks[0].model_copy(update={"question_id": "unknown"})
    with pytest.raises(ValueError, match="unknown"):
        item_analysis((marked.model_copy(update={"marks": (unknown,)}),), memo)
