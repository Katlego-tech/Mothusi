"""Summarise blind moderation disagreements from an eval's JSONL event log."""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from mothusi_contracts import Event, EventKind


class _Pen(BaseModel):
    model_config = ConfigDict(strict=True)
    awarded: int = Field(ge=0)


class _Payload(BaseModel):
    # Evidence and memo fields remain in the source log; only aggregate inputs are read here.
    model_config = ConfigDict(strict=True)
    script_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    first: _Pen
    second: _Pen
    max_marks: int | None = Field(default=None, gt=0)


@dataclass(frozen=True)
class QuestionDisagreements:
    """Counts and mark gaps for one question across scripts."""

    count: int
    second_higher: int
    second_lower: int
    mean_absolute_gap: float
    max_marks: int | None


@dataclass(frozen=True)
class DisagreementReport:
    """Event counts, with questions ranked by descending count then question ID."""

    total_disagreements: int
    per_question: dict[str, QuestionDisagreements]
    per_script: dict[str, int]
    ranked_questions: tuple[str, ...]


def report(events: Sequence[Event]) -> DisagreementReport:
    """Read structured payloads; count every disagreement event, including repeated ones.

    Invalid payloads or conflicting question maxima raise ValueError rather than producing
    a partial report. Missing maxima remain unknown unless another event supplies one.
    """
    grouped: dict[str, list[_Payload]] = defaultdict(list)
    scripts: Counter[str] = Counter()
    for event in events:
        if event.kind != EventKind.DISAGREEMENT:
            continue
        payload = _Payload.model_validate(event.payload)
        grouped[payload.question_id].append(payload)
        scripts[payload.script_id] += 1

    questions: dict[str, QuestionDisagreements] = {}
    for question_id, rows in sorted(grouped.items()):
        maxima = {row.max_marks for row in rows if row.max_marks is not None}
        if len(maxima) > 1:
            raise ValueError(f"question {question_id}: conflicting max_marks")
        questions[question_id] = QuestionDisagreements(
            count=len(rows),
            second_higher=sum(row.second.awarded > row.first.awarded for row in rows),
            second_lower=sum(row.second.awarded < row.first.awarded for row in rows),
            mean_absolute_gap=sum(abs(row.second.awarded - row.first.awarded) for row in rows)
            / len(rows),
            max_marks=next(iter(maxima), None),
        )
    ranked = tuple(sorted(questions, key=lambda q: (-questions[q].count, q)))
    return DisagreementReport(
        sum(scripts.values()), questions, dict(sorted(scripts.items())), ranked
    )


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(row: tuple[str, ...]) -> str:
        return "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()

    return "\n".join([line(headers), line(tuple("-" * w for w in widths)), *map(line, rows)])


def render(report: DisagreementReport) -> str:
    """Render question statistics in rank order and script counts in script ID order."""
    title = f"Total disagreements: {report.total_disagreements}"
    if not report.total_disagreements:
        return f"{title}\nNo disagreements."
    rows: list[tuple[str, ...]] = []
    for question_id in report.ranked_questions:
        q = report.per_question[question_id]
        rows.append(
            (
                question_id,
                str(q.count),
                str(q.second_higher),
                str(q.second_lower),
                f"{q.mean_absolute_gap:.2f}",
                str(q.max_marks) if q.max_marks is not None else "-",
            )
        )
    questions = _table(("Question", "Count", "Higher", "Lower", "Mean gap", "Max marks"), rows)
    scripts = _table(
        ("Script", "Count"), [(sid, str(count)) for sid, count in sorted(report.per_script.items())]
    )
    return f"{title}\n\n{questions}\nHigher/Lower: second mark compared with first.\n\n{scripts}"


def main(argv: Sequence[str] | None = None) -> int:
    """Read one Event per line; file, event and payload errors exit with a single diagnostic."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path, help="eval event log (JSONL)")
    path: Path = parser.parse_args(argv).events
    line_number = 0
    try:
        events: list[Event] = []
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                line_number = number
                event = Event.model_validate_json(line)
                # Validate here as well so a bad payload's diagnostic identifies its source line.
                if event.kind == EventKind.DISAGREEMENT:
                    _Payload.model_validate(event.payload)
                events.append(event)
        rendered = render(report(events))
    except (OSError, ValueError) as exc:
        detail = " ".join(str(exc).split())
        location = f" at line {line_number}" if line_number else ""
        print(f"Cannot report {str(path)!r}{location}: {detail}", file=sys.stderr)
        return 2
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
