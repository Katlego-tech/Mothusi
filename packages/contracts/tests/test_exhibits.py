"""Exhibition manifests preserve §6's fields and structural bounds."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mothusi_contracts import Citation, Contract, Exhibit, ExhibitModel, Stop


def manifest() -> Exhibit:
    return Exhibit(
        exhibit_id="maths-gr10-measurement",
        subject="Mathematics",
        grade=10,
        title="Measurement",
        atp_topic="MEASUREMENT",
        model=ExhibitModel(
            key="exhibits/measurement/model.glb",
            sha256="a" * 64,
            bytes=1024,
            source_url="https://example.org/model",
            author="Author",
            licence="CC-BY-4.0",
            credit="Model by Author, CC BY 4.0",
        ),
        stops=(
            Stop(
                stop_id="overview",
                camera_orbit="30deg 75deg 2.5m",
                camera_target="0m 0.4m 0m",
                field_of_view="30deg",
                caption="Measurement",
                narration="Study measurement.",
                audio_key="exhibits/measurement/overview.mp3",
            ),
        ),
    )


def test_round_trip_defaults_and_contract_base() -> None:
    exhibit = manifest()
    assert exhibit.approved_at is None
    assert exhibit.stops[0].citations == ()
    assert isinstance(exhibit, Contract)
    approved = exhibit.model_copy(update={"approved_at": datetime(2026, 11, 11, tzinfo=UTC)})
    assert Exhibit.model_validate_json(approved.model_dump_json()) == approved
    with pytest.raises(ValidationError):
        exhibit.title = "Changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        Exhibit.model_validate(exhibit.model_dump() | {"unknown": 1})


@pytest.mark.parametrize("grade", [10, 11, 12])
def test_permitted_grades_and_accounting(grade: int) -> None:
    assert (
        Exhibit.model_validate(
            manifest().model_dump()
            | {
                "grade": grade,
                "subject": "Accounting",
            }
        ).grade
        == grade
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("exhibit_id", "Bad id"),
        ("subject", "Physics"),
        ("grade", 9),
        ("grade", 13),
        ("title", ""),
        ("atp_topic", ""),
        ("stops", []),
    ],
)
def test_exhibit_constraints(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Exhibit.model_validate(manifest().model_dump() | {field: value})


def test_unique_stop_ids_and_stop_count() -> None:
    data = manifest().model_dump()
    stop = data["stops"][0]
    with pytest.raises(ValidationError, match="unique"):
        Exhibit.model_validate(data | {"stops": [stop, stop]})
    six = [stop | {"stop_id": f"stop-{i}"} for i in range(6)]
    assert len(Exhibit.model_validate(data | {"stops": six}).stops) == 6
    with pytest.raises(ValidationError):
        Exhibit.model_validate(data | {"stops": [*six, stop]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("stop_id", ""),
        ("stop_id", "Bad"),
        ("camera_orbit", ""),
        ("camera_target", ""),
        ("field_of_view", ""),
        ("caption", ""),
        ("caption", "x" * 91),
        ("narration", ""),
        ("narration", "x" * 601),
        ("audio_key", ""),
    ],
)
def test_stop_constraints(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Stop.model_validate(manifest().stops[0].model_dump() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("document", ""),
        ("pdf_page", 0),
        ("quote", ""),
    ],
)
def test_citation_constraints(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Citation.model_validate(
            {"document": "CAPS.pdf", "pdf_page": 1, "quote": "Text"} | {field: value}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", ""),
        ("sha256", "a" * 63),
        ("sha256", "A" * 64),
        ("bytes", 0),
        ("source_url", ""),
        ("author", ""),
        ("licence", ""),
        ("credit", ""),
    ],
)
def test_model_constraints(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ExhibitModel.model_validate(manifest().model.model_dump() | {field: value})


def test_refusals_belong_to_rules_and_text_bounds_are_inclusive() -> None:
    model = ExhibitModel.model_validate(
        manifest().model.model_dump()
        | {
            "licence": "CC-BY-NC",
            "bytes": 7 * 1024 * 1024,
        }
    )
    assert model.licence == "CC-BY-NC"
    stop = Stop.model_validate(
        manifest().stops[0].model_dump()
        | {
            "caption": "x" * 90,
            "narration": "x" * 600,
            "citations": [{"document": "CAPS.pdf", "pdf_page": 1, "quote": "Text"}],
        }
    )
    assert isinstance(stop.citations[0], Citation)
