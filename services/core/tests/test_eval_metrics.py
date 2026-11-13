"""Worked eval figures and boundary cases from eval.md §§3 and 6."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mothusi_core.eval.metrics import (
    agreement,
    cer,
    percentile,
    reader_accuracy,
    run_cost,
    summarise_latency,
)
from mothusi_core.eval.results import (
    CallTiming,
    CostBasis,
    EvalResult,
    LatencySummary,
    ReaderAccuracy,
    TrapCatch,
)


def basis() -> CostBasis:
    return CostBasis(
        instance="g4dn.xlarge",
        instance_usd_per_hour=Decimal("0.698"),
        stack_usd_per_hour=Decimal("0.289"),
        zar_per_usd=Decimal("16.3515"),
        sources=("synthetic test prices from eval.md §9",),
    )


@pytest.mark.parametrize(
    ("compared", "different", "expected"),
    [(0, 0, None), (24, 0, "1.0000"), (24, 12, "0.5000"), (24, 24, "0.0000"), (32, 1, "0.9688")],
)
def test_agreement(compared: int, different: int, expected: str | None) -> None:
    result = agreement(compared, different)
    assert result == (Decimal(expected) if expected is not None else None)
    if result is not None:
        assert result.as_tuple().exponent == -4


@pytest.mark.parametrize(("compared", "different"), [(-1, 0), (2, -1), (0, 1), (2, 3)])
def test_agreement_rejects_impossible_counts(compared: int, different: int) -> None:
    with pytest.raises(ValueError):
        agreement(compared, different)


@pytest.mark.parametrize(
    ("q", "expected"), [(0, "1.01"), (0.5, "2.00"), (0.95, "20.00"), (1, "20.00")]
)
def test_nearest_rank(q: float, expected: str) -> None:
    values = [20.0, 3.0, 1.005, 2.0]
    assert percentile(values, q) == Decimal(expected)
    assert values == [20.0, 3.0, 1.005, 2.0]


@pytest.mark.parametrize("values", [[], [-1.0], [float("nan")], [float("inf")]])
def test_percentile_rejects_invalid_samples(values: list[float]) -> None:
    with pytest.raises(ValueError):
        percentile(values, 0.5)


@pytest.mark.parametrize("q", [-0.1, 1.1, float("nan")])
def test_percentile_rejects_invalid_quantile(q: float) -> None:
    with pytest.raises(ValueError):
        percentile([1.0], q)


def call(role: str, model: str, seconds: float) -> CallTiming:
    return CallTiming(role=role, model=model, seconds=seconds, input_tokens=2, output_tokens=3)


def test_latency_groups_by_role_and_model_and_counts_strict_limit() -> None:
    result = summarise_latency(
        [
            call("marker", "a", 30),
            call("reader", "a", 40),
            call("marker", "a", 30.001),
            call("marker", "b", 2),
        ],
        {"marker": 30},
    )
    grouped = {(row.role, row.model): row for row in result}
    assert len(grouped) == 3
    assert grouped["marker", "a"] == LatencySummary(
        role="marker",
        model="a",
        calls=2,
        p50_s=Decimal("30.00"),
        p95_s=Decimal("30.00"),
        max_s=Decimal("30.00"),
        limit_s=Decimal(30),
        over_limit=1,
    )
    assert grouped["marker", "b"].over_limit == 0
    assert grouped["reader", "a"].limit_s is None
    assert grouped["reader", "a"].over_limit == 0
    assert summarise_latency([], {}) == ()


def test_run_cost_wall_time_and_tokens_round_once_half_up() -> None:
    assert run_cost(basis(), Decimal("12.00"), Decimal(0)) == Decimal("3.23")
    assert run_cost(basis(), Decimal("12.00"), Decimal("0.01")) == Decimal("3.24")
    laptop = basis().model_copy(
        update={
            "instance": "laptop",
            "instance_usd_per_hour": Decimal(0),
            "stack_usd_per_hour": Decimal(0),
        }
    )
    assert run_cost(laptop, Decimal(12), Decimal("1.005")) == Decimal("1.01")
    for minutes, tokens in [(Decimal(-1), Decimal(0)), (Decimal(0), Decimal(-1))]:
        with pytest.raises(ValueError):
            run_cost(basis(), minutes, tokens)


@pytest.mark.parametrize(
    ("reference", "read", "expected"),
    [
        ("x=3", "x=8", (1, 3)),
        ("", "abc", (3, 0)),
        ("abc", "", (3, 3)),
        ("kitten", "sitting", (3, 6)),
        ("αβ", "αβ", (0, 2)),
        ("  x=3\n +\t2 ", "x=3 + 2", (0, 7)),
    ],
)
def test_cer(reference: str, read: str, expected: tuple[int, int]) -> None:
    assert cer(reference, read) == expected


def test_reader_accuracy_counts_pages_including_empty_pages_and_weights_characters() -> None:
    result = reader_accuracy(
        [
            [(" x=3 ", "x=3", True), ("abcd", "abXd", True)],
            [],
            [("12", "1", False), ("", "x", False)],
        ]
    )
    assert result == ReaderAccuracy(
        pages=3,
        lines=4,
        line_exact_rate=Decimal("0.2500"),
        cer=Decimal("0.3333"),
        uncertain_lines=2,
        uncertain_and_wrong=1,
        wrong_not_flagged=2,
    )


@pytest.mark.parametrize("pages", [[], [[]], [[("", "x", False)]]])
def test_reader_requires_lines_and_reference_characters(
    pages: list[list[tuple[str, str, bool]]],
) -> None:
    with pytest.raises(ValueError):
        reader_accuracy(pages)


def test_eval_result_roundtrip_and_contract_validation() -> None:
    result = EvalResult(
        run_date=date(2026, 11, 11),
        git_commit="1234567",
        fixture="synthetic",
        scripts=32,
        instance="g4dn.xlarge",
        models={"marker": "a"},
        minutes_to_approval=None,
        minutes_to_delivered=None,
        halted="provider unavailable",
        questions_compared=0,
        disagreements=0,
        agreement_rate=None,
        traps=(TrapCatch(trap="memo 2.3", caught=False, evidence="halted"),),
        teacher_questions=(),
        latency=(),
        cost_zar_set=None,
        cost_zar_script=None,
        cost_basis=basis(),
        reader=None,
    )
    assert EvalResult.model_validate_json(result.model_dump_json()) == result
    assert result.reader_skipped == ""
    with pytest.raises(ValidationError):
        EvalResult.model_validate({**result.model_dump(), "scripts": 0})
    with pytest.raises(ValidationError):
        TrapCatch(trap="", caught=False, evidence="")
    with pytest.raises(ValidationError):
        CostBasis.model_validate({**basis().model_dump(), "zar_per_usd": 0})
