"""Sign-in slips carry only the class, credentials and first-sign-in instructions."""

from io import BytesIO

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject
from reportlab.lib.pagesizes import A4

from mothusi_outputs import learner_slips_pdf

INSTRUCTIONS = (
    "Sign in at the address your teacher gives you; you will choose a new PIN the first time."
)


@pytest.mark.parametrize("count,pages", [(1, 1), (8, 1), (9, 2), (16, 2), (17, 3)])
def test_slips_pages_and_text(count: int, pages: int) -> None:
    slips = [(f"ABCDEF{index:02d}", f"{index:06d}") for index in range(count)]
    label = "Grade 10 — Ṱ Class"
    pdf = learner_slips_pdf(label, slips)
    assert isinstance(pdf, bytes)
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == pages
    for page_index, page in enumerate(reader.pages):
        assert float(page.mediabox.width) == pytest.approx(A4[0])
        assert float(page.mediabox.height) == pytest.approx(A4[1])
        page_slips = slips[page_index * 8 : (page_index + 1) * 8]
        text = page.extract_text()
        assert text.count(label) == len(page_slips)
        assert text.count("Your code") == len(page_slips)
        assert text.count("First PIN") == len(page_slips)
        normalized = " ".join(text.split())
        assert normalized.count(INSTRUCTIONS) == len(page_slips)
        for code, pin in page_slips:
            assert code in text
            assert pin in text
        # Only the contracted fields appear; no name field or roster data is emitted.
        expected = " ".join(
            f"{label} Your code {code} First PIN {pin} {INSTRUCTIONS}" for code, pin in page_slips
        )
        assert normalized == expected
        assert "Name" not in text


def test_slips_two_by_four_cut_lines_and_code_typography() -> None:
    slips = [(f"XYZABC{index:02d}", "012345") for index in range(8)]
    page = PdfReader(BytesIO(learner_slips_pdf("10A", slips))).pages[0]
    contents = page.get_contents()
    assert contents is not None
    rectangles = [
        tuple(float(value) for value in args) for args, op in contents.operations if op == b"re"
    ]
    assert len(rectangles) == 8
    assert len({rect[0] for rect in rectangles}) == 2
    assert len({rect[1] for rect in rectangles}) == 4
    assert len({rect[2:] for rect in rectangles}) == 1
    assert any(op == b"d" and len(args[0]) > 0 for args, op in contents.operations)
    assert any(op == b"Tc" and float(args[0]) > 0 for args, op in contents.operations)
    code_fonts: list[tuple[str, float]] = []

    def visit(
        text: str,
        cm: list[float],
        tm: list[float],
        font: DictionaryObject | None,
        size: float,
    ) -> None:
        if any(code in text for code, _ in slips):
            assert font is not None
            code_fonts.append((str(font["/BaseFont"]), size))

    page.extract_text(visitor_text=visit)
    assert len(code_fonts) == 8
    assert all("NotoSans-Bold" in font and size == 20 for font, size in code_fonts)


def test_slips_reject_empty_input() -> None:
    with pytest.raises(ValueError, match=r"empty|at least"):
        learner_slips_pdf("10A", [])


@pytest.mark.parametrize(
    "code",
    [
        "",
        "ABCDEF1",
        "ABCDEF123",
        "abcdef12",
        "ABCDEF1I",
        "ABCDEF1L",
        "ABCDEF1O",
        "ABCDEF1U",
        "ABCDEF1!",
        "ABC DEF1",
        "ABCDEF12\n",
        "\uff21BCDEF12",
    ],
)
def test_slips_reject_invalid_code(code: str) -> None:
    with pytest.raises(ValueError, match="code"):
        learner_slips_pdf("10A", [(code, "123456")])


@pytest.mark.parametrize(
    "pin",
    [
        "",
        "12345",
        "1234567",
        "12345a",
        "123 45",
        "123456\n",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        "١٢٣٤٥٦",
    ],
)
def test_slips_reject_invalid_pin(pin: str) -> None:
    with pytest.raises(ValueError, match="PIN"):
        learner_slips_pdf("10A", [("ABCDEF12", pin)])


def test_slips_reject_repeated_code() -> None:
    with pytest.raises(ValueError, match=r"duplicate|repeated"):
        learner_slips_pdf("10A", [("ABCDEF12", "123456"), ("ABCDEF12", "654321")])


def test_slips_accept_crockford_alphabet() -> None:
    slips = [(code, "000000") for code in ("01234567", "89ABCDEF", "GHJKMNPQ", "RSTVWXYZ")]
    text = PdfReader(BytesIO(learner_slips_pdf("10A", slips))).pages[0].extract_text()
    assert all(code in text for code, _ in slips)
