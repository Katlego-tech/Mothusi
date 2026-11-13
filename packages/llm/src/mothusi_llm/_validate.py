"""Turn a model's text reply into a contract model, or a retryable ProviderError."""

from __future__ import annotations

from pydantic import ValidationError

from mothusi_contracts import Contract
from mothusi_llm.protocol import ProviderError


def parse_reply[T: Contract](text: str, output: type[T], source: str) -> T:
    """Validate `text` as JSON for `output`. A reply that does not fit is worth one more try."""
    try:
        return output.model_validate_json(text)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
        raise ProviderError(
            f"{source}: reply is not valid JSON for {output.__name__}: {errors}", retryable=True
        ) from exc
