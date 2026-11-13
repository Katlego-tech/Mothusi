"""Extract the text layer of a teacher's typed memo, without OCR or model calls."""

import re
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.table import CT_Tc
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

MAX_MEMO_BYTES = 10 * 1024 * 1024


def _table_lines(table: Table) -> Iterator[str]:
    """Read each physical cell once and keep nested rows on separate lines."""
    seen: set[CT_Tc] = set()
    for row in table.rows:
        parts: list[str] = []
        for cell in row.cells:
            # python-docx repeats the same XML cell for horizontal and vertical spans.
            tc = cell._tc  # pyright: ignore[reportPrivateUsage]
            if tc in seen:
                continue
            seen.add(tc)
            paragraphs: list[str] = []
            has_nested_table = False
            for block in cell.iter_inner_content():
                if isinstance(block, Paragraph):
                    paragraphs.append(block.text)
                else:
                    has_nested_table = True
                    text = "\n".join(paragraphs).strip("\n")
                    if text.strip():
                        parts.append(text)
                    paragraphs.clear()
                    if parts:
                        yield " | ".join(parts)
                        parts.clear()
                    yield from _table_lines(block)
            text = "\n".join(paragraphs)
            if has_nested_table:
                text = text.strip("\n")
            # Word requires empty paragraphs around nested tables; those are not cells.
            if text.strip() or not has_nested_table:
                parts.append(text)
        if parts:
            yield " | ".join(parts)


def memo_text(data: bytes, filename: str) -> str:
    """Read a typed PDF or Word memo and report unusable uploads to the teacher."""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise ValueError(f"{filename}: upload a .pdf or .docx memo; a .doc must be saved as .docx.")
    if len(data) > MAX_MEMO_BYTES:
        raise ValueError(f"{filename} exceeds the 10 MiB memo limit; upload a smaller file.")

    cannot_open = f"Cannot open {filename}; upload a readable .pdf or .docx memo."
    if suffix == ".pdf":
        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:
            # File parsers can raise several exception types for malformed uploads.
            raise ValueError(cannot_open) from exc
        if reader.is_encrypted:
            raise ValueError(f"{filename} is encrypted; upload a PDF without password protection.")
        try:
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ValueError(cannot_open) from exc
    else:
        try:
            document = Document(BytesIO(data))
            lines: list[str] = []
            # iter_inner_content walks the body's w:p and w:tbl blocks in order.
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    lines.append(block.text)
                else:
                    lines.extend(_table_lines(block))
            text = "\n".join(lines)
        except Exception as exc:
            raise ValueError(cannot_open) from exc

    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if sum(not char.isspace() for char in text) < 20:
        raise ValueError(
            f"{filename} has no text to read (it is a scan); upload the typed memo, or the TOML"
        )
    return text
