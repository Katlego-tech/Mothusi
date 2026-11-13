"""An append-only, sequence-checked assessment event log in UTF-8 JSONL."""

import fcntl
from collections.abc import Iterable
from pathlib import Path

from mothusi_contracts import Event


def _decode(lines: Iterable[bytes]) -> tuple[Event, ...]:
    events: list[Event] = []
    for number, line in enumerate(lines, start=1):
        try:
            if not line.endswith(b"\n"):
                raise ValueError("unterminated event")
            event = Event.model_validate_json(line)
            if event.seq != len(events):
                raise ValueError(f"expected seq {len(events)}, got {event.seq}")
            if events and event.assessment_id != events[0].assessment_id:
                raise ValueError("assessment_id differs from the log")
        except ValueError as error:
            raise ValueError(f"line {number}: {error}") from error
        events.append(event)
    return tuple(events)


class EventLog:
    """Read current disk state on every operation; never silently truncate a bad log."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def next_seq(self) -> int:
        return len(self.read())

    @property
    def assessment_id(self) -> str | None:
        events = self.read()
        return events[0].assessment_id if events else None

    def append(self, event: Event) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a+b") as destination:
            # Readers share this lock; independent writers cannot both claim one sequence.
            fcntl.flock(destination.fileno(), fcntl.LOCK_EX)
            destination.seek(0)
            existing = _decode(destination)
            if event.seq != len(existing):
                raise ValueError(f"expected seq {len(existing)}, got {event.seq}")
            if existing and event.assessment_id != existing[0].assessment_id:
                raise ValueError("assessment_id differs from the log")
            destination.write((event.model_dump_json() + "\n").encode("utf-8"))
            destination.flush()

    def read(self) -> tuple[Event, ...]:
        if not self._path.exists():
            return ()
        with self._path.open("rb") as source:
            fcntl.flock(source.fileno(), fcntl.LOCK_SH)
            return _decode(source)

    def tail(self, n: int) -> tuple[Event, ...]:
        if n < 1:
            raise ValueError("tail count must be at least 1")
        return self.read()[-n:]
