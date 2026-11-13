"""Both Grade 10 packs' full-year ATP weeks, read from the held 2023/24 ATP pages."""

from pathlib import Path

import pytest

from mothusi_rules import load_atp_weeks

ATP = Path(__file__).resolve().parents[3] / "packs/dbe-caps/atp"


@pytest.mark.parametrize("subject", ["mathematics", "accounting"])
def test_every_teaching_week_of_the_year_is_there_once_with_its_page(subject: str) -> None:
    weeks = load_atp_weeks(ATP / f"{subject}-gr10.toml")
    assert [w.week for w in weeks] == list(range(1, 44))  # 11 + 11 + 11 + 10 weeks
    title = f"ATP 2023-24 Gr 10 {subject.title()} (DBE), PDF p "
    assert all(w.source.startswith(title) for w in weeks)


def test_spot_rows_match_the_printed_pages() -> None:
    maths = {w.week: w for w in load_atp_weeks(ATP / "mathematics-gr10.toml")}
    assert maths[9].topic == "Trigonometry"  # Term 1 week 9, PDF p 1
    assert maths[34].topic == "Measurement" and maths[34].source.endswith("Term 4 week 1")
    acc = {w.week: w for w in load_atp_weeks(ATP / "accounting-gr10.toml")}
    assert "accounting equation" in acc[3].topic  # Term 1 weeks 3-5, PDF p 1
    assert acc[17].topic == "Salaries and wages"
    assert acc[17].source.endswith("PDF p 2, Term 2 week 6")
    assert acc[36].topic == "Budgeting"  # Term 4 week 3, PDF p 4
