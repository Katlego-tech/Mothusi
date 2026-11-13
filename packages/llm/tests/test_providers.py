"""The Anthropic and Ollama adapters: structured output in, a contract model and Usage out.

Design: docs/design/llm-seam.md §4, §6. HTTP is mocked with each SDK's own transport, so these run
offline. The `live` tests call the real services and are skipped without credentials.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from mothusi_contracts import Mark
from mothusi_llm import AnthropicProvider, OllamaProvider, Pricer, Provider, ProviderError

MARK = {
    "question_id": "1.1",
    "awarded": 3,
    "max_marks": 4,
    "evidence_quote": "(x - 3)(x + 2) = 0 so x = 3",
    "memo_line": "1 mark factorising, 1 mark each root",
    "principle": "method and accuracy marks are separate",
}
SYSTEM = "You mark Grade 10 Mathematics scripts against the memo."
USER = "Memo item 1.1 ... Learner's answer: (x - 3)(x + 2) = 0 so x = 3"


def fixed_price(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """A stand-in for the meter: R1 per 1000 input tokens, R5 per 1000 output tokens."""
    assert model
    return (Decimal(input_tokens) + 5 * Decimal(output_tokens)) / 1000


# --------------------------------------------------------------------------- anthropic helpers


def message(
    text: str | None,
    *,
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 1200, "output_tokens": 300},
    }
    if stop_details is not None:
        body["stop_details"] = stop_details
    return body


def anthropic_with(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    price: Pricer = fixed_price,
) -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicProvider("claude-opus-5-5", "test-key", price=price, client=client)


def respond(body: dict[str, Any], seen: list[dict[str, Any]] | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(200, json=body)

    return handler


def status(code: int) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            code, json={"type": "error", "error": {"type": "test_error", "message": f"HTTP {code}"}}
        )

    return handler


def complete_mark(provider: Provider, **kwargs: Any) -> tuple[Mark, Any]:
    return asyncio.run(provider.complete(SYSTEM, USER, Mark, **kwargs))


# --------------------------------------------------------------------------- anthropic


def test_anthropic_validates_into_the_contract_and_prices_usage() -> None:
    seen: list[dict[str, Any]] = []
    provider = anthropic_with(respond(message(json.dumps(MARK)), seen))

    mark, usage = complete_mark(provider)

    assert mark == Mark.model_validate(MARK)
    assert usage.model == "claude-opus-5-5"
    assert (usage.input_tokens, usage.output_tokens) == (1200, 300)
    assert usage.cost_zar == Decimal("2.7")  # 1.2 + 5 x 0.3
    request = seen[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 16000
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert set(request["output_config"]["format"]["schema"]["required"]) >= {"awarded", "memo_line"}
    assert SYSTEM in request["system"] and "JSON" in request["system"]
    assert request["messages"] == [{"role": "user", "content": USER}]


def test_anthropic_budget_sets_max_tokens() -> None:
    seen: list[dict[str, Any]] = []
    provider = anthropic_with(respond(message(json.dumps(MARK)), seen))
    complete_mark(provider, budget_tokens=2000)
    assert seen[0]["max_tokens"] == 2000


def test_anthropic_prices_cache_tokens_as_input() -> None:
    usage_block = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 400,
        "cache_read_input_tokens": 500,
    }
    provider = anthropic_with(respond(message(json.dumps(MARK), usage=usage_block)))
    _, usage = complete_mark(provider)
    assert (usage.input_tokens, usage.output_tokens) == (1000, 50)


@pytest.mark.parametrize(
    ("text", "stop_reason"),
    [
        ("Here is the mark: {", "end_turn"),  # not JSON
        (json.dumps({**MARK, "evidence_quote": ""}), "end_turn"),  # breaks Mark's validator
        (json.dumps({**MARK, "confidence": 0.9}), "end_turn"),  # a field the contract lacks
        ('{"question_id": "1.1", "awar', "max_tokens"),  # cut off
        (None, "end_turn"),  # no text block at all
    ],
)
def test_anthropic_output_that_does_not_fit_is_retryable(
    text: str | None, stop_reason: str
) -> None:
    provider = anthropic_with(respond(message(text, stop_reason=stop_reason)))
    with pytest.raises(ProviderError) as err:
        complete_mark(provider)
    assert err.value.retryable
    assert "Mark" in str(err.value)


def test_anthropic_refusal_is_not_retryable() -> None:
    body = message(
        None,
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
    )
    with pytest.raises(ProviderError) as err:
        complete_mark(anthropic_with(respond(body)))
    assert not err.value.retryable
    assert "refus" in str(err.value) and "cyber" in str(err.value)


@pytest.mark.parametrize(
    ("code", "retryable"),
    [(429, True), (500, True), (529, True), (400, False), (401, False), (404, False)],
)
def test_anthropic_status_codes_map_to_retryability(code: int, retryable: bool) -> None:
    with pytest.raises(ProviderError) as err:
        complete_mark(anthropic_with(status(code)))
    assert err.value.retryable is retryable
    assert str(code) in str(err.value)


def test_anthropic_timeout_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as err:
        complete_mark(anthropic_with(handler))
    assert err.value.retryable


def test_anthropic_default_client_leaves_retries_to_the_planner() -> None:
    provider = AnthropicProvider("claude-opus-5-5", "test-key", 30, price=fixed_price)
    assert provider.client.max_retries == 0
    assert provider.client.timeout == 30


# --------------------------------------------------------------------------- ollama


def ollama_with(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider("llama3.2", "http://ollama.test", client=client)


def ollama_reply(content: str) -> dict[str, Any]:
    return {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 800,
        "eval_count": 200,
    }


def test_ollama_validates_into_the_contract_at_no_cost() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ollama_reply(json.dumps(MARK)))

    mark, usage = complete_mark(ollama_with(handler), budget_tokens=900)

    assert mark == Mark.model_validate(MARK)
    assert (usage.model, usage.input_tokens, usage.output_tokens) == ("llama3.2", 800, 200)
    assert usage.cost_zar == 0
    assert str(seen[0].url) == "http://ollama.test/api/chat"
    body = json.loads(seen[0].content)
    assert body["stream"] is False
    assert body["format"] == Mark.model_json_schema()
    assert body["options"] == {"num_predict": 900}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_ollama_without_budget_sends_no_options() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ollama_reply(json.dumps(MARK)))

    complete_mark(ollama_with(handler))
    assert "options" not in json.loads(seen[0].content)


def test_ollama_turns_thinking_off() -> None:
    # qwen3 thinks by default; on the T4 that put every Marker call past the 60 s timeout
    # (first eval, 2026-11-11). llm-seam.md: think = false for every role.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ollama_reply(json.dumps(MARK)))

    complete_mark(ollama_with(handler), budget_tokens=900)
    assert json.loads(seen[0].content)["think"] is False


def test_ollama_output_that_does_not_fit_is_retryable() -> None:
    provider = ollama_with(lambda r: httpx.Response(200, json=ollama_reply("{not json")))
    with pytest.raises(ProviderError) as err:
        complete_mark(provider)
    assert err.value.retryable and "Mark" in str(err.value)


@pytest.mark.parametrize(
    "reply",
    [
        b"<html>gateway</html>",  # not JSON at all
        b'{"model": "llama3.2", "message": null, "done": true}',  # no message
        b'{"model": "llama3.2", "done": true}',  # message missing
        b'{"message": {"role": "assistant"}, "done": true}',  # message without content
    ],
)
def test_ollama_malformed_envelope_is_retryable(reply: bytes) -> None:
    provider = ollama_with(lambda r: httpx.Response(200, content=reply))
    with pytest.raises(ProviderError) as err:
        complete_mark(provider)
    assert err.value.retryable and "/api/chat" in str(err.value)


@pytest.mark.parametrize(("code", "retryable"), [(404, False), (400, False), (500, True)])
def test_ollama_status_codes_map_to_retryability(code: int, retryable: bool) -> None:
    provider = ollama_with(lambda r: httpx.Response(code, json={"error": "model not found"}))
    with pytest.raises(ProviderError) as err:
        complete_mark(provider)
    assert err.value.retryable is retryable
    assert str(code) in str(err.value)


def test_ollama_timeout_is_retryable_and_refused_connection_is_not() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderError) as timed_out:
        complete_mark(ollama_with(slow))
    assert timed_out.value.retryable
    with pytest.raises(ProviderError) as refused:
        complete_mark(ollama_with(down))
    assert not refused.value.retryable
    assert "http://ollama.test" in str(refused.value)


# --------------------------------------------------------------------------- live (opt-in)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_anthropic_returns_a_valid_mark() -> None:
    model = os.environ.get("MOTHUSI_LIVE_MODEL", "claude-opus-5-5")
    provider = AnthropicProvider(model, os.environ["ANTHROPIC_API_KEY"], 120, price=fixed_price)
    mark, usage = complete_mark(provider)
    assert mark.question_id == "1.1" and usage.output_tokens > 0


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("OLLAMA_URL"), reason="needs OLLAMA_URL and a running Ollama"
)
def test_live_ollama_returns_a_valid_mark() -> None:
    model = os.environ.get("MOTHUSI_OLLAMA_MODEL", "llama3.2")
    provider = OllamaProvider(model, os.environ["OLLAMA_URL"], 300)
    mark, _ = complete_mark(provider)
    assert mark.question_id == "1.1"


# --------------------------------------------------------------------------- images (§11.1)

PNG = b"\x89PNG\r\n\x1a\n" + b"page-crop"
JPEG = b"\xff\xd8\xff" + b"page-crop"


def test_ollama_sends_images_base64_on_the_user_turn() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ollama_reply(json.dumps(MARK)))

    complete_mark(ollama_with(handler), images=(PNG, JPEG))
    user_turn = json.loads(seen[0].content)["messages"][-1]
    assert user_turn["content"] == USER
    assert [base64.b64decode(i) for i in user_turn["images"]] == [PNG, JPEG]


def test_ollama_without_images_sends_no_images_field() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ollama_reply(json.dumps(MARK)))

    complete_mark(ollama_with(handler))
    assert "images" not in json.loads(seen[0].content)["messages"][-1]


def test_anthropic_sends_image_blocks_before_the_text() -> None:
    seen: list[dict[str, Any]] = []
    complete_mark(anthropic_with(respond(message(json.dumps(MARK)), seen)), images=(PNG, JPEG))
    content = seen[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["image", "image", "text"]
    assert [block["source"]["media_type"] for block in content[:2]] == ["image/png", "image/jpeg"]
    assert base64.b64decode(content[0]["source"]["data"]) == PNG
    assert content[2] == {"type": "text", "text": USER}


@pytest.mark.parametrize("make", [ollama_with, anthropic_with])
def test_an_image_that_is_not_png_or_jpeg_is_refused_before_any_call(
    make: Callable[[Any], Provider],
) -> None:
    calls: list[object] = []

    def handler(request: Any) -> Any:
        calls.append(request)
        raise AssertionError("no request may be sent")

    with pytest.raises(ProviderError) as caught:
        complete_mark(make(handler), images=(b"GIF89a...",))
    assert not caught.value.retryable
    assert "PNG or JPEG" in str(caught.value)
    assert calls == []


def test_ollama_default_timeout_covers_a_queued_call() -> None:
    # llm-seam.md: the clock includes time queued on the host (four decode together on the T4,
    # up to ~120 s each, measured 2026-11-12); 60 s timed out every Marker call in the evals.
    provider = OllamaProvider("qwen3:14b")
    assert provider._client.timeout.read == 180  # pyright: ignore[reportPrivateUsage]
