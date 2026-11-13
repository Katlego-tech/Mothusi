"""Provider timing for the eval, including calls that fail (eval.md §6)."""

from __future__ import annotations

import time
from collections.abc import Sequence

from mothusi_contracts import Contract
from mothusi_core.eval.results import CallTiming
from mothusi_llm import Provider, Usage


class Timed:
    """Record wall time and returned usage without changing provider behaviour."""

    def __init__(self, inner: Provider, role: str, model: str, sink: list[CallTiming]) -> None:
        self.name = inner.name
        self._inner = inner
        self._role = role
        self._model = model
        self._sink = sink

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        usage: Usage | None = None
        started = time.perf_counter()
        try:
            reply, usage = await self._inner.complete(
                system, user, output, budget_tokens=budget_tokens, images=images
            )
            return reply, usage
        finally:
            self._sink.append(
                CallTiming(
                    role=self._role,
                    model=usage.model if usage else self._model,
                    seconds=time.perf_counter() - started,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                )
            )
