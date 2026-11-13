"""Deterministic moderation and class feedback documents from computed contracts."""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from mothusi_contracts import Disagreement, ItemStat


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("\\", "\\\\").replace("|", "\\|")


def moderation_report_md(
    assessment_title: str,
    sample: Sequence[str],
    disagreements: Sequence[Disagreement],
    resolutions: Mapping[str, str],
    questions_compared: int,
    moderator: str,
) -> str:
    """Render the sample, disagreements, agreement rate and required sign-offs."""
    if questions_compared <= 0 or questions_compared < len(disagreements):
        raise ValueError("questions_compared must be positive and cover every disagreement")
    lines = [
        f"# Moderation report: {_cell(assessment_title)}",
        "",
        "## Sample",
        "",
        "| script | included |",
        "| --- | --- |",
    ]
    lines.extend(f"| {_cell(script)} | yes |" for script in sample)
    lines.extend(("", "## Disagreements", ""))
    if disagreements:
        lines.extend(
            (
                "| script | question | first mark | second mark | gap | resolution |",
                "| --- | --- | --- | --- | --- | --- |",
            )
        )
        for gap in disagreements:
            resolution = resolutions.get(f"{gap.script_id}/{gap.question_id}", "unresolved")
            lines.append(
                f"| {_cell(gap.script_id)} | {_cell(gap.question_id)} | {gap.first} | "
                f"{gap.second} | {gap.gap} | {_cell(resolution)} |"
            )
    else:
        lines.append("No disagreements in the sample.")
    agreed = questions_compared - len(disagreements)
    rate = (Decimal(agreed) / questions_compared * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    lines.extend(
        (
            "",
            "## Agreement",
            "",
            f"Agreement rate on the sample: {rate}% ({agreed} of {questions_compared} questions).",
            "",
            "## Signatures",
            "",
            "Marker: ____________",
            f"Moderator ({_cell(moderator)}): ____________",
            "Head of Department: ____________  Date: ____________",
        )
    )
    return "\n".join(lines) + "\n"


def class_feedback_md(item_stats: Sequence[ItemStat], top_n: int = 3) -> str:
    """List the lowest and highest facilities, with question ids breaking ties."""
    if not item_stats or top_n < 1:
        raise ValueError("class feedback needs item statistics and top_n >= 1")
    weakest = sorted(item_stats, key=lambda item: (item.facility_percent, item.question_id))
    strongest = sorted(item_stats, key=lambda item: (-item.facility_percent, item.question_id))
    lines = ["# Class feedback"]
    for title, items in (("Weakest questions", weakest), ("Strongest", strongest)):
        lines.extend(
            ("", f"## {title}", "", "| question | facility % | flag |", "| --- | --- | --- |")
        )
        for item in items[:top_n]:
            facility = item.facility_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            lines.append(f"| {_cell(item.question_id)} | {facility} | {item.flag or ''} |")
    return "\n".join(lines) + "\n"
