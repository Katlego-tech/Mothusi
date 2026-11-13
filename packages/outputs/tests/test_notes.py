"""The note has the teacher's name on it, the four parts in order, and no pseudonym."""

from __future__ import annotations

import pytest

from mothusi_contracts import LearnerNote
from mothusi_outputs import learner_note_md


def note() -> LearnerNote:
    return LearnerNote(
        pseudonym="L-007",
        outcome="Factorising quadratics",
        mark_with_quote='4/4: you wrote "(x-3)(x+2)=0" and gave both roots',
        next_task="Exercise 2.3, questions 5 to 8",
        self_assessment_prompt="What do you check first when it will not factorise?",
    )


def test_note_order_name_and_no_pseudonym() -> None:
    text = learner_note_md(note(), "  Zanele   M ")
    assert text.startswith("# Note for Zanele M\n")
    headings = (
        "**What this task tested:**",
        "**Your mark",
        "**Your next task:**",
        "**Ask yourself:**",
    )
    order = [text.index(h) for h in headings]
    assert order == sorted(order)
    assert "L-007" not in text
    assert text.endswith("factorise?\n")


def test_note_needs_a_name() -> None:
    with pytest.raises(ValueError, match="name"):
        learner_note_md(note(), "   ")
