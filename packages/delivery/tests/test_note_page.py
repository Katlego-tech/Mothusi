"""The public page contains the four note parts and no learner identity."""

from datetime import UTC, datetime
from html import escape

import pytest
from pydantic import ValidationError

from mothusi_contracts import Contract, LearnerNote
from mothusi_delivery import NotePage, note_page, note_page_html

EXPIRY = datetime(2026, 11, 25, 12, tzinfo=UTC)


def learner_note() -> LearnerNote:
    return LearnerNote(
        pseudonym="L007-private",
        outcome="Compare values < 10 & > 2",
        mark_with_quote='2/3 for "x < 10"',
        next_task="Try <script>alert('x')</script>",
        self_assessment_prompt="Why < and not >?",
    )


def test_note_page_copies_only_the_named_fields_and_round_trips_json() -> None:
    note = learner_note()
    page = note_page(note, "Maths <revision>", EXPIRY)
    assert isinstance(page, Contract)
    assert page.model_dump() == {
        "assessment_title": "Maths <revision>",
        "outcome": note.outcome,
        "mark_with_quote": note.mark_with_quote,
        "next_task": note.next_task,
        "self_assessment_prompt": note.self_assessment_prompt,
        "expires_at": EXPIRY,
    }
    assert NotePage.model_validate_json(page.model_dump_json()) == page
    with pytest.raises(ValidationError):
        page.model_copy().assessment_title = "Changed"
    with pytest.raises(ValidationError):
        NotePage.model_validate({**page.model_dump(), "name": "Private learner name"})


def test_note_page_rejects_naive_expiry() -> None:
    with pytest.raises(ValueError):
        note_page(learner_note(), "Maths", EXPIRY.replace(tzinfo=None))


def test_note_page_html_is_complete_private_and_escaped() -> None:
    note = learner_note()
    page = note_page(note, 'Maths <test> & "revision"', EXPIRY)
    document = note_page_html(page)
    assert document.lower().startswith("<!doctype html>")
    assert '<html lang="en">' in document
    assert "</html>" in document
    assert '<meta name="robots" content="noindex">' in document
    for forbidden in [note.pseudonym, "Private learner name", "<script", "http", "<img", "<link"]:
        assert forbidden not in document
    for value in [
        page.assessment_title,
        note.outcome,
        note.mark_with_quote,
        note.next_task,
        note.self_assessment_prompt,
    ]:
        assert escape(value) in document
    labels = [
        "What this task tested:",
        "Your mark, and the work that earned it:",
        "Your next task:",
        "Ask yourself:",
    ]
    positions = [document.index(label) for label in labels]
    assert positions == sorted(positions)
