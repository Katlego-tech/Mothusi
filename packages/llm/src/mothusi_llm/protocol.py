"""The one interface every agent calls a model through. Design: docs/architecture/design/llm-seam.md §6.

Adapters (anthropic, ollama, replay) implement `Provider`; agents never import an SDK.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from mothusi_contracts import Contract


class Usage(Contract):
    """What one call cost: tokens and rand. Summed per assessment by the meter."""

    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_zar: Decimal = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def sum_usage(usages: Iterable[Usage]) -> tuple[int, int, Decimal]:
    """Input tokens, output tokens and rand across many calls (models may differ)."""
    inputs = outputs = 0
    cost = Decimal(0)
    for usage in usages:
        inputs += usage.input_tokens
        outputs += usage.output_tokens
        cost += usage.cost_zar
    return inputs, outputs, cost


type Pricer = Callable[[str, int, int], Decimal]
"""(model, input_tokens, output_tokens) -> rand. The meter supplies one (llm-seam.md §6)."""


class ProviderError(Exception):
    """A call that did not produce a valid result. `retryable` says whether trying again is sane."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    """Structured completion: the model's answer parsed into a contract model, plus its usage."""

    name: str

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]: ...

    # `images`: PNG or JPEG bytes attached to the user turn, in order (llm-seam.md §11.1). A
    # provider that cannot take images raises ProviderError(retryable=False).
