"""ATP placement uses supplied, sourced weeks; the rows here are synthetic test data."""

from pathlib import Path

import pytest

from mothusi_contracts import AtpWeek, CatchUpItem
from mothusi_rules import CATCH_UP, check_catch_up, load_atp_weeks


def test_load_and_sort(tmp_path: Path) -> None:
    path = tmp_path / "atp.toml"
    path.write_text(
        '[[week]]\nweek=5\ntopic="Topic B"\nsource="test row B"\n'
        '[[week]]\nweek=3\ntopic="Topic A"\nsource="test row A"\n'
    )
    weeks = load_atp_weeks(path)
    assert [w.week for w in weeks] == [3, 5]
    assert weeks[0].topic == "Topic A" and weeks[0].source == "test row A"


@pytest.mark.parametrize(
    "content",
    [
        '[[week]]\nweek=3\ntopic="A"\nsource="S"\n[[week]]\nweek=3\ntopic="B"\nsource="T"',
        '[[week]]\nweek=0\ntopic="A"\nsource="S"',
        '[[week]]\nweek=3\ntopic="A"',
        '[[week]]\nweek=3\ntopic="A"\nsource="S"\nextra=1',
    ],
)
def test_invalid_weeks(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_atp_weeks(path)


def remaining() -> tuple[AtpWeek, ...]:
    return (
        AtpWeek(week=3, topic="A", source="test source A"),
        AtpWeek(week=5, topic="B", source="test source B"),
    )


def activity(week: int = 3, source: str = "test source A", topic: str = "A") -> CatchUpItem:
    return CatchUpItem(week=week, topic=topic, activity="Practice", atp_source=source)


def test_placed_plan() -> None:
    result = check_catch_up((activity(),), remaining())
    assert result.passed and result.check == CATCH_UP == "catch_up_weeks"
    assert result.about == "catch-up plan"
    assert result.detail == "1 items placed in 2 remaining weeks"


def test_collects_missing_week_and_bad_source() -> None:
    result = check_catch_up(
        (activity(4, topic="Missing"), activity(source="wrong", topic="Wrong")), remaining()
    )
    assert not result.passed
    assert result.detail == (
        "Missing: week 4 is not in the remaining ATP; Wrong: source does not match week 3"
    )


def test_empty_plan_and_no_remaining() -> None:
    result = check_catch_up((), remaining())
    assert result.passed and result.detail == "0 items placed in 2 remaining weeks"
    assert check_catch_up((), ()).passed
    assert not check_catch_up((activity(),), ()).passed
