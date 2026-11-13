"""Replay full-prompt-keyed model recordings through the same validated provider seam."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import Field, JsonValue, ValidationError

from mothusi_contracts import Contract
from mothusi_llm.protocol import Pricer, ProviderError, Usage


class _Record(Contract):
    key: str = Field(min_length=64, max_length=64)
    model: str = Field(min_length=1)
    output: dict[str, JsonValue]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


def _key(system: str, user: str, output_model_name: str, images: Sequence[bytes] = ()) -> str:
    text = f"{system}\n--\n{user}\n--\n{output_model_name}"
    if images:  # without images the key is unchanged, so existing recordings still match
        text += "\n--\n" + ",".join(hashlib.sha256(image).hexdigest() for image in images)
    return hashlib.sha256(text.encode()).hexdigest()


def record(
    path: Path,
    system: str,
    user: str,
    output_model_name: str,
    model: str,
    obj: Mapping[str, JsonValue],
    input_tokens: int,
    output_tokens: int,
    images: Sequence[bytes] = (),
) -> None:
    """Append one response fixture, with a key derived from prompts, output type and images."""
    entry = _Record(
        key=_key(system, user, output_model_name, images),
        model=model,
        output=dict(obj),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    with path.open("a", encoding="utf-8", newline="\n") as destination:
        destination.write(entry.model_dump_json() + "\n")


class ReplayProvider:
    """Validate recorded outputs and price their original usage with the injected Pricer.

    Like the Anthropic and Ollama adapters it takes a `Pricer` (llm-seam.md §8): the meter supplies
    one from the sourced pricing file, and nothing here reads a file at call time.
    """

    name = "replay"

    def __init__(self, path: Path, price: Pricer) -> None:
        self._price = price
        self._records: dict[str, _Record] = {}
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    entry = _Record.model_validate_json(line)
                except ValidationError as error:
                    raise ProviderError(
                        f"invalid replay record on line {number}: {error}", retryable=True
                    ) from error
                self._records[entry.key] = entry

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        key = _key(system, user, output.__name__, images)
        entry = self._records.get(key)
        if entry is None:
            raise ProviderError(f"no replay record for key {key[:12]}", retryable=False)
        try:
            result = output.model_validate_json(json.dumps(entry.output))
        except ValidationError as error:
            raise ProviderError(
                f"replay response is not valid {output.__name__}: {error}", retryable=True
            ) from error
        usage = Usage(
            model=entry.model,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cost_zar=self._price(entry.model, entry.input_tokens, entry.output_tokens),
        )
        return result, usage
