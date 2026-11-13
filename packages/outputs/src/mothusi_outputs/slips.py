"""Printable learner sign-in credentials, without roster names (§6.5)."""

import re
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import cast

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit  # pyright: ignore[reportUnknownVariableType]
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


def learner_slips_pdf(class_label: str, slips: Sequence[tuple[str, str]]) -> bytes:
    """Render eight cut-out slips per A4 page, validating credentials first."""
    if not slips:
        raise ValueError("slips must not be empty")
    seen: set[str] = set()
    for code, pin in slips:
        if re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{8}", code) is None:
            raise ValueError("invalid learner code")
        if re.fullmatch(r"[0-9]{6}", pin) is None:
            raise ValueError("PIN must contain exactly 6 digits")
        if code in seen:
            raise ValueError("duplicate learner code")
        seen.add(code)

    fonts = Path(__file__).parent / "fonts"
    for weight in ("Regular", "Bold"):
        font_name = f"MothusiSheetNotoSans-{weight}"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(  # pyright: ignore[reportUnknownMemberType]
                TTFont(font_name, str(fonts / f"NotoSans-{weight}.ttf"))
            )
    regular = "MothusiSheetNotoSans-Regular"
    bold = "MothusiSheetNotoSans-Bold"
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1)
    canvas.setTitle("Mothusi sign-in slips")
    canvas.setAuthor("Mothusi")
    margin = 10 * mm
    width = (A4[0] - 2 * margin) / 2
    height = (A4[1] - 2 * margin) / 4
    padding = 6 * mm
    instructions = cast(
        list[str],
        simpleSplit(
            "Sign in at the address your teacher gives you; you will "
            "choose a new PIN the first time.",
            regular,
            9,
            width - 2 * padding,
        ),
    )
    for index, (code, pin) in enumerate(slips):
        position = index % 8
        column = position % 2
        row = position // 2
        left = margin + column * width
        top = A4[1] - margin - row * height
        canvas.setDash(3, 3)
        canvas.setLineWidth(0.5)
        canvas.setStrokeGray(0.5)
        canvas.rect(left, top - height, width, height, stroke=1, fill=0)
        x = left + padding
        label_width = pdfmetrics.stringWidth(class_label, bold, 11)
        label_size = min(11, (width - 2 * padding) * 11 / max(label_width, 1))
        canvas.setFont(bold, label_size)
        canvas.drawString(x, top - 10 * mm, class_label)
        canvas.setFont(regular, 9)
        canvas.drawString(x, top - 18 * mm, "Your code")
        text = canvas.beginText(x, top - 27 * mm)
        text.setFont(bold, 20)
        text.setCharSpace(1)
        text.textOut(code)
        text.setCharSpace(0)
        canvas.drawText(text)
        canvas.setFont(regular, 9)
        canvas.drawString(x, top - 36 * mm, "First PIN")
        canvas.setFont(bold, 14)
        canvas.drawString(x, top - 43 * mm, pin)
        canvas.setFont(regular, 9)
        for line_index, line in enumerate(instructions):
            canvas.drawString(x, top - 52 * mm - line_index * 12, line)
        if position == 7 or index == len(slips) - 1:
            canvas.showPage()
    canvas.save()
    return output.getvalue()
