"""A local model through Ollama's chat API, as the offline fallback. Design: llm-seam.md §6."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from mothusi_contracts import Contract
from mothusi_llm._images import media_type
from mothusi_llm._validate import parse_reply
from mothusi_llm.protocol import ProviderError, Usage


class OllamaProvider:
    """A `Provider` backed by Ollama. Costs nothing per call; usage still counts tokens."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 180,  # includes time queued on the host (llm-seam.md)
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        user_turn: dict[str, Any] = {"role": "user", "content": user}
        if images:
            for image in images:
                media_type(image, "ollama")  # PNG or JPEG only, refused before any call
            user_turn["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            # qwen3 thinks by default; at ~21 tok/s on the T4 that put every Marker call past
            # the timeout (llm-seam.md: off for every role).
            "think": False,
            "format": output.model_json_schema(),
            "messages": [
                {"role": "system", "content": system},
                user_turn,
            ],
        }
        if budget_tokens is not None:
            body["options"] = {"num_predict": budget_tokens}

        try:
            response = await self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"ollama: not reachable at {self.base_url} ({exc})", retryable=False
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"ollama: timed out ({type(exc).__name__})", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama: {type(exc).__name__}: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            retryable = response.status_code >= 500
            raise ProviderError(
                f"ollama: HTTP {response.status_code}: {response.text[:200]}", retryable=retryable
            )

        try:
            envelope = _ChatReply.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError(
                f"ollama: the /api/chat reply is not a chat message: {exc.error_count()} error(s), "
                f"first: {exc.errors()[0]['msg']}",
                retryable=True,
            ) from exc
        result = parse_reply(envelope.message.content, output, "ollama")
        usage = Usage(
            model=self.model,
            input_tokens=envelope.prompt_eval_count,
            output_tokens=envelope.eval_count,
            cost_zar=Decimal(0),
        )
        return result, usage


class _ChatMessage(BaseModel):
    content: str


class _ChatReply(BaseModel):
    """The parts of Ollama's non-streaming /api/chat reply this adapter reads."""

    message: _ChatMessage
    prompt_eval_count: int = Field(default=0, ge=0)
    eval_count: int = Field(default=0, ge=0)
