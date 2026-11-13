"""Pure page chunking and pack extraction (knowledge.md §6.2)."""

from pathlib import Path

from mothusi_knowledge.chunk import chunk_page, normalise, pack_passages, strip_running_lines


def test_running_lines_count_pages_and_normalise_digits() -> None:
    pages = tuple(
        f"Header\nUnique {word}\nPage {i}" for i, word in enumerate(("one", "two", "three"), 1)
    )
    cleaned = strip_running_lines(pages)
    assert tuple(text.strip() for text in cleaned) == ("Unique one", "Unique two", "Unique three")
    assert strip_running_lines(pages[:2]) == pages[:2]
    assert strip_running_lines(()) == ()
    assert strip_running_lines(("Repeat\nRepeat\nAlpha", "Beta", "Gamma")) == (
        "Repeat\nRepeat\nAlpha",
        "Beta",
        "Gamma",
    )
    assert strip_running_lines(("Footer\nAlpha", "Footer\nBeta", "Gamma")) == (
        "Alpha",
        "Beta",
        "Gamma",
    )
    assert strip_running_lines(("Footer\nAlpha", "Footer\nBeta", "Gamma"), threshold=1) == (
        "Footer\nAlpha",
        "Footer\nBeta",
        "Gamma",
    )


def test_normalise_preserves_columns_and_collapses_blank_lines() -> None:
    assert normalise("  Heading  \nA    B \t\n \n\t\n\nC  \n") == "Heading\nA    B\n\nC"


def test_long_paragraph_splits_at_whitespace_without_losing_text() -> None:
    text = " ".join(["abcde"] * 416) + " abc"  # 2,499 characters
    text += "d"  # 2,500 characters
    assert len(text) == 2500
    chunks = chunk_page(text)
    assert len(chunks) == 3
    assert all(80 <= len(chunk) <= 1200 for chunk in chunks)
    assert " ".join(chunks) == text
    assert all(word in {"abcde", "abcd"} for chunk in chunks for word in chunk.split())
    assert chunk_page("x" * 2500) == ("x" * 1200, "x" * 1200, "x" * 100)


def test_greedy_paragraph_packing_and_overlap() -> None:
    paragraphs = ["a" * 800, "b" * 200, "c" * 800]
    assert chunk_page("\n\n".join(paragraphs)) == (
        paragraphs[0] + "\n\n" + paragraphs[1],
        paragraphs[1] + "\n\n" + paragraphs[2],
    )
    assert chunk_page("\n\n".join(paragraphs), overlap_max=199) == (
        paragraphs[0] + "\n\n" + paragraphs[1],
        paragraphs[2],
    )
    # Overlap must not prevent the next paragraph from fitting or stall progress.
    assert chunk_page("b" * 200 + "\n\n" + "c" * 1200) == ("b" * 200, "c" * 1200)


def test_short_empty_and_separate_pages() -> None:
    assert chunk_page("x" * 50) == ()
    assert chunk_page(" \n\n ") == ()
    assert chunk_page("x" * 80) == ("x" * 80,)
    pages = ("a" * 100, "b" * 100)
    assert tuple(chunk_page(normalise(page)) for page in pages) == ((pages[0],), (pages[1],))
    assert chunk_page("a" * 10 + "\n\n" + "b" * 10, max_chars=15, min_chars=1, overlap_max=0) == (
        "a" * 10,
        "b" * 10,
    )


def test_pack_passages_only_top_level_sources_and_conventions(tmp_path: Path) -> None:
    pack = tmp_path / "pack.toml"
    pack.write_text(
        'sources = ["CAPS", "ATP"]\nconventions = ["CA marking"]\n'
        'subject = "Maths"\n[exam]\nsources = ["Ignore"]\n'
    )
    assert pack_passages(pack) == (
        ("sources[0]", "CAPS"),
        ("sources[1]", "ATP"),
        ("conventions[0]", "CA marking"),
    )
    pack.write_text('subject = "Maths"\n')
    assert pack_passages(pack) == ()


def test_running_lines_go_but_paragraph_breaks_stay() -> None:
    pages = [
        "Intro para one here.\n\nSecond para two.\n\nFooter 1",
        "Alpha\n\nBeta\n\nFooter 2",
        "Gamma\n\nDelta\n\nFooter 3",
    ]
    stripped = strip_running_lines(pages)
    assert normalise(stripped[0]).split("\n\n") == ["Intro para one here.", "Second para two."]
    assert all("Footer" not in page for page in stripped)
    assert all("\n\n" in page for page in stripped)
