"""Contract acceptance tests for the delegated rules task."""

import csv
import io
from decimal import Decimal

from mothusi_contracts import ConvertedMark, PoATask
from mothusi_rules import mark_sheet_csv


def test_sorted_quoted_and_total() -> None:
    task = PoATask(term=1, name="Test", kind="test", max_marks=50, weight_percent=Decimal(14))
    first = ConvertedMark(
        script_id="s1", raw=37, out_of=50, sba_points=Decimal("10.36"), year_points=Decimal("4.14")
    )
    second = ConvertedMark(
        script_id="s2", raw=50, out_of=50, sba_points=Decimal("14"), year_points=Decimal("5.6")
    )
    result = mark_sheet_csv((("Zulu", first), ("Alpha, B", second)), task)
    assert (
        result == "learner,raw,out_of,sba_points,year_points\r\n"
        '"Alpha, B",50,50,14.00,5.60\r\n'
        "Zulu,37,50,10.36,4.14\r\n"
        "TOTAL,,,24.36,9.74\r\n"
    )
    assert len(list(csv.reader(io.StringIO(result)))) == 4


def test_empty_sheet() -> None:
    task = PoATask(term=1, name="Test", kind="test", max_marks=50, weight_percent=Decimal(14))
    assert (
        mark_sheet_csv((), task)
        == "learner,raw,out_of,sba_points,year_points\r\nTOTAL,,,0.00,0.00\r\n"
    )


def test_quotes_newlines_and_half_up() -> None:
    task = PoATask(term=1, name="Test", kind="test", max_marks=50, weight_percent=Decimal(14))
    mark = ConvertedMark(
        script_id="s", raw=0, out_of=50, sba_points=Decimal("0.005"), year_points=Decimal("0.005")
    )
    result = mark_sheet_csv((('A "B"\nC', mark),), task)
    rows = list(csv.reader(io.StringIO(result)))
    assert rows[1] == ['A "B"\nC', "0", "50", "0.01", "0.01"]
    assert rows[-1] == ["TOTAL", "", "", "0.01", "0.01"]
