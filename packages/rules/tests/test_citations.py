"""A mark's memo citation is real, or the mark is refused (Non-negotiable I, the memo half)."""

from __future__ import annotations

from mothusi_contracts import Mark, MarkedScript, Memo
from mothusi_rules import MEMO_LINE, check_memo_lines


def test_citations_from_answer_or_notes_pass(marked: MarkedScript, memo: Memo) -> None:
    result = check_memo_lines(marked, memo)
    assert result.passed and result.check == MEMO_LINE == "memo_line"
    from_notes = marked.marks[0].model_copy(update={"memo_line": "1 mark each root"})
    assert check_memo_lines(
        marked.model_copy(update={"marks": (from_notes, marked.marks[1])}), memo
    ).passed


def test_whitespace_differences_are_tolerated(marked: MarkedScript, memo: Memo) -> None:
    spaced = marked.marks[1].model_copy(update={"memo_line": "y  =  2x  +  1"})
    assert check_memo_lines(
        marked.model_copy(update={"marks": (marked.marks[0], spaced)}), memo
    ).passed


def test_an_invented_citation_fails_naming_the_question(marked: MarkedScript, memo: Memo) -> None:
    invented = marked.marks[1].model_copy(
        update={"memo_line": "award full marks for any linear equation"}
    )
    result = check_memo_lines(
        marked.model_copy(update={"marks": (marked.marks[0], invented)}), memo
    )
    assert not result.passed
    assert result.detail == "1.2: memo line not found in the memo"


def test_zero_marks_are_not_checked_and_unknown_questions_are(memo: Memo) -> None:
    zero = Mark(
        question_id="1.1", awarded=0, max_marks=4, memo_line="anything", principle="no attempt"
    )
    unknown = Mark(
        question_id="9.9", awarded=1, max_marks=1, evidence_quote="q", memo_line="x", principle="p"
    )
    result = check_memo_lines(
        MarkedScript(script_id="s", marker="m", marks=(zero, unknown), total=1), memo
    )
    assert result.detail == "9.9: not in the memo"


# Passage citations are scoped to the question, independently of memo-line checks.
def test_catch_up_citations_are_exact_and_question_scoped() -> None:
    from mothusi_contracts import CatchUpItem
    from mothusi_rules import check_citations

    item = CatchUpItem(week=2, topic="Prisms", activity="Practise", atp_source="ATP")
    allowed = {"1": ("CAPS, PDF p 21",), "2": ("ATP, PDF p 3",)}
    for citation in ("", "CAPS, PDF p 21"):
        result = check_citations([item.model_copy(update={"citation": citation})], allowed, ["1"])
        assert result.passed and result.check == "citations"
    for citation in ("Invented, PDF p 9", "ATP, PDF p 3", "caps, PDF p 21"):
        result = check_citations([item.model_copy(update={"citation": citation})], allowed, ["1"])
        assert not result.passed and "Prisms" in result.detail
    assert not check_citations(
        [item.model_copy(update={"citation": "CAPS, PDF p 21"})], {}, ["missing"]
    ).passed
    assert check_citations([item], {}, ["missing"]).passed
    assert check_citations([], {}, []).passed


def test_catch_up_citations_checks_every_item() -> None:
    from mothusi_contracts import CatchUpItem
    from mothusi_rules import check_citations

    plan = [
        CatchUpItem(week=2, topic=topic, activity="Practise", atp_source="ATP", citation="Bad")
        for topic in ("Prisms", "Circles")
    ]
    result = check_citations(plan, {}, ["1", "2"])
    assert not result.passed
    assert "Prisms" in result.detail and "Circles" in result.detail
