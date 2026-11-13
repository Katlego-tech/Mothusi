"""US3: the arithmetic checks catch what a tired marker misses."""

from __future__ import annotations

from mothusi_contracts import Mark, MarkedScript, Memo
from mothusi_rules import check_bounds, check_script, check_totals


def _mark(qid: str, awarded: int, max_marks: int) -> Mark:
    return Mark(
        question_id=qid,
        awarded=awarded,
        max_marks=max_marks,
        evidence_quote="q" if awarded else "",
        memo_line="m",
        principle="p",
    )


def test_a_correct_script_passes_both_checks(marked: MarkedScript, memo: Memo) -> None:
    bounds, totals = check_script(marked, memo)
    assert bounds.passed and totals.passed
    assert bounds.about == totals.about == "s-014"


def test_totals_catch_a_wrong_sum(marked: MarkedScript) -> None:
    wrong = marked.model_copy(update={"total": 9})
    result = check_totals(wrong)
    assert not result.passed
    assert result.detail == "marks add up to 10 but the recorded total is 9"


def test_bounds_catch_a_mark_over_the_memo_maximum(memo: Memo) -> None:
    marked = MarkedScript(
        script_id="s", marker="m", marks=(_mark("1.1", 4, 5), _mark("1.2", 6, 6)), total=10
    )
    result = check_bounds(marked, memo)
    assert not result.passed
    assert "1.1: marked out of 5, memo says 4" in result.detail


def test_bounds_catch_a_missing_and_an_unknown_question(memo: Memo) -> None:
    marked = MarkedScript(
        script_id="s", marker="m", marks=(_mark("1.1", 4, 4), _mark("9.9", 0, 1)), total=4
    )
    result = check_bounds(marked, memo)
    assert not result.passed
    assert result.detail == "1.2: not marked; 9.9: not in the memo"


def test_bounds_catch_a_question_marked_twice(memo: Memo) -> None:
    marked = MarkedScript(
        script_id="s",
        marker="m",
        marks=(_mark("1.1", 2, 4), _mark("1.1", 2, 4), _mark("1.2", 6, 6)),
        total=10,
    )
    result = check_bounds(marked, memo)
    assert not result.passed
    assert "1.1: marked 2 times" in result.detail
