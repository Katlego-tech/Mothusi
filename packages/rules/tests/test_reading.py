"""Acceptance tests for marks relying on confirmed-uncertain readings."""

import pytest

from mothusi_contracts import Answer, Mark, MarkedScript, Script
from mothusi_rules.reading import flag_uncertain_evidence


def make_mark(question_id: str, quote: str, awarded: int = 1) -> Mark:
    return Mark(
        question_id=question_id,
        awarded=awarded,
        max_marks=1,
        evidence_quote=quote,
        memo_line="memo",
        principle="matching evidence",
    )


def make_marked(*marks: Mark) -> MarkedScript:
    return MarkedScript(
        script_id="s-1", marker="marker", marks=marks, total=sum(m.awarded for m in marks)
    )


def make_script(*answers: Answer) -> Script:
    return Script(script_id="s-1", learner_pseudonym="L-1", answers=answers)


@pytest.mark.parametrize(
    ("line", "quote", "overlaps"),
    [
        ("x = 3", "x = 3", True),
        ("therefore x = 3", "x = 3", True),
        ("x = 3", "therefore x = 3 so done", True),
        ("abcXYZ", "XYZdef", False),
        ("abcXYZ", "defXYZghi", False),
        ("\u03b1", "\u03b1\u03b2", False),
        ("x=3", "y=2", False),
        (" 1 + 3", "area = 12 cm squared", False),
        ("x", "x = 3", True),
        ("x", "(x) + 3", True),
        ("x", "extra", False),
        ("x", "x2", False),
        ("x", "2x", False),
        ("x", "x_value", False),
        ("xy", "xy = 3", True),
        ("xy", "xyz", False),
        ("xy", "x", False),
        ("x", "x", True),
        ("\u03b1", "(\u03b1)", True),
        ("x\t =  3", "x =\n3", True),
        ("abc", "XYZ", False),
        ("X", "x", False),
        ("\u2212", "-", False),
        ("abc def", "XYZ UVW", False),  # A shared space earns no flag.
        (" \t ", "abc", False),
    ],
)
def test_overlap_definition(line: str, quote: str, overlaps: bool) -> None:
    answer = Answer(question_id="q", text=f"111\n{line}\n222", uncertain_lines=(1,))
    mark = make_mark("q", quote)
    expected = (("q", mark.evidence_quote),) if overlaps else ()
    assert flag_uncertain_evidence(make_marked(mark), make_script(answer)) == expected


def test_only_positive_marks_and_matching_question_are_considered() -> None:
    script = make_script(
        Answer(question_id="a", text="abc", uncertain_lines=(0,)),
        Answer(question_id="b", text="abc"),
    )
    marked = make_marked(make_mark("a", "abc", 0), make_mark("b", "abc"), make_mark("c", "abc"))
    assert flag_uncertain_evidence(marked, script) == ()


def test_only_indices_still_uncertain_are_considered() -> None:
    mark = make_marked(make_mark("q", "abc"))
    answer = Answer(question_id="q", text="abc\nXYZ\nabc", uncertain_lines=(1,))
    assert flag_uncertain_evidence(mark, make_script(answer)) == ()
    confirmed = Answer(question_id="q", text=answer.text, uncertain_lines=(0, 1, 2))
    assert flag_uncertain_evidence(mark, make_script(confirmed)) == (("q", "abc"),)
    corrected = Answer(question_id="q", text=answer.text)
    assert flag_uncertain_evidence(mark, make_script(corrected)) == ()


def test_cross_line_quote_returns_original_quote_once_in_mark_order() -> None:
    script = make_script(
        Answer(question_id="a", text="abc\nde\t f\nghi", uncertain_lines=(0, 1, 2)),
        Answer(question_id="z", text="123", uncertain_lines=(0,)),
    )
    marked = make_marked(make_mark("z", "123"), make_mark("a", "bc\n de\t f\ngh"))
    before = (marked.model_dump(), script.model_dump())
    expected = (("z", "123"), ("a", "bc\n de\t f\ngh"))
    assert flag_uncertain_evidence(marked, script) == expected
    assert flag_uncertain_evidence(marked, script) == expected
    assert (marked.model_dump(), script.model_dump()) == before


def test_quote_spanning_two_lines_contains_one_uncertain_line() -> None:
    script = make_script(Answer(question_id="q", text="x = 3\nso y = 4", uncertain_lines=(1,)))
    marked = make_marked(make_mark("q", "3\nso\t y = 4"))
    assert flag_uncertain_evidence(marked, script) == (("q", "3\nso\t y = 4"),)


def test_unreadable_and_missing_answers_have_no_uncertain_text() -> None:
    marked = make_marked(make_mark("q", "abc"))
    assert flag_uncertain_evidence(marked, make_script()) == ()
    assert (
        flag_uncertain_evidence(
            marked, make_script(Answer(question_id="q", text="", unreadable=True))
        )
        == ()
    )


def test_wrong_script_pairing_raises() -> None:
    script = make_script().model_copy(update={"script_id": "other"})
    with pytest.raises(ValueError, match="script_id"):
        flag_uncertain_evidence(make_marked(make_mark("q", "abc")), script)
