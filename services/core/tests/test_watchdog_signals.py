"""The two log markers the watchdog alarms on (infra/watchdog.sh)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mothusi_contracts import AgentRole, EventKind
from mothusi_core.events import NEEDS_TEACHER, RUN_HALTED, EventSink


def sink(tmp_path: Path) -> EventSink:
    return EventSink(tmp_path / "events.jsonl", "A-1")


def test_a_halt_is_logged_as_an_error_with_its_marker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="mothusi.watchdog"):
        sink(tmp_path).decision("halt", "the run stopped: model host unreachable")
    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.getMessage().startswith(f"{RUN_HALTED} run A-1: halt: the run stopped")


def test_waiting_for_the_teacher_is_logged_as_a_warning_with_its_marker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="mothusi.watchdog"):
        sink(tmp_path).emit(
            EventKind.NEEDS_TEACHER, "memo 2.3 disagrees with the Setter", {}, AgentRole.SETTER
        )
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith(f"{NEEDS_TEACHER} run A-1: memo 2.3 disagrees")


def test_ordinary_events_and_other_decisions_log_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="mothusi.watchdog"):
        s = sink(tmp_path)
        s.decision("withheld", "a note was held back")
        s.emit(EventKind.STATE, "marking", {})
    assert caplog.records == []


def test_the_markers_match_the_infra_watchdog() -> None:
    # infra/watchdog.sh searches the core's log group for these two strings.
    assert (RUN_HALTED, NEEDS_TEACHER) == ("WATCHDOG_RUN_HALTED", "WATCHDOG_NEEDS_TEACHER")
