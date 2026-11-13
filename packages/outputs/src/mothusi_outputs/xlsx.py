"""Numeric teacher mark sheets with reproducible OOXML archive metadata."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from openpyxl.styles import Font

from mothusi_contracts import ConvertedMark, PoATask

_FIXED = datetime(2026, 1, 1, tzinfo=UTC)


def mark_sheet_xlsx(rows: Sequence[tuple[str, ConvertedMark]], task: PoATask) -> bytes:
    """Render one mark sheet, preserving numeric cells and half-up totals."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Mark sheet"
    sheet["A1"] = f"{task.name} ({task.max_marks} marks, {task.weight_percent}% of SBA)"
    sheet["A1"].data_type = "s"
    sheet.append(("learner", "raw", "out_of", "sba_points", "year_points"))
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    sba = year = Decimal(0)
    for learner, mark in sorted(rows, key=lambda row: row[0]):
        sheet.append((learner, mark.raw, mark.out_of, mark.sba_points, mark.year_points))
        sheet.cell(sheet.max_row, 1).data_type = "s"
        sba += mark.sba_points
        year += mark.year_points
    sheet.append(
        (
            "TOTAL",
            None,
            None,
            sba.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            year.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )
    )
    for row in sheet.iter_rows(min_row=3, min_col=4, max_col=5):
        for cell in row:
            cell.number_format = "0.00"
    sheet.freeze_panes = "A3"
    for column, width in zip("ABCDE", (20, 8, 8, 12, 12), strict=True):
        sheet.column_dimensions[column].width = width
    workbook.properties.created = workbook.properties.modified = _FIXED
    raw = BytesIO()
    workbook.save(raw)
    # openpyxl overwrites modified on save, and ZIP records carry wall-clock times.
    # Normalise both layers so the contract holds across different invocations.
    result = BytesIO()
    with ZipFile(raw) as source, ZipFile(result, "w", compression=ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == "docProps/core.xml":
                core = ElementTree.fromstring(content)
                for field in ("created", "modified"):
                    node = core.find("{http://purl.org/dc/terms/}" + field)
                    if node is not None:
                        node.text = "2026-01-01T00:00:00Z"
                content = ElementTree.tostring(core, encoding="utf-8")
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            target.writestr(info, content)
    return result.getvalue()
