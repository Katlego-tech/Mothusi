"""Dispatch guards inspect decoded strings and nested first-mark objects."""

from typing import Any

import pytest

from mothusi_contracts import AgentRole, Brief, MarkedScript
from mothusi_rules import BLIND, check_brief_blind


def brief(inputs: dict[str, Any], agent: AgentRole = AgentRole.MODERATOR) -> Brief:
    return Brief(brief_id="b", assessment_id="a", agent=agent, task="moderate", inputs=inputs)


def test_blind_inputs_pass() -> None:
    value = brief(
        {
            "memo": {"items": [{"question_id": "1", "answer": "x=3"}]},
            "scripts": [{"script_id": "L-001"}],
        }
    )
    result = check_brief_blind(value, ("Test Learner",))
    assert result.passed and result.detail == "brief is blind"
    assert result.check == BLIND == "blind_brief" and result.about == "b"


def test_nested_first_mark_shape_and_key(marked: MarkedScript) -> None:
    result = check_brief_blind(brief({"nested": [{"first": marked.model_dump()}]}), ())
    assert result.detail == "moderator brief carries first marks (1 objects)"
    assert not result.passed
    assert not check_brief_blind(brief({"marked": []}), ()).passed
    # The envelope's Any inputs can carry a contract instance, not only its dict form.
    assert not check_brief_blind(brief({"nested": [marked]}), ()).passed


@pytest.mark.parametrize("text", ["Zanele M", "zanele m", "ZANELE   M", "Zanele\tM", "Zanele\nM"])
def test_name_normalisation(text: str) -> None:
    result = check_brief_blind(
        brief({"nested": [{"value": text}]}, AgentRole.MARKER), ("Zanele M",)
    )
    assert result.detail == "learner name 'Zanele M' appears in the brief"
    assert not result.passed


@pytest.mark.parametrize("name", ["Zoë M", "Zoe\u0308 M"])
@pytest.mark.parametrize("text", ["Zoë M", "Zoe\u0308 M"])
def test_canonical_unicode_equivalence(name: str, text: str) -> None:
    assert not check_brief_blind(brief({"value": text}), (name,)).passed


def test_dict_keys_and_tuples() -> None:
    result = check_brief_blind(brief({"outer": ({"Test Learner": "other"},)}), ("Test Learner",))
    assert not result.passed


@pytest.mark.parametrize("agent", [AgentRole.WRITER, AgentRole.EXECUTOR])
def test_teacher_side_roles_can_see_names(agent: AgentRole) -> None:
    assert check_brief_blind(brief({"value": "Test Learner"}, agent), ("Test Learner",)).passed


def test_small_names_and_no_mark_leak_for_other_roles() -> None:
    assert check_brief_blind(brief({"value": "Al Li X"}), ("Al", "Li", "X")).passed
    assert check_brief_blind(brief({"marked": []}, AgentRole.MARKER), ()).passed


def test_sorted_failures_and_multiple_objects() -> None:
    value = brief({"marked": [{"marks": [], "total": 0, "marker": "m"}], "learner": "Test Learner"})
    result = check_brief_blind(value, ("Test Learner",))
    assert result.detail == (
        "learner name 'Test Learner' appears in the brief; "
        "moderator brief carries first marks (2 objects)"
    )


def test_cyclic_container_is_safe() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    assert check_brief_blind(brief({"cycle": cycle}), ()).passed


@pytest.mark.parametrize(
    "text",
    [
        "Tha\u200bbo Nko\u00adsi",
        "\uff34\uff48\uff41\uff42\uff4f \uff2e\uff4b\uff4f\uff53\uff49",
        "thabo  nkosi",
    ],
)
def test_zero_width_soft_hyphen_and_fullwidth_variants_are_found(text: str) -> None:
    """Reviewer: these had passed; NFKC and dropping format characters close them."""
    assert not check_brief_blind(brief({"value": text}, AgentRole.MARKER), ("Thabo Nkosi",)).passed


def test_a_marked_script_hidden_in_a_json_string_or_a_set_is_found(marked: MarkedScript) -> None:
    as_text = marked.model_dump_json()
    assert not check_brief_blind(brief({"attachment": as_text}), ()).passed
    assert not check_brief_blind(
        brief({"names": frozenset({"Thabo Nkosi"})}, AgentRole.MARKER), ("Thabo Nkosi",)
    ).passed
    assert not check_brief_blind(
        brief({"raw": b"Thabo Nkosi"}, AgentRole.MARKER), ("Thabo Nkosi",)
    ).passed
