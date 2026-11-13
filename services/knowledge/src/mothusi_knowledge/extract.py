"""PDF pages to text with poppler's pdftotext (knowledge.md §4.1, §6.4).

One subprocess per document, an argument list and no shell: `pdftotext -layout <file> -` writes the
pages separated by form feeds, so page n of the list is PDF page n (1-based), exactly the page a
citation names.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ExtractError(Exception):
    """A source cannot be read: the CLI exits 2 with this sentence."""


def pdf_pages(path: Path) -> tuple[str, ...]:
    if shutil.which("pdftotext") is None:
        raise ExtractError("pdftotext is not installed (poppler-utils); install it and run again")
    if not path.is_file():
        raise ExtractError(f"{path}: no such source file")
    done = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no detail"]
        raise ExtractError(f"{path}: pdftotext failed ({detail[0]})")
    pages = done.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # pdftotext ends the last page with a form feed
    return tuple(pages)
