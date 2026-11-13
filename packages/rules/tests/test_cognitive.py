"""Contract acceptance tests for the delegated rules task."""

import tomllib
from pathlib import Path

import pytest

from mothusi_contracts import Memo, MemoItem, SubjectPack
from mothusi_rules import COGNITIVE_GRID, check_cognitive_grid


@pytest.fixture
def pack() -> SubjectPack:
    path = Path(__file__).resolve().parents[3] / "packs/dbe-caps/mathematics-gr10.toml"
    return SubjectPack.model_validate(tomllib.loads(path.read_text()))


def grid(shares: tuple[int, ...], levels: tuple[str, ...]) -> Memo:
    return Memo(
        assessment_id="a",
        total_marks=sum(shares),
        items=tuple(
            MemoItem(question_id=str(i), max_marks=share, answer="a", level=level)
            for i, (share, level) in enumerate(zip(shares, levels, strict=True))
        ),
    )


@pytest.mark.parametrize(
    ("shares", "passed"),
    [((20, 35, 30, 15), True), ((25, 30, 30, 15), True), ((2501, 2999, 3000, 1500), False)],
)
def test_targets_and_tolerance(pack: SubjectPack, shares: tuple[int, ...], passed: bool) -> None:
    assert pack.cognitive is not None
    memo = grid(shares, tuple(pack.cognitive.levels))
    result = check_cognitive_grid(memo, pack)
    assert result.passed is passed
    assert result.check == COGNITIVE_GRID == "cognitive_grid"
    assert result.about == "a"
    for level, target in pack.cognitive.levels.items():
        assert level + ":" in result.detail
        assert f"(target {target}%)" in result.detail
    assert ("OFF" in result.detail) is (not passed)
    if not passed:
        assert "knowledge: 25.01%" in result.detail


def test_unknown_level(pack: SubjectPack) -> None:
    memo = grid(
        (20, 35, 30, 15), ("recall", "routine_procedures", "complex_procedures", "problem_solving")
    )
    result = check_cognitive_grid(memo, pack)
    assert not result.passed
    assert "unknown level 'recall' on 0" in result.detail
    assert "knowledge: 0.00%" in result.detail


def test_a_pack_without_a_grid_cannot_be_checked(pack: SubjectPack) -> None:
    memo = grid((20, 35, 30, 15), tuple(pack.cognitive.levels)) if pack.cognitive else None
    assert memo is not None
    with pytest.raises(ValueError, match="no cognitive grid"):
        check_cognitive_grid(memo, pack.model_copy(update={"cognitive": None}))


@pytest.mark.parametrize(
    ("shares", "passed"),
    [((45, 60, 45), True), ((52, 60, 38), True), ((53, 60, 37), False)],
)
def test_the_accounting_grid_on_a_150_mark_paper(shares: tuple[int, ...], passed: bool) -> None:
    """CAPS Accounting 30/40/30, Mothusi's 5-point tolerance: 52/150 is 34.67%, 53/150 is 35.33%."""
    path = Path(__file__).resolve().parents[3] / "packs/dbe-caps/accounting-gr10.toml"
    pack = SubjectPack.model_validate(tomllib.loads(path.read_text()))
    assert pack.cognitive is not None
    result = check_cognitive_grid(grid(shares, tuple(pack.cognitive.levels)), pack)
    assert result.passed is passed
    assert "knowledge_comprehension:" in result.detail and "(target 30%)" in result.detail
