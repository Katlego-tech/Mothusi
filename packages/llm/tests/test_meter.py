"""Meter calculations use explicitly synthetic sourced fixture prices."""

from decimal import Decimal
from pathlib import Path

import pytest

from mothusi_llm import Meter, Usage, load_pricing, price, sum_usage

PRICES = Path(__file__).parent / "fixtures/pricing.toml"


def test_prices_both_token_directions() -> None:
    pricing = load_pricing(PRICES)
    assert price(pricing, "test-model", 1_000_000, 0) == Decimal("25.0000")
    assert price(pricing, "test-model", 0, 1_000_000) == Decimal("100.0000")
    assert price(pricing, "test-model", 1_000_000, 1_000_000) == Decimal("125.0000")
    assert price(pricing, "half-up-test", 1, 0) == Decimal("0.0001")
    assert price(pricing, "test-model", 0, 0).as_tuple().exponent == -4


def test_unknown_model_names_known_models() -> None:
    with pytest.raises(ValueError) as error:
        price(load_pricing(PRICES), "unknown", 1, 1)
    assert "test-model" in str(error.value) and "half-up-test" in str(error.value)


def test_meter_total_and_per_script() -> None:
    usages = (
        Usage(model="test-model", input_tokens=5, output_tokens=7, cost_zar=Decimal("1.005")),
        Usage(model="other", input_tokens=3, output_tokens=2, cost_zar=Decimal("1.005")),
    )
    meter = Meter()
    assert meter.total() == (0, 0, Decimal(0))
    for usage in usages:
        meter.add(usage)
    assert meter.total() == sum_usage(usages) == (8, 9, Decimal("2.010"))
    assert meter.per_script(2) == Decimal("1.01")
    assert meter.per_script(2).as_tuple().exponent == -2


@pytest.mark.parametrize("n", [0, -1])
def test_invalid_class_size(n: int) -> None:
    with pytest.raises(ValueError):
        Meter().per_script(n)


@pytest.mark.parametrize(("inputs", "outputs"), [(-1, 0), (0, -1)])
def test_negative_tokens(inputs: int, outputs: int) -> None:
    with pytest.raises(ValueError):
        price(load_pricing(PRICES), "test-model", inputs, outputs)


def test_missing_pricing_source(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        '[rates]\nzar_per_usd=20\n[[model]]\nname="m"\n'
        'usd_per_million_input=1\nusd_per_million_output=2\nsource="test"'
    )
    with pytest.raises(ValueError):
        load_pricing(path)


def test_legacy_pricing_has_optional_hosting_defaults() -> None:
    pricing = load_pricing(PRICES)
    assert pricing.instance == ()
    assert pricing.stack is None


def test_load_hosting_prices_and_lookup(tmp_path: Path) -> None:
    from mothusi_llm.meter import InstancePrice, StackPrice, instance_price

    path = tmp_path / "hosting.toml"
    path.write_text(
        PRICES.read_text()
        + '\n[[instance]]\nname="g4dn.xlarge"\nusd_per_hour=0.698\nsource="test GPU"\n'
        + '[[instance]]\nname="laptop"\nusd_per_hour="0"\nsource="test laptop"\n'
        + '[stack]\nusd_per_hour="0.289"\nsource="test stack"\n'
    )
    pricing = load_pricing(path)
    assert instance_price(pricing, "g4dn.xlarge") == InstancePrice(
        name="g4dn.xlarge", usd_per_hour=Decimal("0.698"), source="test GPU"
    )
    assert instance_price(pricing, "laptop").usd_per_hour == 0
    assert pricing.stack == StackPrice(usd_per_hour=Decimal("0.289"), source="test stack")
    with pytest.raises(ValueError, match="unknown") as error:
        instance_price(pricing, "unknown")
    assert "g4dn.xlarge" in str(error.value)
    assert "laptop" in str(error.value)
    with pytest.raises(ValueError, match="known"):
        instance_price(load_pricing(PRICES), "missing")


def test_negative_hosting_rates_rejected() -> None:
    from mothusi_llm.meter import InstancePrice, StackPrice

    with pytest.raises(ValueError):
        InstancePrice(name="gpu", usd_per_hour=Decimal(-1), source="test")
    with pytest.raises(ValueError):
        StackPrice(usd_per_hour=Decimal(-1), source="test")
