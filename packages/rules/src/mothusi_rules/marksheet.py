"""Produce the teacher's mark sheet as spreadsheet-compatible CSV."""

import csv
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO

from mothusi_contracts import ConvertedMark, PoATask


def mark_sheet_csv(rows: Sequence[tuple[str, ConvertedMark]], task: PoATask) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(("learner", "raw", "out_of", "sba_points", "year_points"))
    sba_total = Decimal(0)
    year_total = Decimal(0)
    for learner, mark in sorted(rows, key=lambda row: row[0]):
        sba_total += mark.sba_points
        year_total += mark.year_points
        writer.writerow(
            (learner, mark.raw, mark.out_of, _points(mark.sba_points), _points(mark.year_points))
        )
    writer.writerow(("TOTAL", "", "", _points(sba_total), _points(year_total)))
    return output.getvalue()


def _points(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")
