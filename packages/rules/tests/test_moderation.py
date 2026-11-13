"""Contract acceptance tests for the delegated rules task."""

import random
from decimal import Decimal

import pytest

from mothusi_contracts import Mark, MarkedScript, ModerationRule
from mothusi_rules import disagreements, moderation_sample, should_widen


@pytest.fixture
def rule() -> ModerationRule:
    return ModerationRule(
        sample_percent=Decimal(10),
        min_scripts=6,
        spread="top-middle-bottom",
        widen_above_gap_percent=Decimal(10),
    )


def scripts(n: int) -> tuple[MarkedScript, ...]:
    return tuple(
        MarkedScript(
            script_id=f"s{i:02}",
            marker="m",
            total=n - i,
            marks=(Mark(question_id="q", awarded=0, max_marks=1, memo_line="m", principle="p"),),
        )
        for i in range(n)
    )


def test_sample(rule: ModerationRule) -> None:
    data = scripts(32)
    expected = ("s00", "s11", "s22", "s01", "s12", "s23")
    assert moderation_sample(data, rule) == expected
    assert moderation_sample(tuple(reversed(data)), rule) == expected
    assert set(moderation_sample(scripts(4), rule)) == {"s00", "s01", "s02", "s03"}
    assert moderation_sample((), rule) == ()
    assert moderation_sample(scripts(1), rule) == ("s00",)


def test_tie_break_and_ceiling(rule: ModerationRule) -> None:
    data = tuple(s.model_copy(update={"total": 1}) for s in reversed(scripts(7)))
    small = rule.model_copy(update={"min_scripts": 1, "sample_percent": Decimal(15)})
    assert moderation_sample(data, small) == ("s00", "s03")


def test_random_is_seeded(rule: ModerationRule) -> None:
    data = scripts(32)
    random_rule = rule.model_copy(update={"spread": "random"})
    expected = tuple(sorted(random.Random(7).sample([s.script_id for s in data], 6)))
    assert moderation_sample(data, random_rule, seed=7) == expected
    assert moderation_sample(tuple(reversed(data)), random_rule, seed=7) == expected


def test_disagreements_and_widening(marked: MarkedScript, rule: ModerationRule) -> None:
    other = marked.model_copy(
        update={
            "marks": tuple(
                m.model_copy(update={"awarded": m.awarded - 1}) for m in reversed(marked.marks)
            )
        }
    )
    gaps = disagreements(marked, other)
    assert {g.question_id for g in gaps} == {"1.1", "1.2"}
    assert all(
        g.first - g.second == 1 and g.reason == "" and g.script_id == marked.script_id for g in gaps
    )
    assert should_widen(gaps, 10, rule)
    assert not should_widen(gaps[:1], 10, rule)
    assert disagreements(marked, marked) == ()
    assert not should_widen((), 10, rule)


@pytest.mark.parametrize("count", [0, -1])
def test_bad_denominator(rule: ModerationRule, count: int) -> None:
    with pytest.raises(ValueError):
        should_widen((), count, rule)


def test_incomparable_scripts(marked: MarkedScript) -> None:
    for other in [
        marked.model_copy(update={"script_id": "other"}),
        marked.model_copy(update={"marks": marked.marks[:1]}),
    ]:
        with pytest.raises(ValueError):
            disagreements(marked, other)
