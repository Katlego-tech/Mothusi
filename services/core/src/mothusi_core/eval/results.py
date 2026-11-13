"""Eval result contracts from docs/design/eval.md §6."""

from datetime import date
from decimal import Decimal

from pydantic import Field

from mothusi_contracts import Contract


class TrapCatch(Contract):
    trap: str = Field(min_length=1)
    caught: bool
    evidence: str


class LatencySummary(Contract):
    role: str = Field(min_length=1)  # AgentRole value, or "reader"
    model: str = Field(min_length=1)
    calls: int = Field(ge=1)
    p50_s: Decimal = Field(ge=0)
    p95_s: Decimal = Field(ge=0)
    max_s: Decimal = Field(ge=0)
    limit_s: Decimal | None = None
    over_limit: int = Field(ge=0)


class CostBasis(Contract):
    instance: str = Field(min_length=1)  # "g4dn.xlarge" | "laptop" | ...
    instance_usd_per_hour: Decimal = Field(ge=0)
    stack_usd_per_hour: Decimal = Field(ge=0)
    zar_per_usd: Decimal = Field(gt=0)
    sources: tuple[str, ...]  # copied from pricing.toml `source` fields


class ReaderAccuracy(Contract):
    pages: int = Field(ge=1)
    lines: int = Field(ge=1)
    line_exact_rate: Decimal = Field(ge=0, le=1)
    cer: Decimal = Field(ge=0)
    uncertain_lines: int = Field(ge=0)
    uncertain_and_wrong: int = Field(ge=0)
    wrong_not_flagged: int = Field(ge=0)


class EvalResult(Contract):
    run_date: date
    git_commit: str = Field(min_length=7)
    fixture: str
    scripts: int = Field(ge=1)
    instance: str
    models: dict[str, str]  # role -> model actually called (from Timed)
    minutes_to_approval: Decimal | None
    minutes_to_delivered: Decimal | None
    halted: str | None = None
    questions_compared: int = Field(ge=0)
    disagreements: int = Field(ge=0)
    agreement_rate: Decimal | None  # None when questions_compared == 0
    traps: tuple[TrapCatch, ...]
    teacher_questions: tuple[str, ...]
    latency: tuple[LatencySummary, ...]
    cost_zar_set: Decimal | None
    cost_zar_script: Decimal | None
    cost_basis: CostBasis
    reader: ReaderAccuracy | None
    reader_skipped: str = ""


class CallTiming(Contract):
    role: str
    model: str
    seconds: float
    input_tokens: int
    output_tokens: int
