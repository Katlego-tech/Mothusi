"""Recorded calls are keyed by the full prompt and still validate the requested contract."""

import asyncio
import hashlib
import json
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from mothusi_contracts import CheckResult, Mark
from mothusi_llm import Pricer, Provider, ProviderError, ReplayProvider, load_pricing, price, record

PRICER: Pricer = partial(price, load_pricing(Path(__file__).parent / "fixtures/pricing.toml"))


def mark() -> Mark:
    return Mark(
        question_id="q",
        awarded=2,
        max_marks=2,
        evidence_quote="x=3",
        memo_line="x=3",
        principle="correct",
    )


def test_record_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    record(
        path, "system", "user", "Mark", "test-model", mark().model_dump(mode="json"), 1_000_000, 0
    )
    data = json.loads(path.read_text())
    assert data["key"] == hashlib.sha256(b"system\n--\nuser\n--\nMark").hexdigest()
    provider: Provider = ReplayProvider(path, PRICER)
    result, usage = asyncio.run(provider.complete("system", "user", Mark, budget_tokens=5))
    assert provider.name == "replay" and result == mark()
    assert usage.input_tokens == 1_000_000 and usage.output_tokens == 0
    assert usage.model == "test-model" and usage.cost_zar == Decimal("25.0000")


def test_missing_key_nonretryable(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text("")
    key = hashlib.sha256(b"s\n--\nu\n--\nMark").hexdigest()
    with pytest.raises(ProviderError, match=key[:12]) as error:
        asyncio.run(ReplayProvider(path, PRICER).complete("s", "u", Mark))
    assert not error.value.retryable


def test_tampered_output_is_retryable(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    record(path, "s", "u", "Mark", "test-model", {"question_id": "q"}, 1, 1)
    with pytest.raises(ProviderError) as error:
        asyncio.run(ReplayProvider(path, PRICER).complete("s", "u", Mark))
    assert error.value.retryable


def test_prompt_and_output_type_are_part_of_key(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    record(path, "s", "u", "Mark", "test-model", mark().model_dump(mode="json"), 1, 1)
    provider = ReplayProvider(path, PRICER)
    for system, user in [("other", "u"), ("s", "other")]:
        with pytest.raises(ProviderError) as error:
            asyncio.run(provider.complete(system, user, Mark))
        assert not error.value.retryable
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete("s", "u", CheckResult))
    assert not error.value.retryable


def test_append_multiple_records(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    for user in ["first", "second"]:
        record(path, "s", user, "Mark", "test-model", mark().model_dump(mode="json"), 2, 3)
    assert len(path.read_text().splitlines()) == 2
    provider = ReplayProvider(path, PRICER)
    assert asyncio.run(provider.complete("s", "first", Mark))[0] == mark()
    assert asyncio.run(provider.complete("s", "second", Mark))[0] == mark()


def test_images_are_part_of_the_key_and_absent_images_keep_the_old_key(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    page = b"\x89PNG\r\n\x1a\nA"
    record(
        path, "s", "u", "Mark", "test-model", mark().model_dump(mode="json"), 1, 1, images=(page,)
    )
    key = json.loads(path.read_text())["key"]
    expected = hashlib.sha256(
        b"s\n--\nu\n--\nMark\n--\n" + hashlib.sha256(page).hexdigest().encode()
    ).hexdigest()
    assert key == expected
    provider: Provider = ReplayProvider(path, PRICER)
    result, _ = asyncio.run(provider.complete("s", "u", Mark, images=(page,)))
    assert result == mark()
    with pytest.raises(ProviderError):  # another image, or none, is another call
        asyncio.run(provider.complete("s", "u", Mark, images=(page + b"B",)))
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete("s", "u", Mark))
