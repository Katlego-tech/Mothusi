"""Claude through the Messages API, with structured output. Design:
docs/architecture/design/llm-seam.md §6."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic, transform_schema

from mothusi_contracts import Contract
from mothusi_llm._images import media_type
from mothusi_llm._validate import parse_reply
from mothusi_llm.protocol import Pricer, ProviderError, Usage

DEFAULT_MAX_TOKENS = 16000
JSON_ONLY = "Reply with one JSON object that matches the given schema, and nothing else."


class AnthropicProvider:
    """A `Provider` backed by Claude. Retries on bad output are the Planner's, not this class's."""

    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_s: float = 30,
        *,
        price: Pricer,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self._price = price
        # No SDK retries: a Marker call must answer within timeout_s or fall back (the latency
        # budget), and retrying is the Planner's decision, not this adapter's.
        self.client = client or AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        content: str | list[dict[str, Any]] = user
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type(image, "anthropic"),
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                }
                for image in images
            ]
            content.append({"type": "text", "text": user})
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=budget_tokens or DEFAULT_MAX_TOKENS,
                system=f"{system}\n\n{JSON_ONLY}",
                messages=[{"role": "user", "content": content}],  # pyright: ignore[reportArgumentType]
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": transform_schema(output.model_json_schema()),
                    }
                },
            )
        except anthropic.APIStatusError as exc:
            code = exc.status_code
            retryable = code == 429 or code >= 500
            raise ProviderError(
                f"anthropic: HTTP {code}: {exc.message}", retryable=retryable
            ) from exc
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError(f"anthropic: {type(exc).__name__}: {exc}", retryable=True) from exc

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = details.category if details is not None else None
            raise ProviderError(
                f"anthropic: the model refused (category: {category or 'unspecified'})",
                retryable=False,
            )

        texts = [block.text for block in response.content if block.type == "text"]
        if not texts:
            raise ProviderError(
                f"anthropic: no text in the reply for {output.__name__} "
                f"(stop_reason {response.stop_reason})",
                retryable=True,
            )
        source = "anthropic"
        if response.stop_reason == "max_tokens":
            source = "anthropic (cut off at max_tokens)"
        result = parse_reply("".join(texts), output, source)

        reported = response.usage
        input_tokens = (
            reported.input_tokens
            + (reported.cache_creation_input_tokens or 0)
            + (reported.cache_read_input_tokens or 0)
        )
        usage = Usage(
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=reported.output_tokens,
            cost_zar=self._price(self.model, input_tokens, reported.output_tokens),
        )
        return result, usage
