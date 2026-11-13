"""Pure, page-local text preparation (knowledge.md §6.2)."""

import re
import tomllib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path


def strip_running_lines(pages: Sequence[str], *, threshold: float = 0.6) -> tuple[str, ...]:
    """Remove lines whose digit-normalised form occurs on enough distinct pages."""
    if len(pages) < 3:
        return tuple(pages)

    def key(line: str) -> str:
        return re.sub(r"\d", "#", line.strip())

    # Blank lines are paragraph breaks, never running lines: counting them would delete every
    # break and turn each page into one paragraph.
    counts = Counter(line for page in pages for line in set(map(key, page.splitlines())) if line)
    running = {line for line, count in counts.items() if count / len(pages) >= threshold}
    return tuple(
        "\n".join(
            line for line in page.splitlines() if not line.strip() or key(line) not in running
        )
        for page in pages
    )


def normalise(page_text: str) -> str:
    """Trim line ends and excessive blank lines while retaining table columns."""
    text = "\n".join(line.rstrip() for line in page_text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_page(
    page_text: str, *, max_chars: int = 1200, overlap_max: int = 300, min_chars: int = 80
) -> tuple[str, ...]:
    """Greedily pack one page's paragraphs, with bounded last-paragraph overlap."""
    if max_chars <= 0 or overlap_max < 0 or min_chars < 0:
        raise ValueError("max_chars must be positive; overlap_max and min_chars non-negative")
    paragraphs = re.split(r"\n\s*\n", page_text.strip())
    pieces: list[str] = []
    for paragraph in paragraphs:
        remainder = paragraph.strip()
        while len(remainder) > max_chars:
            boundary = next(
                (i for i in range(max_chars, 0, -1) if remainder[i].isspace()), max_chars
            )
            pieces.append(remainder[:boundary].strip())
            remainder = remainder[boundary:].strip()
        if remainder:
            pieces.append(remainder)

    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and len("\n\n".join((*current, piece))) > max_chars:
            chunks.append("\n\n".join(current))
            last = current[-1]
            current = (
                [last]
                if len(last) <= overlap_max and len(last) + 2 + len(piece) <= max_chars
                else []
            )
        current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return tuple(chunk for chunk in chunks if len(chunk.strip()) >= min_chars)


def pack_passages(pack_path: Path) -> tuple[tuple[str, str], ...]:
    """Read only sourced text and conventions, preserving their TOML array indices."""
    with pack_path.open("rb") as source:
        pack = tomllib.load(source)
    return tuple(
        (f"{field}[{index}]", text)
        for field in ("sources", "conventions")
        for index, text in enumerate(pack.get(field, ()))
    )
