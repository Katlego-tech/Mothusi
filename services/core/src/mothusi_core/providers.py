"""The Planner's side of the provider seam: fallback, metering and recording.

Design: docs/design/llm-seam.md §4 (the fallback order is the Planner's: primary, then fallback,
then the agent's own retry/needs_teacher) and marking-pipeline.md §6.2. Agents see one `Provider`;
these wrappers are composed per run by the Planner and the API.
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from mothusi_contracts import Contract
from mothusi_llm import (
    AnthropicProvider,
    OllamaProvider,
    Pricer,
    Provider,
    ProviderError,
    ReplayProvider,
    Usage,
    load_pricing,
    price,
    record,
)

type OnFallback = Callable[[str, str, str], None]
"""(primary name, fallback name, error) -> None: the Planner logs a decision event."""


class Fallback:
    """Ask the primary; on any ProviderError ask the fallback once and say so."""

    def __init__(
        self, primary: Provider, fallback: Provider | None, on_fallback: OnFallback
    ) -> None:
        self.name = primary.name if fallback is None else f"{primary.name}+{fallback.name}"
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        try:
            return await self._primary.complete(
                system, user, output, budget_tokens=budget_tokens, images=images
            )
        except ProviderError as error:
            if self._fallback is None:
                raise
            self._on_fallback(self._primary.name, self._fallback.name, str(error))
            return await self._fallback.complete(
                system, user, output, budget_tokens=budget_tokens, images=images
            )


class Slots:
    """At most `size` model calls in flight, shared by every provider of one Planner.

    The model host decodes a few requests at once (OLLAMA_NUM_PARALLEL, deployment-aws.md §6.2) and
    queues the rest, and a client's timeout counts that queueing. Waiting here instead keeps the
    timeout for the host's own work (2026-11-12: a stage that sent 6-33 calls at once timed out).
    One semaphore per event loop, since a Planner can be driven from more than one.
    """

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError(f"slots must be at least 1, not {size}")
        self.size = size
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

    def semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if (held := self._by_loop.get(loop)) is None:
            held = self._by_loop[loop] = asyncio.Semaphore(self.size)
        return held


class Limited:
    """Hold one of the shared `Slots` for the length of each call."""

    def __init__(self, inner: Provider, slots: Slots) -> None:
        self.name = inner.name
        self._inner = inner
        self._slots = slots

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        async with self._slots.semaphore():
            return await self._inner.complete(
                system, user, output, budget_tokens=budget_tokens, images=images
            )


class Metered:
    """Collect every call's Usage so the Planner can report tokens and rand (the usage event)."""

    def __init__(self, inner: Provider) -> None:
        self.name = inner.name
        self._inner = inner
        self._usages: list[Usage] = []

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        reply, usage = await self._inner.complete(
            system, user, output, budget_tokens=budget_tokens, images=images
        )
        self._usages.append(usage)
        return reply, usage

    def drain(self) -> tuple[int, int, Decimal]:
        """Input tokens, output tokens and rand since the last drain."""
        usages, self._usages = self._usages, []
        return (
            sum(u.input_tokens for u in usages),
            sum(u.output_tokens for u in usages),
            sum((u.cost_zar for u in usages), Decimal(0)),
        )


class Recording:
    """Record every call into a replay file (mothusi_llm.record), then return it unchanged."""

    def __init__(self, inner: Provider, path: Path) -> None:
        self.name = inner.name
        self._inner = inner
        self._path = path

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        reply, usage = await self._inner.complete(
            system, user, output, budget_tokens=budget_tokens, images=images
        )
        record(
            self._path,
            system,
            user,
            output.__name__,
            usage.model,
            reply.model_dump(mode="json"),
            usage.input_tokens,
            usage.output_tokens,
            images,
        )
        return reply, usage


ROOT = Path(__file__).resolve().parents[4]
PRICING = ROOT / "packages/llm/pricing.toml"


def priced() -> bool:
    """Whether a sourced price list exists (docs/resources-to-fetch.md row 4 until it does)."""
    return PRICING.exists()


def free(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """A replayed call spends nothing now, whatever the recorded model was (scripted or live)."""
    return Decimal(0)


def pricer() -> Pricer:
    """The sourced price list when it exists; otherwise R0 a call, and the run is UNPRICED.

    Prices are a fact not yet in hand: without pricing.toml no rate is guessed. The Planner is told
    (`Planner.priced = False`), logs that cost is not metered and skips the budget check, and the
    usage events and bundle say `priced: false` instead of presenting the calls as free.
    """
    if priced():
        pricing = load_pricing(PRICING)
        return lambda model, i, o: price(pricing, model, i, o)
    return lambda model, i, o: Decimal(0)


type Role = Literal["reader", "text", "moderator"]
"""Which model an agent is briefed on (llm-seam.md §11.2)."""

AGENT_ROLES: dict[str, Role] = {"read": "reader", "moderate": "moderator"}
"""Agents not listed here use the text role."""


@dataclass(frozen=True)
class ProviderSet:
    """One provider per role, plus the shared fallback. Built by `from_environment()`."""

    reader: Provider
    text: Provider
    moderator: Provider
    fallbacks: Mapping[Role, Provider]
    """Per role, so a reader call (with images) falls back to a vision model, never a text one."""
    priced: bool

    @property
    def fallback(self) -> Provider | None:
        """The text role's fallback (most agents)."""
        return self.fallbacks.get("text")

    def for_role(self, role: Role) -> Provider:
        return {"reader": self.reader, "text": self.text, "moderator": self.moderator}[role]

    def for_agent(self, agent: str) -> Provider:
        """`read` -> reader, `moderate` -> moderator, every other agent -> text."""
        return self.for_role(AGENT_ROLES.get(agent, "text"))


def from_environment() -> ProviderSet:
    """The providers per role from MOTHUSI_PROVIDER: anthropic (default with a key), ollama, replay.

    Ollama (ADR 0001: the Cape Town G4dn, or the laptop) takes a model per role from
    MOTHUSI_OLLAMA_MODEL (text, qwen3:14b), MOTHUSI_OLLAMA_MODERATOR_MODEL (phi4, a different family
    for the blind re-mark) and MOTHUSI_OLLAMA_READER_MODEL (qwen3-vl:8b-instruct);
    OLLAMA_FALLBACK_URL, when set, is a second Ollama (the laptop) asked when the first fails.
    Ollama and replay cost nothing per call, so they are priced; Anthropic is priced only when the
    sourced price list exists.
    """
    kind = os.environ.get("MOTHUSI_PROVIDER") or (
        "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "replay"
    )
    cost = pricer()
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    text_model = os.environ.get("MOTHUSI_OLLAMA_MODEL", "qwen3:14b")
    moderator_model = os.environ.get("MOTHUSI_OLLAMA_MODERATOR_MODEL", "phi4")
    reader_model = os.environ.get("MOTHUSI_OLLAMA_READER_MODEL", "qwen3-vl:8b-instruct")
    if kind == "replay":
        path = os.environ.get("MOTHUSI_REPLAY") or str(
            ROOT / "services/core/fixtures/gr10-maths-t1/replay-6.jsonl"
        )
        replay = ReplayProvider(Path(path), free)
        return ProviderSet(replay, replay, replay, {}, True)

    def ollama_set(url: str) -> tuple[Provider, Provider, Provider]:
        return (
            OllamaProvider(reader_model, url),
            OllamaProvider(text_model, url),
            OllamaProvider(moderator_model, url),
        )

    def per_role(url: str) -> dict[Role, Provider]:
        reader, text, moderator = ollama_set(url)
        return {"reader": reader, "text": text, "moderator": moderator}

    if kind == "ollama":
        reader, text, moderator = ollama_set(ollama_url)
        fallback_url = os.environ.get("OLLAMA_FALLBACK_URL")
        return ProviderSet(
            reader, text, moderator, per_role(fallback_url) if fallback_url else {}, True
        )
    if kind == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("MOTHUSI_PROVIDER=anthropic needs ANTHROPIC_API_KEY in .env")
        model = os.environ.get("MOTHUSI_ANTHROPIC_MODEL", "claude-sonnet-5-5")
        moderator = os.environ.get("MOTHUSI_ANTHROPIC_MODERATOR_MODEL", model)
        claude = AnthropicProvider(model, key, price=cost)
        return ProviderSet(
            claude,
            claude,
            AnthropicProvider(moderator, key, price=cost),
            per_role(ollama_url),
            priced(),
        )
    raise ValueError(f"MOTHUSI_PROVIDER must be anthropic, ollama or replay, not {kind!r}")
