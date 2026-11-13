"""The HOD's report preserves the rules renderer's facts and signing space."""

from io import BytesIO
from typing import cast

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject
from reportlab.pdfbase.ttfonts import TTFont

from mothusi_contracts import Disagreement
from mothusi_outputs import moderation_report_pdf
from mothusi_rules import moderation_report_md


def report(sample: tuple[str, ...] = ("L-021", "L-007")) -> str:
    return moderation_report_md(
        "Term 1 <Maths> & review",
        sample,
        (
            Disagreement(script_id="L-021", question_id="Q4", first=2, second=5),
            Disagreement(script_id="L-007", question_id="Q9", first=7, second=6),
        ),
        {"L-021/Q4": r"Confirmed | memo C:\work <evidence> & checked"},
        10,
        "Maṱodzi",
    )


def test_pdf_report_reads_every_cell_and_signature() -> None:
    reader = PdfReader(BytesIO(moderation_report_pdf(report(), title="HOD report")))
    text = "\n".join(page.extract_text() for page in reader.pages)
    for cell in (
        "script",
        "included",
        "yes",
        "question",
        "first mark",
        "second mark",
        "gap",
        "resolution",
        "L-021",
        "Q4",
        "2",
        "5",
        "3",
        "L-007",
        "Q9",
        "7",
        "6",
        "1",
        r"Confirmed | memo C:\work <evidence> & checked",
        "unresolved",
        "Marker:",
        "Moderator (Maṱodzi):",
        "Head of Department:",
        "Date:",
        "Moderation report: Term 1 <Maths> & review",
        "Agreement rate on the sample: 80.00% (8 of 10 questions).",
        "HOD report · page 1 of 1",
    ):
        assert cell in " ".join(text.split())
    assert "____" not in text
    assert reader.metadata is not None
    assert reader.metadata.title == "HOD report"
    assert reader.metadata.author == "Mothusi"
    assert reader.metadata.subject == "Moderation report"
    assert "/CreationDate" not in reader.metadata
    assert "/ModDate" not in reader.metadata
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595.276, abs=0.01)
    assert float(reader.pages[0].mediabox.height) == pytest.approx(841.89, abs=0.01)


def test_pdf_report_deterministic_across_other_renders() -> None:
    first = moderation_report_pdf(report(), title="Review")
    moderation_report_pdf(report(("Other",)), title="Different")
    assert first == moderation_report_pdf(report(), title="Review")


@pytest.mark.parametrize(
    "line",
    [
        "### Unknown",
        "- bullet",
        "> quote",
        "```",
        "![image](x)",
        "---",
        "    indented",
        "**bold**",
        "[link](x)",
    ],
)
def test_pdf_report_unknown_line_names_number(line: str) -> None:
    with pytest.raises(ValueError, match=r"\bline 3\b"):
        moderation_report_pdf("# Report\n\n" + line, title="Review")


@pytest.mark.parametrize(
    "table, number", [("| a | b |\n| x | y |", 3), ("| a | b |\n| --- | --- |\n| x |", 4)]
)
def test_pdf_report_malformed_table_names_line(table: str, number: int) -> None:
    with pytest.raises(ValueError, match=rf"\bline {number}\b"):
        moderation_report_pdf("# Report\n" + table, title="Review")


def test_pdf_report_multipage_repeats_headers_and_numbers() -> None:
    sample = tuple(f"L-{i:04d}" for i in range(180))
    reader = PdfReader(BytesIO(moderation_report_pdf(report(sample), title="Long review")))
    assert len(reader.pages) > 2
    texts = [page.extract_text() for page in reader.pages]
    for number, text in enumerate(texts, 1):
        assert f"Long review · page {number} of {len(texts)}" in text
        if any(pseudonym in text for pseudonym in sample):
            assert "script" in text and "included" in text
    for pseudonym in sample:
        assert pseudonym in "\n".join(texts)


def test_pdf_report_empty_sample_no_disagreements() -> None:
    md = moderation_report_md("Test", (), (), {}, 3, "M")
    reader = PdfReader(BytesIO(moderation_report_pdf(md, title="Review")))
    text = reader.pages[0].extract_text()
    assert "No disagreements in the sample." in text
    assert "100.00% (3 of 3 questions)" in text


def test_pdf_report_fonts_embed_and_cover_tshivenda() -> None:
    from pathlib import Path

    import mothusi_outputs

    assert mothusi_outputs.__file__ is not None
    root = Path(mothusi_outputs.__file__).parent / "fonts"
    for weight in ("Regular", "Bold"):
        font = TTFont(f"coverage-{weight}", str(root / f"NotoSans-{weight}.ttf"))
        assert all(font.face.charToGlyph.get(ord(letter), 0) for letter in "ḓḽṅṋṱ")
    assert "SIL OPEN FONT LICENSE Version 1.1" in (root / "OFL.txt").read_text()
    reader = PdfReader(BytesIO(moderation_report_pdf(report(), title="Maṱodzi")))
    resources = cast(DictionaryObject, reader.pages[0]["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"].get_object())
    embedded: list[DictionaryObject] = []
    for ref in fonts.values():
        font = cast(DictionaryObject, ref.get_object())
        if "NotoSans" in str(font.get("/BaseFont")):
            embedded.append(font)
    assert len(embedded) == 2
    for font in embedded:
        assert "/ToUnicode" in font
        assert "/FontFile2" in cast(DictionaryObject, font["/FontDescriptor"].get_object())


def test_pdf_report_backslash_before_literal_bar() -> None:
    md = report((r"L-\|007",))
    text = PdfReader(BytesIO(moderation_report_pdf(md, title="Review"))).pages[0].extract_text()
    assert r"L-\|007" in text


def test_pdf_report_signature_rules_have_pen_lengths() -> None:
    reader = PdfReader(BytesIO(moderation_report_pdf(report(), title="Review")))
    lengths: list[float] = []
    for page in reader.pages:
        contents = page.get_contents()
        assert contents is not None
        start: tuple[float, float] | None = None
        for operands, operator in contents.operations:
            if operator == b"m":
                start = (float(operands[0]), float(operands[1]))
            elif operator == b"l" and start is not None and float(operands[1]) == start[1]:
                lengths.append(abs(float(operands[0]) - start[0]))
    assert sum(abs(length - 60 * 72 / 25.4) < 0.01 for length in lengths) == 3
    assert sum(abs(length - 35 * 72 / 25.4) < 0.01 for length in lengths) == 1
