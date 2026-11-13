"""A learner note must be grounded in that script and must not label the learner."""

import pytest

from mothusi_contracts import LearnerNote, MarkedScript, Script
from mothusi_rules import NOTE_GROUNDING, check_note_grounding


def note(
    text: str = '10/10 for "(x-3)(x+2)=0"',
    outcome: str = "Factorising",
    next_task: str = "Solve another quadratic",
) -> LearnerNote:
    return LearnerNote(
        pseudonym="L-014",
        outcome=outcome,
        mark_with_quote=text,
        next_task=next_task,
        self_assessment_prompt="How did you check both roots?",
    )


def test_grounded_total_and_item_marks(marked: MarkedScript, script: Script) -> None:
    for text in (
        '10/10 for "(x-3)(x+2)=0"',
        "4/4 for “(x-3)(x+2)=0”",
        '4/4 for "(x-3)(x+2)=0   so x=3"; 6/6 for “y=2x+1”',
    ):
        result = check_note_grounding(note(text), script, marked)
        assert result.passed and result.check == NOTE_GROUNDING == "note_grounding"
        assert result.about == "L-014"


def test_invented_quotes_marks_and_sorted_failures(marked: MarkedScript, script: Script) -> None:
    result = check_note_grounding(note('5/6 for "invented"'), script, marked)
    assert not result.passed
    assert (
        result.detail
        == 'mark 5/6 is not in the marked script; quote not found in the script: "invented"'
    )


def test_case_sensitive_quote(marked: MarkedScript, script: Script) -> None:
    result = check_note_grounding(note('6/6 for "Y=2x+1"'), script, marked)
    assert result.detail == 'quote not found in the script: "Y=2x+1"'


@pytest.mark.parametrize(
    "label",
    [
        "is weak at algebra",
        "are poor",
        "was slow",
        "were lazy",
        "is bad",
        "is hopeless",
        "is careless",
        "better than",
        "worse than",
        "slower than",
        "faster than",
        "top of the class",
        "bottom of the class",
        "not clever",
        "not smart",
        "not bright",
        "not good at",
        "cannot do",
        "can't do",
        "can not do",
    ],
)
def test_all_label_patterns(marked: MarkedScript, script: Script, label: str) -> None:
    for field in ("outcome", "mark_with_quote", "next_task"):
        original = note()
        changed = original.model_copy(update={field: getattr(original, field) + " " + label})
        result = check_note_grounding(changed, script, marked)
        assert not result.passed and "labels the learner:" in result.detail


def test_labels_ignore_case(marked: MarkedScript, script: Script) -> None:
    assert (
        "labels the learner: 'IS WEAK'"
        in check_note_grounding(note(outcome="IS WEAK"), script, marked).detail
    )


@pytest.mark.parametrize("text", ["well done", "Excellent", "BRILLIANT", 'well done ""'])
def test_ungrounded_praise(marked: MarkedScript, script: Script, text: str) -> None:
    result = check_note_grounding(note(text), script, marked)
    assert result.detail == "no quote from the learner's work; praise without a quote"


def test_praise_with_evidence_and_missing_quote(marked: MarkedScript, script: Script) -> None:
    assert check_note_grounding(note('Well done: 4/4 for "(x-3)(x+2)=0"'), script, marked).passed
    assert (
        check_note_grounding(note("10/10"), script, marked).detail
        == "no quote from the learner's work"
    )


def test_pairing_mismatch(marked: MarkedScript, script: Script) -> None:
    with pytest.raises(ValueError):
        check_note_grounding(note(), script, marked.model_copy(update={"script_id": "other"}))
    with pytest.raises(ValueError):
        check_note_grounding(note().model_copy(update={"pseudonym": "other"}), script, marked)


@pytest.mark.parametrize(
    "text", ['2 / 2 for "(x-3)(x+2)=0"', '2 out of 2 for "(x-3)(x+2)=0"', '50% for "(x-3)(x+2)=0"']
)
def test_marks_in_other_forms_are_checked(marked: MarkedScript, script: Script, text: str) -> None:
    """Reviewer: '2 / 2', '2 out of 2' and '50%' had slipped past the n/m pattern."""
    result = check_note_grounding(note(text), script, marked)
    assert not result.passed and "is not in the marked script" in result.detail


def test_true_percentages_and_guillemet_quotes_pass(marked: MarkedScript, script: Script) -> None:
    assert check_note_grounding(note("100% for «(x-3)(x+2)=0»"), script, marked).passed
    assert check_note_grounding(note("4/4 for `(x-3)(x+2)=0`"), script, marked).passed


@pytest.mark.parametrize("label", ["is  weak", "can\u2019t do", "not good  at"])
def test_labels_with_odd_spacing_or_apostrophes(
    marked: MarkedScript, script: Script, label: str
) -> None:
    assert "labels the learner:" in check_note_grounding(note(outcome=label), script, marked).detail
