"""Approved bundles preserve their supplied content and never overwrite an earlier record."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mothusi_contracts import LearnerNote
from mothusi_delivery.note_page import NotePage, note_page
from mothusi_outputs import BUNDLE_FILES, write_bundle


def note(pseudonym: str = "L-001") -> LearnerNote:
    return LearnerNote(
        pseudonym=pseudonym,
        outcome="Solve equations",
        mark_with_quote='2/2 for "x=3"',
        next_task="Solve x+1=4",
        self_assessment_prompt="How did you check your answer?",
    )


def write(
    root: Path,
    notes: tuple[tuple[str, LearnerNote], ...] = (),
    pages: tuple[tuple[str, NotePage], ...] = (),
) -> tuple[Path, ...]:
    return write_bundle(
        root,
        title="Test bundle",
        mark_sheet_csv="learner,raw\r\nA,2\r\n",
        mark_sheet_xlsx=b"unit-test spreadsheet bytes",
        class_feedback_md="Feedback\n",
        catch_up_plan_md="Plan\r\n",
        moderation_report_md="Report\n",
        learner_notes=notes,
        moderation_report_pdf=b"report PDF bytes\x00\xff",
        learner_notes_pdf=b"notes PDF bytes\x00\xff",
        note_pages=pages,
    )


def test_files_content_manifest_and_sorted_paths(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    paths = write(root, (("Teacher-side name", note()),))
    assert paths == tuple(sorted(paths))
    assert set(BUNDLE_FILES) == {
        "README.md",
        "mark-sheet.csv",
        "mark-sheet.xlsx",
        "class-feedback.md",
        "catch-up-plan.md",
        "moderation-report.md",
        "moderation-report.pdf",
        "learner-notes.pdf",
    }
    assert {p.relative_to(root).as_posix() for p in paths} == set(BUNDLE_FILES) | {"notes/L-001.md"}
    assert (root / "mark-sheet.csv").read_bytes() == b"learner,raw\r\nA,2\r\n"
    assert (root / "mark-sheet.xlsx").read_bytes() == b"unit-test spreadsheet bytes"
    assert (root / "class-feedback.md").read_bytes() == b"Feedback\n"
    assert (root / "catch-up-plan.md").read_bytes() == b"Plan\r\n"
    assert (root / "moderation-report.md").read_bytes() == b"Report\n"
    text = (root / "notes/L-001.md").read_text()
    assert "Teacher-side name" in text and "L-001" not in text
    manifest = (root / "README.md").read_text()
    assert "Bundle: Test bundle" in manifest
    for path in paths:
        assert path.relative_to(root).as_posix() in manifest
        assert f"{path.stat().st_size} bytes" in manifest
    assert (root / "notes").is_dir()


def test_empty_existing_root_and_empty_notes(tmp_path: Path) -> None:
    paths = write(tmp_path)
    assert len(paths) == len(BUNDLE_FILES) and (tmp_path / "notes").is_dir()


def test_nonempty_root_is_unchanged(tmp_path: Path) -> None:
    sentinel = tmp_path / "existing"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ValueError, match="bundle root is not empty"):
        write(tmp_path)
    assert list(tmp_path.iterdir()) == [sentinel] and sentinel.read_bytes() == b"keep"


def test_second_write_refused(tmp_path: Path) -> None:
    write(tmp_path)
    with pytest.raises(ValueError, match="bundle root is not empty"):
        write(tmp_path)


def test_duplicate_pseudonyms_fail_before_writing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    with pytest.raises(ValueError, match="duplicate"):
        write(root, (("A", note()), ("B", note())))
    assert not root.exists()


@pytest.mark.parametrize("pseudonym", ["../escape", "/absolute", "a/b", "a\\b", ".", ".."])
def test_pseudonym_cannot_escape_notes(tmp_path: Path, pseudonym: str) -> None:
    root = tmp_path / "bundle"
    with pytest.raises(ValueError):
        write(root, (("A", note(pseudonym)),))
    assert not root.exists()


def test_an_over_long_pseudonym_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    with pytest.raises(ValueError, match="invalid learner pseudonym"):
        write(root, (("A", note("L" * 300)),))
    assert not root.exists() and list(tmp_path.iterdir()) == []


def test_a_failure_mid_write_leaves_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is whole or absent: a retry after an OS error must not meet 'not empty'."""
    root = tmp_path / "bundle"
    real = Path.write_bytes

    def failing(self: Path, data: bytes) -> int:
        if self.name == "moderation-report.md":
            raise OSError(28, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing)
    with pytest.raises(OSError):
        write(root)
    monkeypatch.undo()
    assert not root.exists() and list(tmp_path.iterdir()) == []
    assert len(write(root)) == len(BUNDLE_FILES)


def test_pdf_bytes_and_sorted_utf8_note_pages(tmp_path: Path) -> None:
    page = note_page(note(), "Équations", datetime(2026, 11, 24, tzinfo=UTC))
    paths = write(tmp_path, (("Teacher-side name", note()),), (("L-001", page),))
    assert (tmp_path / "moderation-report.pdf").read_bytes() == b"report PDF bytes\x00\xff"
    assert (tmp_path / "learner-notes.pdf").read_bytes() == b"notes PDF bytes\x00\xff"
    content = (tmp_path / "note-pages/L-001.json").read_bytes()
    data = json.loads(content)
    assert data == page.model_dump(mode="json")
    assert list(data) == sorted(data)
    assert "Équations".encode() in content
    assert b"Teacher-side name" not in content and b"L-001" not in content
    assert NotePage.model_validate_json(content) == page
    assert paths == tuple(sorted(paths))
    manifest = (tmp_path / "README.md").read_text()
    assert f"note-pages/L-001.json: {len(content)} bytes" in manifest


@pytest.mark.parametrize(
    "pseudonym", ["../escape", "/absolute", "a/b", "a\\b", ".", "..", "", "L" * 300]
)
def test_note_page_pseudonyms_validated_before_writing(tmp_path: Path, pseudonym: str) -> None:
    root = tmp_path / "bundle"
    page = note_page(note(), "Title", datetime(2026, 11, 24, tzinfo=UTC))
    with pytest.raises(ValueError, match="invalid learner pseudonym"):
        write(root, pages=((pseudonym, page),))
    assert not root.exists() and list(tmp_path.iterdir()) == []


def test_duplicate_note_pages_refused(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    page = note_page(note(), "Title", datetime(2026, 11, 24, tzinfo=UTC))
    with pytest.raises(ValueError, match="duplicate"):
        write(root, pages=(("L-001", page), ("L-001", page)))
    assert not root.exists()
