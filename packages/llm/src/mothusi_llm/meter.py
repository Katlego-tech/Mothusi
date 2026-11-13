"""Sourced model pricing and deterministic usage accounting in rand."""

import tomllib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from mothusi_contracts import Contract
from mothusi_llm.protocol import Usage, sum_usage


class _Rates(Contract):
    zar_per_usd: Decimal = Field(gt=0)
    source: str = Field(min_length=1)


class _ModelPrice(Contract):
    name: str = Field(min_length=1)
    usd_per_million_input: Decimal = Field(ge=0)
    usd_per_million_output: Decimal = Field(ge=0)
    source: str = Field(min_length=1)


class InstancePrice(Contract):
    """Sourced hourly price of a model host."""

    name: str
    usd_per_hour: Decimal = Field(ge=0)
    source: str


class StackPrice(Contract):
    """Sourced hourly price of the rest of the stack."""

    usd_per_hour: Decimal = Field(ge=0)
    source: str


class Pricing(Contract):
    """The pricing file, including provenance for both FX and each model's prices."""

    rates: _Rates
    model: tuple[_ModelPrice, ...] = Field(min_length=1)

    instance: tuple[InstancePrice, ...] = ()
    stack: StackPrice | None = None

    @model_validator(mode="after")
    def _unique_models(self) -> Self:
        if len({model.name for model in self.model}) != len(self.model):
            raise ValueError("pricing model names must be unique")
        return self


def load_pricing(path: Path) -> Pricing:
    """Load validated prices without rounding TOML decimal values through binary floats."""
    with path.open("rb") as source:
        return Pricing.model_validate(tomllib.load(source, parse_float=Decimal))


def price(pricing: Pricing, model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Price one call in rand, half-up to four decimal places."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    for entry in pricing.model:
        if entry.name == model:
            usd = (
                entry.usd_per_million_input * input_tokens
                + entry.usd_per_million_output * output_tokens
            ) / 1_000_000
            return (usd * pricing.rates.zar_per_usd).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
    raise ValueError(
        f"unknown model {model!r}; known models: "
        + ", ".join(entry.name for entry in pricing.model)
    )


def instance_price(pricing: Pricing, name: str) -> InstancePrice:
    """Look up a host, naming the available hosts when the name is unknown."""
    for entry in pricing.instance:
        if entry.name == name:
            return entry
    raise ValueError(
        f"unknown instance {name!r}; known instances: "
        + ", ".join(entry.name for entry in pricing.instance)
    )


class Meter:
    """Accumulate call usage, exposing token/cost totals and rand per script."""

    def __init__(self) -> None:
        self._usages: list[Usage] = []

    def add(self, usage: Usage) -> None:
        self._usages.append(usage)

    def total(self) -> tuple[int, int, Decimal]:
        return sum_usage(self._usages)

    def per_script(self, n: int) -> Decimal:
        if n <= 0:
            raise ValueError("script count must be positive")
        return (self.total()[2] / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
