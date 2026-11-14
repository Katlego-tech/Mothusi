"""The event log's one record type. Design: docs/architecture/design/event-log.md §3.

One Event per Brief, Result, check, decision, state change and teacher action. The console streams
them; the moderation record is read back from them; nothing in Mothusi is decided off the record.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from mothusi_contracts.envelopes import AgentRole
from mothusi_contracts.models import Contract


class EventKind(StrEnum):
    """The console's fourteen kinds, including teacher actions and conversation events."""

    CREATED = "created"
    STATE = "state"
    BRIEF = "brief"
    RESULT = "result"
    CHECK = "check"
    DECISION = "decision"
    DISAGREEMENT = "disagreement"
    NEEDS_TEACHER = "needs_teacher"
    USAGE = "usage"
    DELIVERED = "delivered"
    TEACHER = "teacher"
    MESSAGE = "message"
    REPLY = "reply"
    NOTE_SENT = "note_sent"


class Event(Contract):
    """One line of the live log, with the structured payload behind it."""

    seq: int = Field(ge=0, description="Position in the log; the EventLog assigns and enforces it")
    at: datetime = Field(description="Timezone-aware")
    assessment_id: str = Field(min_length=1)
    kind: EventKind
    agent: AgentRole | None = None
    summary: str = Field(min_length=1, max_length=200, description="One line a teacher can read")
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aware_and_one_line(self) -> Self:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("Event.at must be timezone-aware")
        if "\n" in self.summary or "\r" in self.summary:
            raise ValueError("Event.summary is one line")
        return self
