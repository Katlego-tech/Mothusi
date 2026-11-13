"""Disagreement aggregation from structured event payloads and JSONL CLI errors."""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from mothusi_contracts import Event, EventKind
from mothusi_core.eval.disagreements import main, render, report


def event(
    script: str = "s1",
    question: str = "1.1",
    first: int = 1,
    second: int = 3,
    maximum: int | None = 5,
) -> Event:
    payload: dict[str, JsonValue] = {
        "script_id": script,
        "question_id": question,
        "first": {"awarded": first, "evidence_quote": "x=3", "memo_line": "x=3"},
        "second": {"awarded": second, "evidence_quote": "x=3", "memo_line": "x=3"},
        "question": "Solve for x",
        "memo_answer": "x=3",
        "marking_notes": "Award method marks",
        "answer": "x=3",
        "reason": "Different method credit",
    }
    if maximum is not None:
        payload["max_marks"] = maximum
    return Event(
        seq=0,
        at=datetime(2026, 11, 13, tzinfo=UTC),
        assessment_id="eval-run",
        kind=EventKind.DISAGREEMENT,
        summary="misleading Q99: first 99, second 0",
        payload=payload,
    )


def sample() -> list[Event]:
    return [
        event("s1", "2", 0, 3),
        event("s2", "1", 4, 2),
        event("s2", "2", 4, 3),
        event("s3", "2", 2, 3, None),
        event("s3", "3", 1, 0, None),
        Event(
            seq=5,
            at=datetime(2026, 11, 13, tzinfo=UTC),
            assessment_id="eval-run",
            kind=EventKind.CHECK,
            summary="not a disagreement",
        ),
    ]


def test_report_counts_directions_gaps_scripts_and_ranking() -> None:
    events = sample()
    result = report(events)
    assert result.total_disagreements == 5
    assert result.per_script == {"s1": 1, "s2": 2, "s3": 2}
    assert result.ranked_questions == ("2", "1", "3")
    assert set(result.per_question) == {"1", "2", "3"}
    q = result.per_question["2"]
    assert (q.count, q.second_higher, q.second_lower) == (3, 2, 1)
    assert q.mean_absolute_gap == pytest.approx(5 / 3)
    assert q.max_marks == 5
    assert result.per_question["1"].mean_absolute_gap == 2
    assert result.per_question["3"].max_marks is None
    assert report(list(reversed(events))) == result
    assert events == sample()


@pytest.mark.parametrize("events", [[], [sample()[-1]]])
def test_empty_report(events: list[Event]) -> None:
    result = report(events)
    assert result.total_disagreements == 0
    assert result.per_question == {}
    assert result.per_script == {}
    assert result.ranked_questions == ()
    assert render(result) == "Total disagreements: 0\nNo disagreements."


def test_equal_marks_do_not_count_as_either_direction() -> None:
    q = report([event(first=2, second=2)]).per_question["1.1"]
    assert (q.count, q.second_higher, q.second_lower, q.mean_absolute_gap) == (1, 0, 0, 0)


def test_render_shows_ranked_question_statistics_and_script_counts() -> None:
    lines = render(report(sample())).splitlines()
    assert lines[0] == "Total disagreements: 5"
    header = next(line for line in lines if "Question" in line)
    assert all(label in header for label in ("Count", "Higher", "Lower", "Mean gap", "Max marks"))
    rows = [line.split() for line in lines if line.split() and line.split()[0] in {"1", "2", "3"}]
    assert rows == [
        ["2", "3", "2", "1", "1.67", "5"],
        ["1", "1", "0", "1", "2.00", "5"],
        ["3", "1", "0", "1", "1.00", "-"],
    ]
    assert "Higher/Lower: second mark compared with first." in lines
    assert [line.split() for line in lines if line.startswith("s")] == [
        ["s1", "1"],
        ["s2", "2"],
        ["s3", "2"],
    ]


@pytest.mark.parametrize(
    "key,value",
    [("first", 2), ("second", {"awarded": -1}), ("script_id", ""), ("max_marks", "five")],
)
def test_invalid_disagreement_is_not_silently_omitted(key: str, value: JsonValue) -> None:
    record = event()
    record.payload[key] = value
    with pytest.raises(ValueError):
        report([record])


def test_conflicting_maxima_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_marks"):
        report([event(maximum=4), event(maximum=5)])


def test_cli_reads_real_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(e.model_dump_json() for e in sample()) + "\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "mothusi_core.eval.disagreements", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == render(report(sample())) + "\n"
    assert completed.stderr == ""


@pytest.mark.parametrize("failure", ["missing", "directory", "json", "event", "payload", "utf8"])
def test_cli_errors_are_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    path = tmp_path / "events.jsonl"
    if failure == "directory":
        path.mkdir()
    elif failure == "utf8":
        path.write_bytes(b"\xff")
    elif failure != "missing":
        invalid = event()
        invalid.payload.pop("first")
        text = {"json": "{", "event": "{}", "payload": invalid.model_dump_json()}[failure]
        path.write_text(event().model_dump_json() + "\n" + text, encoding="utf-8")
    assert main([str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err
    assert "events.jsonl" in captured.err
    if failure in {"json", "event", "payload"}:
        assert "line 2" in captured.err


def test_cli_empty_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "Total disagreements: 0\nNo disagreements.\n"
