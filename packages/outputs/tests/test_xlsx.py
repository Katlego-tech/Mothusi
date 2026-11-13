"""The mark sheet retains numeric values and deterministic workbook bytes."""

from datetime import datetime, tzinfo
from decimal import Decimal
from io import BytesIO
from typing import Self, cast
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from openpyxl.styles import Font

from mothusi_contracts import ConvertedMark, PoATask
from mothusi_outputs import mark_sheet_xlsx


def task() -> PoATask:
    return PoATask(term=1, name="Test", kind="test", max_marks=50, weight_percent=Decimal("14"))


def mark(sba: str, year: str, raw: int = 37) -> ConvertedMark:
    return ConvertedMark(
        script_id="L-001", raw=raw, out_of=50, sba_points=Decimal(sba), year_points=Decimal(year)
    )


def test_workbook_shape_values_and_formatting() -> None:
    data = mark_sheet_xlsx(
        (("Zulu", mark("10.36", "4.14")), ("Alpha", mark("14", "5.6", 50))), task()
    )
    workbook = load_workbook(BytesIO(data))
    assert workbook.sheetnames == ["Mark sheet"]
    sheet = workbook["Mark sheet"]
    assert sheet["A1"].value == "Test (50 marks, 14% of SBA)"
    assert [sheet.cell(2, c).value for c in range(1, 6)] == [
        "learner",
        "raw",
        "out_of",
        "sba_points",
        "year_points",
    ]
    assert all(cast(Font, sheet.cell(2, c).font).b for c in range(1, 6))
    assert [sheet.cell(3, c).value for c in range(1, 6)] == ["Alpha", 50, 50, 14, 5.6]
    assert [sheet.cell(4, c).value for c in range(1, 6)] == ["Zulu", 37, 50, 10.36, 4.14]
    assert [sheet.cell(5, c).value for c in range(1, 6)] == ["TOTAL", None, None, 24.36, 9.74]
    assert all(sheet.cell(r, c).data_type == "n" for r in (3, 4) for c in range(2, 6))
    assert sheet.freeze_panes == "A3"
    assert [sheet.column_dimensions[c].width for c in "ABCDE"] == [20, 8, 8, 12, 12]
    assert sheet["D5"].number_format == sheet["E5"].number_format == "0.00"


def test_deterministic_despite_writer_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (("A", mark("1", "0.4")),)
    first = mark_sheet_xlsx(rows, task())

    class Later(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls(2035, 1, 2, tzinfo=tz)

    monkeypatch.setattr("openpyxl.writer.excel.datetime.datetime", Later)
    second = mark_sheet_xlsx(rows, task())
    assert first == second
    with ZipFile(BytesIO(first)) as archive:
        assert all(entry.date_time == (2026, 1, 1, 0, 0, 0) for entry in archive.infolist())
        core = archive.read("docProps/core.xml")
        assert b"2035" not in core


def test_empty_and_round_half_up_totals() -> None:
    empty = load_workbook(BytesIO(mark_sheet_xlsx((), task())))["Mark sheet"]
    assert empty["A3"].value == "TOTAL" and empty["D3"].value == empty["E3"].value == 0
    sheet = load_workbook(BytesIO(mark_sheet_xlsx((("A", mark("0.005", "0.005")),), task())))[
        "Mark sheet"
    ]
    assert sheet["D4"].value == sheet["E4"].value == 0.01


def test_names_are_text_even_when_they_look_like_formulas() -> None:
    sheet = load_workbook(BytesIO(mark_sheet_xlsx((("=1+1", mark("1", "0.4")),), task())))[
        "Mark sheet"
    ]
    assert sheet["A3"].value == "=1+1" and sheet["A3"].data_type == "s"
