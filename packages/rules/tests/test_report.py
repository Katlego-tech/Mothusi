"""Reports render computed facts in deterministic, teacher-readable markdown."""

from decimal import Decimal

import pytest

from mothusi_contracts import Disagreement, ItemStat
from mothusi_rules import class_feedback_md, moderation_report_md


def gaps() -> tuple[Disagreement, ...]:
    return (
        Disagreement(script_id="s2", question_id="q1", first=2, second=5),
        Disagreement(script_id="s1", question_id="q2", first=3, second=1),
    )


def test_sections_facts_and_signatures() -> None:
    result = moderation_report_md("Test", ("s2", "s1"), gaps(), {"s2/q1": "confirmed"}, 10, "M")
    headings = [line for line in result.splitlines() if line.startswith("#")]
    assert headings == [
        "# Moderation report: Test",
        "## Sample",
        "## Disagreements",
        "## Agreement",
        "## Signatures",
    ]
    assert result.index("| s2 |") < result.index("| s1 |")
    assert "| s2 | q1 | 2 | 5 | 3 | confirmed |" in result
    assert "| s1 | q2 | 3 | 1 | 2 | unresolved |" in result
    assert "80.00% (8 of 10 questions)" in result
    for line in [
        "Marker: ____________",
        "Moderator (M): ____________",
        "Head of Department: ____________  Date: ____________",
    ]:
        assert line in result
    assert "\r" not in result
    assert all(line == line.rstrip() for line in result.splitlines())


def test_no_disagreements_and_rounding() -> None:
    result = moderation_report_md("Test", (), (), {}, 3, "M")
    assert "No disagreements in the sample." in result
    assert "100.00% (3 of 3 questions)" in result
    assert "66.67% (2 of 3 questions)" in moderation_report_md("T", ("s2",), gaps()[:1], {}, 3, "M")


@pytest.mark.parametrize("count", [0, -1, 1])
def test_invalid_comparison_count(count: int) -> None:
    with pytest.raises(ValueError):
        moderation_report_md("T", (), gaps(), {}, count, "M")


def item(qid: str, facility: str) -> ItemStat:
    return ItemStat(
        question_id=qid,
        max_marks=100,
        scripts=1,
        mean_awarded=Decimal(facility),
        facility_percent=Decimal(facility),
        flag="re-teach" if Decimal(facility) < 40 else None,
    )


def test_class_rankings_and_ties() -> None:
    stats = (item("q3", "90"), item("q2", "20"), item("q1", "20"), item("q4", "100"))
    result = class_feedback_md(stats, 2)
    weakest, strongest = result.split("## Strongest")
    assert weakest.index("| q1 |") < weakest.index("| q2 |")
    assert "| q3 |" not in weakest
    assert strongest.index("| q4 |") < strongest.index("| q3 |")
    assert "20.00" in weakest and "re-teach" in weakest
    assert result.startswith("# Class feedback\n")


def test_small_class_and_strong_ties() -> None:
    result = class_feedback_md((item("q2", "90"), item("q1", "90")))
    for section in result.split("## ")[1:]:
        assert section.index("| q1 |") < section.index("| q2 |")
    assert result.count("| q1 |") == 2


def test_invalid_class_feedback() -> None:
    with pytest.raises(ValueError):
        class_feedback_md(())
    with pytest.raises(ValueError):
        class_feedback_md((item("q", "50"),), 0)


def test_table_cells_escape_delimiters() -> None:
    result = moderation_report_md("T", ("s|1",), (), {}, 1, "M")
    assert "| s\\|1 |" in result
