"""Page-exact retrieval contracts (knowledge.md §6.1)."""

import pytest
from pydantic import ValidationError

from mothusi_contracts import CatchUpItem
from mothusi_contracts.models import Passage, PassageKind


def passage(**changes: object) -> Passage:
    return Passage.model_validate(
        dict(
            passage_id="caps.pdf#p21c0",
            source="CAPS FET Mathematics",
            source_key="caps.pdf",
            kind="caps",
            page=21,
            locator="PDF p 21",
            text="Volume of prisms",
            score=0.8123,
        )
        | changes
    )


def test_passage_citation_and_round_trip() -> None:
    item = passage()
    assert item.citation == "CAPS FET Mathematics, PDF p 21"
    assert item.kind is PassageKind.CAPS
    assert item.subject is None and item.grade is None
    assert Passage.model_validate_json(item.model_dump_json()) == item
    with pytest.raises(ValidationError):
        item.source = "Other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,value",
    [
        ("passage_id", ""),
        ("source", " "),
        ("source_key", ""),
        ("locator", ""),
        ("text", ""),
        ("kind", "invented"),
        ("page", 0),
        ("grade", 0),
        ("grade", 13),
        ("score", -0.1),
        ("score", 1.1),
        ("citation", "invented"),
    ],
)
def test_passage_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        passage(**{field: value})


def test_pack_passage_and_kind_values() -> None:
    item = passage(kind="pack", page=None, locator="conventions[1]", score=0)
    assert item.citation == "CAPS FET Mathematics, conventions[1]"
    assert {kind.value for kind in PassageKind} == {
        "caps",
        "atp",
        "poa",
        "circular",
        "policy",
        "pack",
    }


def test_catch_up_citation_defaults_to_empty_and_accepts_copy() -> None:
    fields = dict(week=2, topic="Volume", activity="Practise prisms", atp_source="ATP row")
    assert CatchUpItem.model_validate(fields).citation == ""
    assert (
        CatchUpItem.model_validate(fields | {"citation": passage().citation}).citation
        == passage().citation
    )
