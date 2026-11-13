"""Learner note pages preserve feedback, privacy, and the exact vector QR grid."""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from io import BytesIO

import pytest
import segno
from pypdf import PdfReader
from reportlab.lib.units import mm

from mothusi_contracts import LearnerNote
from mothusi_outputs.pdf import learner_notes_pdf

LABELS = (
    "What this task tested:",
    "Your mark, and the work that earned it:",
    "Your next task:",
    "Ask yourself:",
)


def entry(index: int) -> tuple[str, LearnerNote, str]:
    pseudonym = f"L-{index:03}"
    # 23:00 UTC falls on the next calendar day in Johannesburg.
    expires = int(datetime(2026, 11, 26, 23, tzinfo=UTC).timestamp())
    payload = (
        urlsafe_b64encode(f"v1|assessment|{pseudonym}|{expires}".encode()).decode().rstrip("=")
    )
    token = payload + "." + urlsafe_b64encode(bytes(16)).decode().rstrip("=")
    return (
        ("Ṱhani Ḓavhana", "Zanele M", "Mpho K")[index - 1],
        LearnerNote(
            pseudonym=pseudonym,
            outcome=f"Factorising quadratic {index}",
            mark_with_quote=f'{index}/4: you wrote "(x < 3) & (x > 2)"',
            next_task=f"Exercise {index}, questions 5 to 8",
            self_assessment_prompt=f"What do you check at step {index}?",
        ),
        f"https://notes.example/n/{token}",
    )


def test_pdf_notes_pages_order_content_and_privacy() -> None:
    entries = [entry(index) for index in (3, 1, 2)]
    reader = PdfReader(BytesIO(learner_notes_pdf("Quadratics <review>", entries)))
    assert len(reader.pages) == 3
    for page, (name, note, url) in zip(reader.pages, entries, strict=True):
        assert float(page.mediabox.width) == pytest.approx(210 * mm)
        assert float(page.mediabox.height) == pytest.approx(297 * mm)
        text = page.extract_text()
        assert name in text and "Quadratics <review>" in text
        assert all(other not in text for other, _, _ in entries if other != name)
        assert note.pseudonym not in text and url not in text and "https://" not in text
        positions = [text.index(label) for label in LABELS]
        assert positions == sorted(positions)
        for value in (
            note.outcome,
            note.mark_with_quote,
            note.next_task,
            note.self_assessment_prompt,
        ):
            assert value in text
        assert " ".join(text.split()).endswith(
            "Scan to read this note on a phone. The link expires on 2026-11-27."
        )


def test_pdf_notes_vector_qr_grid_and_quiet_zone() -> None:
    entries = [entry(index) for index in (1, 2, 3)]
    reader = PdfReader(BytesIO(learner_notes_pdf("Feedback", entries)))
    for page, (_, _, url) in zip(reader.pages, entries, strict=True):
        contents = page.get_contents()
        assert contents is not None
        rectangles = [
            tuple(float(value) for value in args)
            for args, operator in contents.operations
            if operator == b"re"
        ]
        matrix = segno.make(url, error="q").matrix
        module = 35 * mm / (len(matrix) + 8)
        # 20 mm margins; the full 35 mm symbol includes four quiet modules per edge.
        expected = [
            (
                (155 * mm) + (col + 4) * module,
                20 * mm + (len(matrix) + 3 - row) * module,
                module,
                module,
            )
            for row, values in enumerate(matrix)
            for col, dark in enumerate(values)
            if dark
        ]
        assert len(rectangles) == len(expected)
        for drawn, wanted in zip(rectangles, expected, strict=True):
            assert drawn == pytest.approx(wanted, abs=0.0001)
        assert b"Do" not in [operator for _, operator in contents.operations]


@pytest.mark.parametrize("name", ["", "  \n\t"])
def test_pdf_notes_reject_empty_name(name: str) -> None:
    _, note, url = entry(1)
    with pytest.raises(ValueError, match="name"):
        learner_notes_pdf("Feedback", [(name, note, url)])


@pytest.mark.parametrize("url", ["", "http://example/n/a", "ftp://example/a", "HTTPS://example/a"])
def test_pdf_notes_reject_non_https_url(url: str) -> None:
    name, note, _ = entry(1)
    with pytest.raises(ValueError, match="https"):
        learner_notes_pdf("Feedback", [(name, note, url)])


def test_pdf_notes_reject_repeated_pseudonym() -> None:
    name, note, url = entry(1)
    with pytest.raises(ValueError, match="pseudonym"):
        learner_notes_pdf("Feedback", [(name, note, url), ("Other name", note, url)])


def test_pdf_notes_deterministic_and_empty_roster() -> None:
    entries = [entry(1), entry(2), entry(3)]
    first = learner_notes_pdf("Feedback", entries)
    learner_notes_pdf("Another title", [entry(2)])
    assert first == learner_notes_pdf("Feedback", entries)
    assert len(PdfReader(BytesIO(learner_notes_pdf("Empty", []))).pages) == 0


@pytest.mark.parametrize("url", ["https://notes.example/n/plain", "https://notes.example/n/!.tag"])
def test_pdf_notes_reject_unreadable_expiry(url: str) -> None:
    name, note, _ = entry(1)
    with pytest.raises(ValueError, match="expiry"):
        learner_notes_pdf("Feedback", [(name, note, url)])


def test_pdf_notes_long_feedback_stays_on_one_page_without_loss() -> None:
    name, note, url = entry(1)
    long_text = " ".join(f"Feedback{i}" for i in range(500))
    note = note.model_copy(update={"next_task": long_text})
    reader = PdfReader(BytesIO(learner_notes_pdf("Long feedback", [(name, note, url)])))
    assert len(reader.pages) == 1
    text = " ".join(reader.pages[0].extract_text().split())
    assert long_text in text
    assert note.self_assessment_prompt in text
    metadata = reader.metadata
    assert metadata is not None
    assert metadata.title == "Long feedback"
    assert metadata.author == "Mothusi"
    assert "/CreationDate" not in metadata and "/ModDate" not in metadata
