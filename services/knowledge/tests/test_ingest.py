"""Sources to a snapshot (knowledge.md §4.1, §6.3, §6.4). No database, no model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from mothusi_contracts import PassageKind
from mothusi_knowledge import __main__ as cli
from mothusi_knowledge.embed import HashingEmbedder
from mothusi_knowledge.extract import ExtractError, pdf_pages
from mothusi_knowledge.ingest import Source, drafts_for, load_sources, write_snapshot

ROOT = Path(__file__).resolve().parents[3]
PARA = "Revise the volume and surface areas of right prisms and cylinders in Grade 10. " * 2


def fake_pdf(pages: tuple[str, ...]):
    def read(_: Path) -> tuple[str, ...]:
        return pages

    return read


def test_the_checked_in_sources_list_names_held_files_and_valid_kinds() -> None:
    sources = load_sources(ROOT / "services/knowledge/sources.toml")
    assert {s.kind for s in sources} >= {PassageKind.CAPS, PassageKind.ATP, PassageKind.PACK}
    assert all(not Path(s.path).is_absolute() and ".." not in Path(s.path).parts for s in sources)


def test_passages_are_page_local_with_exact_pdf_page_locators(tmp_path: Path) -> None:
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    source = Source(path="doc.pdf", title="ATP Gr 10 Maths", kind=PassageKind.ATP, grade=10)
    pages = (f"Header\n\n{PARA}", "Header\n\n", f"Header\n\n{PARA}\n\n{PARA}", "Header\n\nx")
    drafts, report = drafts_for(source, tmp_path, 1200, fake_pdf(pages))
    assert [d.locator for d in drafts] == ["PDF p 1", "PDF p 3"]
    assert report.pages == 4 and report.empty == 2 and report.passages == 2
    assert all("Header" not in d.text for d in drafts)  # a running header on every page
    assert len({d.passage_id for d in drafts}) == len(drafts)


def test_a_pack_gives_its_sources_and_conventions_only(tmp_path: Path) -> None:
    (tmp_path / "pack.toml").write_text(
        'pack_id = "x"\nsources = ["CAPS FET Mathematics p 21"]\nconventions = ["Round to 2 dp"]\n'
    )
    source = Source(path="pack.toml", title="pack", kind=PassageKind.PACK)
    drafts, _ = drafts_for(source, tmp_path, 1200)
    assert [(d.locator, d.page) for d in drafts] == [("sources[0]", None), ("conventions[0]", None)]


def test_the_snapshot_is_a_header_then_one_embedded_passage_per_line(tmp_path: Path) -> None:
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4 bytes")
    source = Source(path="doc.pdf", title="CAPS", kind=PassageKind.CAPS, subject="Mathematics")
    said: list[str] = []
    out = tmp_path / "build/passages.jsonl"
    sha, n = write_snapshot(
        [source], tmp_path, out, HashingEmbedder(), read_pdf=fake_pdf((PARA,)), say=said.append
    )
    lines = out.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["snapshot"] == 1 and header["dim"] == 1024 and header["model"] == "hashing"
    assert header["sources"] == [
        {"source_key": "doc.pdf", "sha256": hashlib.sha256(b"%PDF-1.4 bytes").hexdigest()}
    ]
    row = json.loads(lines[1])
    assert n == 1 and len(row["embedding"]) == 1024 and row["page"] == 1 and row["chunk"] == 0
    assert "score" not in row and row["subject"] == "Mathematics"
    assert sha == hashlib.sha256(out.read_bytes()).hexdigest()
    assert said == ["CAPS: 1 pages, 0 empty, 1 passages", f"snapshot {sha} (1 passages)"]


def test_a_missing_source_or_missing_pdftotext_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "s.toml").write_text(
        '[[source]]\npath = "nope.pdf"\ntitle = "Nope"\nkind = "caps"\n'
    )
    monkeypatch.setattr(cli, "BgeM3Embedder", HashingEmbedder)
    code = cli.main(
        [
            "ingest",
            "--sources",
            str(tmp_path / "s.toml"),
            "--out",
            str(tmp_path / "o.jsonl"),
            "--root",
            str(tmp_path),
        ]
    )
    assert code == 2 and "nope.pdf: no such source file" in capsys.readouterr().err

    def no_tool(_: str) -> None:
        return None

    monkeypatch.setattr("mothusi_knowledge.extract.shutil.which", no_tool)
    with pytest.raises(ExtractError, match="pdftotext is not installed"):
        pdf_pages(tmp_path / "s.toml")


def test_pdftotext_pages_are_split_on_form_feeds() -> None:
    held = ROOT / "preparation/docs/sources/dbe/curriculum/ATP 2023-24 Gr 10 Mathematics (DBE).pdf"
    if not held.is_file():
        pytest.skip("the held ATP PDF is gitignored and not in this checkout")
    pages = pdf_pages(held)
    assert len(pages) == 5 and "MEASUREMENT" in pages[4]


def test_a_failed_ingest_leaves_the_previous_snapshot_whole(tmp_path: Path) -> None:
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    source = Source(path="doc.pdf", title="CAPS", kind=PassageKind.CAPS)
    out = tmp_path / "passages.jsonl"
    out.write_text("the previous, good snapshot\n")

    class Broken(HashingEmbedder):
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("the model ran out of memory")

    with pytest.raises(RuntimeError):
        write_snapshot([source], tmp_path, out, Broken(), read_pdf=fake_pdf((PARA,)), say=print)
    assert out.read_text() == "the previous, good snapshot\n"
