"""Sources to a snapshot (knowledge.md §4.1, §6.3, §6.4).

The snapshot is JSON Lines: a header line, then one Passage per line (without `score`) plus its
`chunk` and `embedding`. Its identity is the sha256 of the file's bytes.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from mothusi_contracts import Contract, PassageKind
from mothusi_knowledge.chunk import chunk_page, normalise, pack_passages, strip_running_lines
from mothusi_knowledge.embed import Embedder
from mothusi_knowledge.extract import ExtractError, pdf_pages

SNAPSHOT_VERSION = 1
EMBED_BATCH = 32


class Source(Contract):
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: PassageKind
    subject: str | None = None
    grade: int | None = Field(default=None, ge=1, le=12)


class SourcesFile(Contract):
    source: tuple[Source, ...] = Field(min_length=1)


@dataclass(frozen=True)
class Draft:
    """A passage before embedding."""

    passage_id: str
    source: Source
    page: int | None
    chunk: int
    locator: str
    text: str


@dataclass(frozen=True)
class SourceReport:
    title: str
    pages: int
    empty: int
    passages: int


def load_sources(path: Path) -> tuple[Source, ...]:
    with path.open("rb") as stream:
        return SourcesFile.model_validate(tomllib.load(stream)).source


def _key(source: Source) -> str:
    return hashlib.sha256(source.path.encode()).hexdigest()[:10]


def drafts_for(
    source: Source,
    root: Path,
    max_chars: int,
    read_pdf: Callable[[Path], tuple[str, ...]] = pdf_pages,
) -> tuple[list[Draft], SourceReport]:
    """A source's passages, page-local (a chunk never spans pages)."""
    file = root / source.path
    if not file.is_file():
        raise ExtractError(f"{source.path}: no such source file")
    key = _key(source)
    out: list[Draft] = []
    if source.kind is PassageKind.PACK:
        for n, (locator, text) in enumerate(pack_passages(file)):
            if text.strip():
                out.append(Draft(f"{key}-{locator}", source, None, n, locator, text.strip()))
        return out, SourceReport(source.title, 0, 0, len(out))
    pages = strip_running_lines(read_pdf(file))
    empty = 0
    for number, page in enumerate(pages, start=1):
        chunks = chunk_page(normalise(page), max_chars=max_chars)
        if not chunks:
            empty += 1
        for n, text in enumerate(chunks):
            out.append(Draft(f"{key}-p{number}-c{n}", source, number, n, f"PDF p {number}", text))
    return out, SourceReport(source.title, len(pages), empty, len(out))


def _batches[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def write_snapshot(
    sources: Sequence[Source],
    root: Path,
    out: Path,
    embedder: Embedder,
    *,
    max_chars: int = 1200,
    read_pdf: Callable[[Path], tuple[str, ...]] = pdf_pages,
    say: Callable[[str], None] = print,
) -> tuple[str, int]:
    """Build the snapshot file; returns (sha256, passages). Raises ExtractError on a bad source."""
    drafts: list[Draft] = []
    hashes: list[dict[str, str]] = []
    for source in sources:
        found, report = drafts_for(source, root, max_chars, read_pdf)
        drafts.extend(found)
        hashes.append(
            {
                "source_key": source.path,
                "sha256": hashlib.sha256((root / source.path).read_bytes()).hexdigest(),
            }
        )
        counts = f"{report.pages} pages, {report.empty} empty, {report.passages} passages"
        say(f"{report.title}: {counts}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed only when every batch is embedded: a failed or
    # interrupted ingest never leaves a partial snapshot where a good one was.
    partial = out.with_name(f".{out.name}.partial")
    header: dict[str, Any] = {
        "snapshot": SNAPSHOT_VERSION,
        "model": embedder.model,
        "dim": embedder.dim,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": hashes,
    }
    with partial.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(header, ensure_ascii=False) + "\n")
        for batch in _batches(drafts, EMBED_BATCH):
            vectors = embedder.embed([d.text for d in batch])
            for draft, vector in zip(batch, vectors, strict=True):
                row = {
                    "passage_id": draft.passage_id,
                    "source": draft.source.title,
                    "source_key": draft.source.path,
                    "kind": draft.source.kind.value,
                    "subject": draft.source.subject,
                    "grade": draft.source.grade,
                    "page": draft.page,
                    "chunk": draft.chunk,
                    "locator": draft.locator,
                    "text": draft.text,
                    "embedding": [round(v, 6) for v in vector],
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    partial.replace(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    say(f"snapshot {digest} ({len(drafts)} passages)")
    return digest, len(drafts)
