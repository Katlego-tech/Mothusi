"""Exhibit refusals name every reason against the cited page, in stop order."""

from collections.abc import Callable

import pytest

from mothusi_contracts import Citation, Exhibit, ExhibitModel, Stop
from mothusi_rules import LICENCES, MAX_MODEL_BYTES, check_exhibit


def exhibit() -> Exhibit:
    return Exhibit(
        exhibit_id="measurement",
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
                citations=(
                    Citation(document="CAPS.pdf", pdf_page=33, quote="Volume and surface areas"),
                ),
            ),
        ),
    )


def page_text(document: str, page: int) -> str:
    return {("CAPS.pdf", 33): "Revise the VOLUME\n and\t surface   areas of cylinders."}[
        document, page
    ]


def test_quote_on_its_page_ignores_case_and_collapses_whitespace() -> None:
    result = check_exhibit(exhibit(), page_text)
    assert result.passed
    assert result.check == "exhibit"
    assert result.about == "measurement"
    assert result.detail
    assert exhibit().approved_at is None  # Approval belongs to the serving gate.


@pytest.mark.parametrize("licence", ["CC-BY-4.0", "CC0-1.0"])
def test_allowed_licences_and_maximum_bytes(licence: str) -> None:
    assert MAX_MODEL_BYTES == 6 * 1024 * 1024
    assert LICENCES == ("CC-BY-4.0", "CC0-1.0")
    original = exhibit()
    model = original.model.model_copy(update={"licence": licence, "bytes": MAX_MODEL_BYTES})
    assert check_exhibit(original.model_copy(update={"model": model}), page_text).passed


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"licence": "CC-BY-NC"}, "licence"),
        ({"bytes": 7 * 1024 * 1024}, "bytes"),
        ({"bytes": 6 * 1024 * 1024 + 1}, "bytes"),
    ],
)
def test_model_refusals(changes: dict[str, object], reason: str) -> None:
    original = exhibit()
    result = check_exhibit(
        original.model_copy(
            update={
                "model": original.model.model_copy(update=changes),
            }
        ),
        page_text,
    )
    assert not result.passed
    assert reason in result.detail
    assert "; " not in result.detail


@pytest.mark.parametrize(
    "citations,detail",
    [
        ((), "overview: no citation"),
        (
            (Citation(document="CAPS.pdf", pdf_page=33, quote="Invented"),),
            "overview: quote not on CAPS.pdf p 33",
        ),
        (
            (Citation(document="CAPS.pdf", pdf_page=34, quote="Volume"),),
            "overview: CAPS.pdf p 34 not held",
        ),
        (
            (Citation(document="Other.pdf", pdf_page=33, quote="Volume"),),
            "overview: Other.pdf p 33 not held",
        ),
    ],
)
def test_citation_refusals(citations: tuple[Citation, ...], detail: str) -> None:
    original = exhibit()
    stop = original.stops[0].model_copy(update={"citations": citations})
    result = check_exhibit(original.model_copy(update={"stops": (stop,)}), page_text)
    assert not result.passed
    assert result.detail == detail


def test_unreadable_page_value_error() -> None:
    def unreadable(document: str, page: int) -> str:
        raise ValueError("Cannot extract page")

    result = check_exhibit(exhibit(), unreadable)
    assert not result.passed
    assert result.detail == "overview: CAPS.pdf p 33 not held"


def test_reports_all_failures_in_stop_and_citation_order() -> None:
    original = exhibit()
    missing = Citation(document="Missing.pdf", pdf_page=1, quote="Text")
    wrong = Citation(document="CAPS.pdf", pdf_page=33, quote="Invented")
    stops = (
        original.stops[0].model_copy(update={"stop_id": "z", "citations": (missing, wrong)}),
        original.stops[0].model_copy(update={"stop_id": "a", "citations": ()}),
    )
    model = original.model.model_copy(update={"licence": "CC-BY-NC", "bytes": 7 * 1024 * 1024})
    result = check_exhibit(original.model_copy(update={"model": model, "stops": stops}), page_text)
    assert not result.passed
    reasons = result.detail.split("; ")
    assert "licence" in reasons[0] and "CC-BY-NC" in reasons[0]
    assert "bytes" in reasons[1]
    assert reasons[2:] == [
        "z: Missing.pdf p 1 not held",
        "z: quote not on CAPS.pdf p 33",
        "a: no citation",
    ]


def test_unexpected_reader_errors_propagate() -> None:
    def broken(document: str, page: int) -> str:
        raise RuntimeError("Reader failed")

    reader: Callable[[str, int], str] = broken
    with pytest.raises(RuntimeError, match="Reader failed"):
        check_exhibit(exhibit(), reader)
