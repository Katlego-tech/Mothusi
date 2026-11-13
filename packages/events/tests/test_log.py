"""Append-only JSONL logs retain every event and refuse gaps, mixing and corruption."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mothusi_contracts import Event, EventKind
from mothusi_events import EventLog


def event(seq: int, assessment: str = "a") -> Event:
    return Event(
        seq=seq,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        assessment_id=assessment,
        kind=EventKind.CHECK,
        summary="Check complete",
        payload={"check": "evidence", "passed": True},
    )


def test_roundtrip_tail_and_lazy_file_creation(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    log = EventLog(path)
    assert not path.exists() and log.next_seq() == 0 and log.assessment_id is None
    assert log.read() == () and log.tail(2) == ()
    for index in range(3):
        log.append(event(index))
    assert log.read() == tuple(event(i) for i in range(3))
    assert log.next_seq() == 3 and log.assessment_id == "a"
    assert log.tail(2) == (event(1), event(2))
    assert log.tail(99) == log.read()
    assert path.read_bytes().endswith(b"\n") and b"\r" not in path.read_bytes()
    assert len(path.read_bytes().splitlines()) == 3
    reopened = EventLog(path)
    assert reopened.read() == log.read() and reopened.next_seq() == 3


def test_invalid_sequence_and_assessment_do_not_change_file(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = EventLog(path)
    log.append(event(0))
    log.append(event(1))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="expected seq 2, got 5"):
        log.append(event(5))
    with pytest.raises(ValueError, match="assessment"):
        log.append(event(2, "other"))
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "bad", ["not JSON", "{}", "", event(9).model_dump_json(), event(1, "b").model_dump_json()]
)
def test_corrupt_line_names_line(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text(event(0).model_dump_json() + "\n" + bad + "\n")
    with pytest.raises(ValueError, match="line 2:"):
        EventLog(path).read()


@pytest.mark.parametrize("count", [0, -1])
def test_tail_rejects_nonpositive(tmp_path: Path, count: int) -> None:
    with pytest.raises(ValueError):
        EventLog(tmp_path / "events").tail(count)


def test_independent_instances_see_appends(tmp_path: Path) -> None:
    path = tmp_path / "events"
    first, second = EventLog(path), EventLog(path)
    first.append(event(0))
    assert second.next_seq() == 1
    second.append(event(1))
    assert first.read() == (event(0), event(1))


def test_invalid_utf8_names_corrupt_line(tmp_path: Path) -> None:
    path = tmp_path / "events"
    path.write_bytes(event(0).model_dump_json().encode("utf-8") + b"\n\xff\n")
    with pytest.raises(ValueError, match="line 2:"):
        EventLog(path).read()
