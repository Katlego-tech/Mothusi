"""Agent envelopes: the Brief one agent sends and the Result it gets back.

Design: docs/architecture/design/marking-pipeline.md §6 and the agent contracts. The Planner speaks to every
other
agent only through these two shapes, so a handoff is a visible, loggable thing.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field

from mothusi_contracts.models import Contract


class AgentRole(StrEnum):
    PLANNER = "planner"
    INTAKE = "intake"
    SETTER = "setter"
    MARKER = "marker"
    MODERATOR = "moderator"
    ANALYST = "analyst"
    SUPPORT = "support"
    WRITER = "writer"
    REVIEWER = "reviewer"
    EXECUTOR = "executor"


class ResultStatus(StrEnum):
    DONE = "done"
    RETRY = "retry"
    BLOCKED = "blocked"
    NEEDS_TEACHER = "needs_teacher"


class Brief(Contract):
    """What the Planner asks one agent to do, with everything that agent needs and nothing else."""

    brief_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    agent: AgentRole
    task: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(ge=1, default=1)
    budget_tokens: int | None = Field(default=None, gt=0)


class Result(Contract):
    """What the agent hands back. `evidence` names what the outputs rest on (memo lines, quotes)."""

    brief_id: str = Field(min_length=1)
    agent: AgentRole
    status: ResultStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    reason: str = ""
    cost_zar: Decimal = Field(ge=0, default=Decimal(0))
