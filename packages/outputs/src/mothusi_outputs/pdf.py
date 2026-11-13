"""Deterministic printable moderation reports and learner notes."""

import base64
import re
from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import segno
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfdoc import PDFDictionary, PDFDocument, PDFInfo, PDFString
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from mothusi_contracts import LearnerNote

_WIDTH = A4[0] - 40 * mm
_FONT = "MothusiNotoSans"
_BOLD = "MothusiNotoSans-Bold"
_UNSUPPORTED = re.compile(
    r"^(?:\s+\S|#{1,6}(?:\s|$)|[-+*]\s|\d+[.)]\s|>|```|~~~|!\[|\[|"
    r"<|[*_`]|-{3,}\s*$|={3,}\s*$)"
)


class _UndatedInfo(PDFInfo):
    """ReportLab's invariant mode fixes timestamps; this contract omits them."""

    def format(self, document: PDFDocument) -> bytes:
        return PDFDictionary(  # pyright: ignore[reportUnknownMemberType]
            {
                "Title": PDFString(self.title),
                "Author": PDFString(self.author),
                "Subject": PDFString(self.subject),
                "Creator": PDFString("Mothusi"),
                "Producer": PDFString(self.producer),
            }
        ).format(document)


class _ReportCanvas(Canvas):
    _doc: PDFDocument

    def __init__(self, filename: str | IO[bytes], **kwargs: Any) -> None:
        super().__init__(filename, **kwargs)  # pyright: ignore[reportUnknownMemberType]
        # Install before BaseDocTemplate caches the info object during build().
        self._doc.info = _UndatedInfo()


def _unescape(text: str) -> str:
    return re.sub(r"\\([\\|])", r"\1", text)


def _cells(line: str, number: int) -> list[str]:
    if not line.startswith("| ") or not line.endswith(" |"):
        raise ValueError(f"unsupported table at line {number}")
    # Consume escaped pairs together: a literal backslash before an escaped bar
    # must not turn that bar back into a column boundary.
    cells: list[str] = []
    start = 1
    index = 1
    while index < len(line):
        if line[index] == "\\" and index + 1 < len(line) and line[index + 1] in "\\|":
            index += 2
            continue
        if line[index] == "|":
            cells.append(_unescape(line[start:index].strip()))
            start = index + 1
        index += 1
    return cells


def _story(report_md: str) -> list[Flowable]:
    body = ParagraphStyle("body", fontName=_FONT, fontSize=10, leading=14, spaceAfter=7)
    heading = ParagraphStyle(
        "heading",
        parent=body,
        fontName=_BOLD,
        fontSize=13,
        leading=17,
        spaceBefore=12,
        keepWithNext=True,
    )
    title = ParagraphStyle("title", parent=heading, fontSize=18, leading=23)
    cell = ParagraphStyle("cell", parent=body, fontSize=9, leading=12, spaceAfter=0)
    header = ParagraphStyle("header", parent=cell, fontName=_BOLD)
    story: list[Flowable] = []
    lines = report_md.splitlines()
    index = 0
    signatures = False
    while index < len(lines):
        line = lines[index]
        number = index + 1
        index += 1
        if not line:
            continue
        if line.startswith("# "):
            story.append(Paragraph(escape(_unescape(line[2:])), title))
            signatures = False
        elif line.startswith("## "):
            story.append(Paragraph(escape(_unescape(line[3:])), heading))
            signatures = line == "## Signatures"
        elif line.startswith("|"):
            rows = [_cells(line, number)]
            if index >= len(lines):
                raise ValueError(f"missing table separator at line {number}")
            separator = _cells(lines[index], index + 1)
            if len(separator) != len(rows[0]) or any(c != "---" for c in separator):
                raise ValueError(f"invalid table separator at line {index + 1}")
            index += 1
            while index < len(lines) and lines[index].startswith("|"):
                row = _cells(lines[index], index + 1)
                if len(row) != len(rows[0]):
                    raise ValueError(f"wrong table column count at line {index + 1}")
                rows.append(row)
                index += 1
            table = Table(
                [
                    [Paragraph(escape(c), header if n == 0 else cell) for c in row]
                    for n, row in enumerate(rows)
                ],
                colWidths=[_WIDTH / len(rows[0])] * len(rows[0]),
                repeatRows=1,
                splitInRow=1,
                hAlign="LEFT",
                style=TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#888888")),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            )
            story.extend([table, Spacer(1, 7)])
        elif signatures:
            match = re.fullmatch(
                r"(Marker:|Moderator \(.*\):|Head of Department:) (_{4,})"
                r"(?:  (Date:) (_{4,}))?",
                line,
            )
            if match is None:
                raise ValueError(f"unsupported signature at line {number}")
            for label, width in ((match[1], 60), (match[3], 35)):
                if label:
                    story.append(
                        KeepTogether(
                            [
                                Paragraph(escape(_unescape(label)), body),
                                Spacer(1, 8 * mm),
                                HRFlowable(width=width * mm, thickness=0.6, hAlign="LEFT"),
                                Spacer(1, 5 * mm),
                            ]
                        )
                    )
        elif _UNSUPPORTED.match(line):
            raise ValueError(f"unsupported markdown at line {number}")
        else:
            story.append(Paragraph(escape(_unescape(line)), body))
    return story


def _register_fonts() -> None:
    fonts = Path(__file__).parent / "fonts"
    for name, weight in ((_FONT, "Regular"), (_BOLD, "Bold")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(  # pyright: ignore[reportUnknownMemberType]
                TTFont(name, str(fonts / f"NotoSans-{weight}.ttf"))
            )


def moderation_report_pdf(report_md: str, *, title: str) -> bytes:
    """Render the rules report with embedded fonts and deterministic PDF bytes."""
    _register_fonts()
    page_count = 0

    def finish_page(canvas: Canvas, document: BaseDocTemplate) -> None:
        nonlocal page_count
        page_count = max(page_count, document.page)
        canvas.saveState()
        footer = Paragraph(
            escape(f"{title} · page {document.page} of {page_count}"),
            ParagraphStyle("footer", fontName=_FONT, fontSize=8, leading=10),
        )
        _, height = footer.wrap(_WIDTH, 15 * mm)
        footer.drawOn(canvas, 20 * mm, 10 * mm - height / 2)
        canvas.restoreState()

    # The first pass measures pagination; the second writes the known page total.
    result = BytesIO()
    for _ in range(2):
        result = BytesIO()
        document = BaseDocTemplate(
            result,
            pagesize=A4,
            invariant=1,
            title=title,
            author="Mothusi",
            subject="Moderation report",
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )
        frame = Frame(
            20 * mm,
            20 * mm,
            _WIDTH,
            A4[1] - 40 * mm,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        document.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=finish_page))
        document.build(_story(report_md), canvasmaker=_ReportCanvas)
    return result.getvalue()


def _note_qr(canvas: Canvas, url: str) -> None:
    matrix = segno.make(url, error="q").matrix
    module = 35 * mm / (len(matrix) + 8)
    for row_number, row in enumerate(matrix):
        for column, dark in enumerate(row):
            if dark:
                canvas.rect(
                    155 * mm + (column + 4) * module,
                    20 * mm + (len(matrix) + 3 - row_number) * module,
                    module,
                    module,
                    stroke=0,
                    fill=1,
                )


def _note_paragraphs(title: str, name: str, note: LearnerNote) -> list[Paragraph]:
    body = ParagraphStyle("note-body", fontName=_FONT, fontSize=11, leading=16, spaceAfter=12)
    heading = ParagraphStyle(
        "note-heading", parent=body, fontName=_BOLD, fontSize=17, leading=23, spaceAfter=18
    )
    paragraphs = [Paragraph(escape(title), heading), Paragraph(escape(name), heading)]
    for label, content in (
        ("What this task tested:", note.outcome),
        ("Your mark, and the work that earned it:", note.mark_with_quote),
        ("Your next task:", note.next_task),
        ("Ask yourself:", note.self_assessment_prompt),
    ):
        paragraphs.extend(
            [
                Paragraph(escape(label), ParagraphStyle("note-label", parent=body, fontName=_BOLD)),
                Paragraph(escape(content), body),
            ]
        )
    return paragraphs


def _note_expiry(url: str) -> str:
    """Read display-only expiry from §6.2's token; the core authenticates it on scan."""
    try:
        token = urlsplit(url).path.rsplit("/", 1)[-1]
        payload, _ = token.split(".")
        decoded = base64.b64decode(
            payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
        ).decode("utf-8")
        version, _, _, timestamp = decoded.split("|")
        if version != "v1":
            raise ValueError("unknown note link version")
        return (
            datetime.fromtimestamp(int(timestamp), ZoneInfo("Africa/Johannesburg"))
            .date()
            .isoformat()
        )
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("note URL must contain a v1 token with a readable expiry") from exc


def learner_notes_pdf(title: str, notes: Sequence[tuple[str, LearnerNote, str]]) -> bytes:
    """Render one complete A4 page per learner, with a vector QR to their note."""
    seen: set[str] = set()
    for name, note, url in notes:
        if not name.strip():
            raise ValueError("the learner's name is needed for the note")
        if not url.startswith("https://"):
            raise ValueError("note URL must start with https://")
        if note.pseudonym in seen:
            raise ValueError("duplicate learner pseudonym")
        seen.add(note.pseudonym)
    expiries = [_note_expiry(url) for _, _, url in notes]
    _register_fonts()
    output = BytesIO()
    canvas = _ReportCanvas(output, pagesize=A4, invariant=1)
    canvas.setTitle(title)
    canvas.setAuthor("Mothusi")
    canvas.setSubject("Learner notes")
    for (name, note, url), expiry in zip(notes, expiries, strict=True):
        paragraphs = _note_paragraphs(title, " ".join(name.split()), note)
        heights = [paragraph.wrap(_WIDTH, A4[1])[1] for paragraph in paragraphs]
        total = sum(h + p.getSpaceAfter() for p, h in zip(paragraphs, heights, strict=True))
        # Scale the complete note uniformly if needed, keeping it above the QR band.
        scale = min(1.0, (A4[1] - 85 * mm) / total)
        canvas.saveState()
        canvas.translate(20 * mm, A4[1] - 20 * mm)
        canvas.scale(scale, scale)
        y = 0.0
        for paragraph, height in zip(paragraphs, heights, strict=True):
            y -= height
            paragraph.drawOn(canvas, 0, y)
            y -= paragraph.getSpaceAfter()
        canvas.restoreState()
        _note_qr(canvas, url)
        caption = Paragraph(
            f"Scan to read this note on a phone. The link expires on {expiry}.",
            ParagraphStyle("note-caption", fontName=_FONT, fontSize=9, leading=13),
        )
        _, height = caption.wrap(120 * mm, 35 * mm)
        caption.drawOn(canvas, 20 * mm, 55 * mm - height)
        canvas.showPage()
    canvas.save()
    return output.getvalue()
