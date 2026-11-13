"""Deterministic answer-sheet layout and vector PDF rendering (§6.3)."""

from collections.abc import Sequence
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import segno
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from mothusi_contracts import AnswerSheetManifest, Memo, MmRect, SheetBox, SheetPage


def _rect(x: int, y: int, w: int, h: int) -> MmRect:
    return MmRect(x=Decimal(x), y=Decimal(y), w=Decimal(w), h=Decimal(h))


def _validate_id(value: str) -> None:
    if ":" in value:
        raise ValueError("answer-sheet identifiers must not contain ':'")


def layout_answer_sheet(
    assessment_id: str, memo: Memo, learner_pseudonym: str
) -> AnswerSheetManifest:
    """Compute all paper geometry without drawing, in memo order."""
    _validate_id(assessment_id)
    _validate_id(learner_pseudonym)
    pages: list[SheetPage] = []
    boxes: list[SheetBox] = []
    y = 48

    def page() -> SheetPage:
        number = len(pages) + 1
        return SheetPage(
            page_no=number,
            qr_payload=f"mothusi:v1:{assessment_id}:{learner_pseudonym}:{number}",
            boxes=tuple(boxes),
        )

    for item in memo.items:
        height = min(120, max(30, 12 * item.max_marks))
        if y + height > 280:
            pages.append(page())
            boxes = []
            y = 48
        boxes.append(
            SheetBox(
                question_id=item.question_id,
                marks=item.max_marks,
                box=_rect(29, y, 167, height),
                label=_rect(14, y, 15, height),
            )
        )
        y += height + 4
    pages.append(page())
    return AnswerSheetManifest(
        assessment_id=assessment_id,
        learner_pseudonym=learner_pseudonym,
        name_band=_rect(14, 14, 148, 25),
        qr=_rect(166, 14, 30, 30),
        fiducials=(
            _rect(4, 4, 8, 8),
            _rect(198, 4, 8, 8),
            _rect(4, 285, 8, 8),
            _rect(198, 285, 8, 8),
        ),
        pages=tuple(pages),
    )


def _draw_rect(canvas: Canvas, rect: MmRect, *, fill: int) -> None:
    canvas.rect(
        float(rect.x) * mm,
        (297 - float(rect.y + rect.h)) * mm,
        float(rect.w) * mm,
        float(rect.h) * mm,
        stroke=1 - fill,
        fill=fill,
    )


def _draw_qr(canvas: Canvas, rect: MmRect, payload: str) -> None:
    matrix = segno.make(payload, error="q").matrix
    # Two quiet-zone modules on each side, inside the manifest rectangle.
    size = float(rect.w) / (len(matrix) + 4)
    for row_number, row in enumerate(matrix):
        for column, dark in enumerate(row):
            if dark:
                canvas.rect(
                    (float(rect.x) + (column + 2) * size) * mm,
                    (297 - float(rect.y) - (row_number + 3) * size) * mm,
                    size * mm,
                    size * mm,
                    stroke=0,
                    fill=1,
                )


def answer_sheets_pdf(
    assessment_id: str, memo: Memo, learners: Sequence[tuple[str, str]]
) -> tuple[bytes, tuple[AnswerSheetManifest, ...]]:
    """Draw each learner's consecutive pages from their layout manifest."""
    _validate_id(assessment_id)
    seen: set[str] = set()
    for name, pseudonym in learners:
        if not name.strip():
            raise ValueError("printed name must not be empty")
        _validate_id(pseudonym)
        if pseudonym in seen:
            raise ValueError("duplicate learner pseudonym")
        seen.add(pseudonym)
    manifests = tuple(layout_answer_sheet(assessment_id, memo, p) for _, p in learners)
    fonts = Path(__file__).parent / "fonts"
    for weight in ("Regular", "Bold"):
        font_name = f"MothusiSheetNotoSans-{weight}"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            # ReportLab's registerFont parameter is untyped in its bundled annotations.
            pdfmetrics.registerFont(  # pyright: ignore[reportUnknownMemberType]
                TTFont(font_name, str(fonts / f"NotoSans-{weight}.ttf"))
            )
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1)
    canvas.setTitle("Mothusi answer sheets")
    canvas.setAuthor("Mothusi")
    for (name, _), manifest in zip(learners, manifests, strict=True):
        for page in manifest.pages:
            canvas.setFillGray(0)
            for fiducial in manifest.fiducials:
                _draw_rect(canvas, fiducial, fill=1)
            _draw_qr(canvas, manifest.qr, page.qr_payload)
            if page.page_no == 1:
                band = manifest.name_band
                canvas.setFont("MothusiSheetNotoSans-Bold", 10)
                canvas.drawString(float(band.x + 2) * mm, (297 - float(band.y + 6)) * mm, "Name")
                # Fit even long class-list names entirely inside the region intake blanks.
                font = "MothusiSheetNotoSans-Regular"
                width = pdfmetrics.stringWidth(name, font, 12)
                size = min(12, float(band.w - 4) * mm * 12 / max(width, 1))
                canvas.setFont(font, size)
                canvas.drawString(float(band.x + 2) * mm, (297 - float(band.y + 16)) * mm, name)
            canvas.setFont("MothusiSheetNotoSans-Regular", 10)
            canvas.setLineWidth(1)
            canvas.setStrokeGray(0.5)
            for answer in page.boxes:
                _draw_rect(canvas, answer.box, fill=0)
                label = answer.label
                label_width = pdfmetrics.stringWidth(
                    answer.question_id, "MothusiSheetNotoSans-Regular", 10
                )
                canvas.setFont(
                    "MothusiSheetNotoSans-Regular",
                    min(10, float(label.w - 1) * mm * 10 / max(label_width, 1)),
                )
                canvas.drawRightString(
                    float(label.x + label.w - 1) * mm,
                    (297 - float(label.y + 6)) * mm,
                    answer.question_id,
                )
            canvas.showPage()
    canvas.save()
    return output.getvalue(), manifests
