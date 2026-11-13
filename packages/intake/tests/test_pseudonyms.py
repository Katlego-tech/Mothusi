"""System criterion: no learner name reaches an agent; the mapping is reproducible."""

from __future__ import annotations

import pytest

from mothusi_intake import pseudonymise


def test_order_is_the_class_list_order() -> None:
    assert pseudonymise(["Zanele M", "Aarav P", "Thabo  K"]) == {
        "Zanele M": "L-001",
        "Aarav P": "L-002",
        "Thabo K": "L-003",
    }


def test_width_grows_past_999_learners() -> None:
    mapping = pseudonymise([f"n{i}" for i in range(1000)])
    assert mapping["n0"] == "L-0001" and mapping["n999"] == "L-1000"


def test_duplicates_and_blanks_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate learner names: A B"):
        pseudonymise(["A B", "A  B"])
    with pytest.raises(ValueError, match="needs a name"):
        pseudonymise(["A", "  "])
