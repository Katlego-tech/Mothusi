"""Contract acceptance tests for the delegated rules task."""

import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from mothusi_contracts import SubjectPack
from mothusi_rules import convert_mark, find_task


@pytest.fixture
def pack() -> SubjectPack:
    path = Path(__file__).resolve().parents[3] / "packs/dbe-caps/mathematics-gr10.toml"
    return SubjectPack.model_validate(tomllib.loads(path.read_text()))


@pytest.mark.parametrize(
    ("raw", "sba", "year"), [(37, "10.36", "4.14"), (50, "14.00", "5.60"), (0, "0.00", "0.00")]
)
def test_worked_examples(pack: SubjectPack, raw: int, sba: str, year: str) -> None:
    result = convert_mark("s", raw, find_task(pack, "Test"), pack)
    assert (result.raw, result.out_of, result.script_id) == (raw, 50, "s")
    assert result.sba_points == Decimal(sba)
    assert result.year_points == Decimal(year)
    assert result.sba_points.as_tuple().exponent == -2


@pytest.mark.parametrize("raw", [-1, 51])
def test_invalid_raw(pack: SubjectPack, raw: int) -> None:
    with pytest.raises(ValueError):
        convert_mark("s", raw, find_task(pack, "Test"), pack)


def test_exact_task_name(pack: SubjectPack) -> None:
    for name in ["test", "missing"]:
        with pytest.raises(ValueError) as error:
            find_task(pack, name)
        for task in pack.poa:
            assert task.name in str(error.value)


def test_half_up_and_round_each_step(pack: SubjectPack) -> None:
    task = find_task(pack, "Test").model_copy(
        update={"max_marks": 200, "weight_percent": Decimal("1")}
    )
    result = convert_mark("s", 1, task, pack.model_copy(update={"sba_percent": 50}))
    assert result.sba_points == Decimal("0.01")
    assert result.year_points == Decimal("0.01")
