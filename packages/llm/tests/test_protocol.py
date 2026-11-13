"""The usage record adds up, and a provider error says whether to retry."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from mothusi_llm import ProviderError, Usage, sum_usage


def test_usage_sums_across_models() -> None:
    a = Usage(
        model="claude-sonnet-5-5", input_tokens=1200, output_tokens=300, cost_zar=Decimal("0.12")
    )
    b = Usage(model="ollama/llama", input_tokens=800, output_tokens=200, cost_zar=Decimal(0))
    assert a.total_tokens == 1500
    assert sum_usage([a, b]) == (2000, 500, Decimal("0.12"))
    assert sum_usage([]) == (0, 0, Decimal(0))


def test_usage_rejects_negative_cost() -> None:
    with pytest.raises(ValidationError):
        Usage(model="m", input_tokens=1, output_tokens=1, cost_zar=Decimal("-1"))


def test_provider_error_carries_retryability() -> None:
    err = ProviderError("timed out", retryable=True)
    assert err.retryable and str(err) == "timed out"
    assert not ProviderError("not valid JSON for Mark", retryable=False).retryable
