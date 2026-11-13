"""Write the approved bundle once, preserving supplied bytes and pseudonymous paths.

The bundle is a moderation record: it is written whole or not at all. Files go into a temporary
sibling directory that is renamed into place at the end, so an error mid-way leaves nothing behind.
"""

import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from mothusi_contracts import LearnerNote
from mothusi_outputs.notes import learner_note_md

if TYPE_CHECKING:
    from mothusi_delivery.note_page import NotePage

MAX_NAME_BYTES = 200  # well under every filesystem's 255-byte limit

BUNDLE_FILES = (
    "README.md",
    "mark-sheet.csv",
    "mark-sheet.xlsx",
    "class-feedback.md",
    "catch-up-plan.md",
    "moderation-report.md",
    "moderation-report.pdf",
    "learner-notes.pdf",
)


def write_bundle(
    root: Path,
    *,
    title: str,
    mark_sheet_csv: str,
    mark_sheet_xlsx: bytes,
    class_feedback_md: str,
    catch_up_plan_md: str,
    moderation_report_md: str,
    learner_notes: Sequence[tuple[str, LearnerNote]],
    moderation_report_pdf: bytes,
    learner_notes_pdf: bytes,
    note_pages: Sequence[tuple[str, NotePage]],
) -> tuple[Path, ...]:
    """Refuse existing contents; write supplied outputs and a byte-size manifest."""
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"bundle root is not empty: {root}")
    files: dict[str, bytes] = {
        "mark-sheet.csv": mark_sheet_csv.encode("utf-8"),
        "mark-sheet.xlsx": mark_sheet_xlsx,
        "class-feedback.md": class_feedback_md.encode("utf-8"),
        "catch-up-plan.md": catch_up_plan_md.encode("utf-8"),
        "moderation-report.md": moderation_report_md.encode("utf-8"),
        "moderation-report.pdf": moderation_report_pdf,
        "learner-notes.pdf": learner_notes_pdf,
    }
    seen: set[str] = set()
    for name, note in learner_notes:
        pseudonym = note.pseudonym
        if pseudonym in seen:
            raise ValueError(f"duplicate learner pseudonym: {pseudonym}")
        _validate_pseudonym(pseudonym)
        seen.add(pseudonym)
        files[f"notes/{pseudonym}.md"] = learner_note_md(note, name).encode("utf-8")
    seen_pages: set[str] = set()
    for pseudonym, page in note_pages:
        _validate_pseudonym(pseudonym)
        if pseudonym in seen_pages:
            raise ValueError(f"duplicate learner pseudonym: {pseudonym}")
        seen_pages.add(pseudonym)
        files[f"note-pages/{pseudonym}.json"] = json.dumps(
            page.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    # Include README itself: its byte count stabilises once the number of size digits does.
    files["README.md"] = b""
    while True:
        manifest = f"# Bundle: {title}\n\n" + "".join(
            f"- {name}: {len(content)} bytes\n" for name, content in sorted(files.items())
        )
        content = manifest.encode("utf-8")
        if content == files["README.md"]:
            break
        files["README.md"] = content
    # Whole or nothing: build the bundle beside its destination, then rename it into place.
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = root.parent / f".{root.name}.writing-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        (staging / "notes").mkdir(parents=True)
        for name, content in sorted(files.items()):
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if root.exists():
            root.rmdir()  # it was checked empty above
        staging.rename(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return tuple(sorted(root / name for name in files))


def _validate_pseudonym(pseudonym: str) -> None:
    if (
        not pseudonym
        or pseudonym in (".", "..")
        or any(character in pseudonym for character in ("/", "\\", "\x00"))
        or len(pseudonym.encode("utf-8")) > MAX_NAME_BYTES
    ):
        raise ValueError(f"invalid learner pseudonym for a filename: {pseudonym!r}")
