"""Events are timezone-aware, one-line, and carry their structured payload."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mothusi_contracts import AgentRole, Event, EventKind, LearnerNote


def test_event_round_trips_as_json() -> None:
    event = Event(
        seq=3,
        at=datetime(2026, 11, 13, 11, 5, tzinfo=UTC),
        assessment_id="gr10-maths-t1-test",
        kind=EventKind.CHECK,
        agent=AgentRole.MARKER,
        summary="L-014 Q3: evidence check failed; re-briefing attempt 2",
        payload={"check": "evidence", "passed": False},
    )
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_event_time_must_be_aware_and_summary_one_line() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Event(
            seq=0, at=datetime(2026, 11, 13), assessment_id="a", kind=EventKind.STATE, summary="s"
        )
    with pytest.raises(ValidationError, match="one line"):
        Event(
            seq=0,
            at=datetime.now(UTC),
            assessment_id="a",
            kind=EventKind.STATE,
            summary="two\nlines",
        )


def test_learner_note_needs_all_five_parts() -> None:
    note = LearnerNote(
        pseudonym="L-007",
        outcome="Solving quadratic equations by factorising",
        mark_with_quote='4/4: you wrote "(x-3)(x+2)=0 so x=3 or x=-2" and stated both roots',
        next_task="Exercise 2.3, questions 5 to 8",
        self_assessment_prompt="Which step do you check first when a quadratic will not factorise?",
    )
    assert note.pseudonym == "L-007"
    with pytest.raises(ValidationError):
        LearnerNote(
            pseudonym="L-007",
            outcome="o",
            mark_with_quote="m",
            next_task="n",
            self_assessment_prompt="",
        )
