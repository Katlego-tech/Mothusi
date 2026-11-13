"""Providers per role (llm-seam.md §11.2): which model each agent is briefed on."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Contract, Mark
from mothusi_core import providers
from mothusi_core.graph.planner import Planner
from mothusi_llm import OllamaProvider, ProviderError, Usage

MARK = Mark(
    question_id="1.1",
    awarded=1,
    max_marks=2,
    evidence_quote="x = 3",
    memo_line="x = 3",
    principle="correct",
)


class Named:
    """A provider that records it was asked and answers with one fixed Mark."""

    def __init__(self, name: str, cost: str = "0") -> None:
        self.name = name
        self.calls: int = 0
        self.cost = Decimal(cost)

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.calls += 1
        reply = output.model_validate(MARK.model_dump())
        return reply, Usage(model=self.name, input_tokens=10, output_tokens=2, cost_zar=self.cost)


def test_ollama_gives_each_role_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://g4dn.internal:11434")
    for name in (
        "MOTHUSI_OLLAMA_MODEL",
        "MOTHUSI_OLLAMA_MODERATOR_MODEL",
        "MOTHUSI_OLLAMA_READER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OLLAMA_FALLBACK_URL", raising=False)
    found = providers.from_environment()
    models = {
        role: (p.model, p.base_url)
        for role, p in (
            ("reader", found.reader),
            ("text", found.text),
            ("moderator", found.moderator),
        )
        if isinstance(p, OllamaProvider)
    }
    assert models == {
        "reader": ("qwen3-vl:8b-instruct", "http://g4dn.internal:11434"),
        "text": ("qwen3:14b", "http://g4dn.internal:11434"),
        "moderator": ("phi4", "http://g4dn.internal:11434"),
    }
    assert found.fallback is None and found.priced


def test_ollama_fallback_url_is_a_second_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_FALLBACK_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("MOTHUSI_OLLAMA_MODEL", "qwen3:8b")
    found = providers.from_environment()
    fallbacks = {
        role: (p.model, p.base_url)
        for role, p in found.fallbacks.items()
        if isinstance(p, OllamaProvider)
    }
    assert fallbacks == {
        "reader": (
            "qwen3-vl:8b-instruct",
            "http://127.0.0.1:11434",
        ),  # images fall back to a vision model
        "text": ("qwen3:8b", "http://127.0.0.1:11434"),
        "moderator": ("phi4", "http://127.0.0.1:11434"),
    }


@pytest.mark.parametrize(
    ("agent", "role"),
    [("read", "reader"), ("moderate", "moderator"), ("mark", "text"), ("write", "text")],
)
def test_for_agent_maps_agents_to_roles(agent: str, role: str) -> None:
    reader, text, moderator = Named("reader"), Named("text"), Named("moderator")
    found = providers.ProviderSet(reader, text, moderator, {}, True)
    assert found.for_agent(agent).name == role


def test_the_planner_briefs_each_role_on_its_provider_and_drains_them_together(
    tmp_path: Path,
) -> None:
    text, moderator = Named("text", "1.50"), Named("moderator", "0.25")
    planner = Planner(
        primary=text,
        fallback=None,
        roles={"moderator": moderator},
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )

    async def ask() -> None:
        await planner.provider("a1").complete("s", "u", Mark)
        await planner.provider("a1", "moderator").complete("s", "u", Mark)
        await planner.provider("a1", "reader").complete("s", "u", Mark)  # no reader: primary

    asyncio.run(ask())
    assert (text.calls, moderator.calls) == (2, 1)
    assert planner.drain("a1") == (30, 6, Decimal("3.25"))
    assert planner.drain("a1") == (0, 0, Decimal(0))


class Failing(Named):
    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.calls += 1
        raise ProviderError("down", retryable=True)


def test_a_failed_reader_call_falls_back_to_the_reader_fallback_with_its_images(
    tmp_path: Path,
) -> None:
    reader, text_fallback, reader_fallback = Failing("g4dn-reader"), Named("text"), Named("vl")
    planner = Planner(
        primary=Named("text-primary"),
        fallback=text_fallback,
        roles={"reader": reader},
        fallback_roles={"reader": reader_fallback},
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    asyncio.run(planner.provider("a1", "reader").complete("s", "u", Mark, images=(b"png",)))
    assert (reader.calls, reader_fallback.calls, text_fallback.calls) == (1, 1, 0)
