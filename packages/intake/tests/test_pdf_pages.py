"""Uploads retain image bytes or become ordered, full-page PNGs at 150 dpi."""

import re
from io import BytesIO

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen.canvas import Canvas

from mothusi_intake.pages import pages_from_upload


def make_pdf(page_size: tuple[float, float] = A4) -> bytes:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=page_size)
    for color in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        canvas.setFillColorRGB(*color)
        canvas.rect(0, 0, *page_size, fill=1, stroke=0)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


@pytest.mark.parametrize("filename", ["scripts.pdf", "SCRIPTS.PDF"])
def test_three_a4_pages_render_in_order_at_150_dpi(filename: str) -> None:
    pages = pages_from_upload(make_pdf(), filename)

    assert isinstance(pages, tuple)
    assert len(pages) == 3
    for page, color in zip(pages, ((255, 0, 0), (0, 255, 0), (0, 0, 255)), strict=True):
        assert isinstance(page, bytes)
        with Image.open(BytesIO(page)) as image:
            assert image.format == "PNG"
            assert image.size == (1240, 1754)
            assert image.convert("RGB").getpixel((620, 877)) == color
            assert image.convert("RGB").getpixel((1239, 1753)) == color


@pytest.mark.parametrize(
    ("page_size", "expected"),
    [(landscape(A4), (1754, 1240)), ((72, 144), (150, 300))],
)
def test_pdf_dimensions_follow_page_geometry(
    page_size: tuple[float, float], expected: tuple[int, int]
) -> None:
    pages = pages_from_upload(make_pdf(page_size), "different-size.pdf")
    for page in pages:
        with Image.open(BytesIO(page)) as image:
            assert image.size == expected


@pytest.mark.parametrize(
    ("image_format", "filename"),
    [("PNG", "scan.png"), ("PNG", "SCAN.PNG"), ("JPEG", "scan.jpg"), ("JPEG", "scan.JPEG")],
)
def test_image_upload_passes_through_byte_for_byte(image_format: str, filename: str) -> None:
    with BytesIO() as stream, Image.new("RGB", (43, 67), "navy") as image:
        image.save(stream, format=image_format)
        data = stream.getvalue()
    assert pages_from_upload(data, filename) == (data,)


@pytest.mark.parametrize("filename", ["learner homework.docx", "scan.gif", "scan.tiff", "scan"])
def test_unsupported_upload_is_refused_by_filename(filename: str) -> None:
    with pytest.raises(ValueError, match=re.escape(filename)):
        pages_from_upload(b"unsupported upload", filename)


@pytest.mark.parametrize("filename", ["broken.pdf", "broken.png", "broken.jpg"])
@pytest.mark.parametrize("data", [b"", b"not an image or PDF"])
def test_invalid_upload_is_refused_by_filename(data: bytes, filename: str) -> None:
    with pytest.raises(ValueError, match=re.escape(filename)):
        pages_from_upload(data, filename)


def test_other_image_format_cannot_masquerade_as_png() -> None:
    with BytesIO() as stream, Image.new("RGB", (10, 10), "white") as image:
        image.save(stream, format="GIF")
        data = stream.getvalue()
    with pytest.raises(ValueError, match=r"disguised\.png"):
        pages_from_upload(data, "disguised.png")
