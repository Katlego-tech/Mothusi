"""Exhibition manifests shared by validation, publication and the hall viewer."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from mothusi_contracts.models import Contract


class Citation(Contract):
    """Words on a held document's one-based PDF page."""

    document: str = Field(min_length=1)
    pdf_page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class Stop(Contract):
    """A camera position with caption, narration, audio and supporting citations."""

    stop_id: str = Field(pattern=r"^[a-z0-9-]+$")
    camera_orbit: str = Field(min_length=1)
    camera_target: str = Field(min_length=1)
    field_of_view: str = Field(min_length=1)
    caption: str = Field(min_length=1, max_length=90)
    narration: str = Field(min_length=1, max_length=600)
    audio_key: str = Field(min_length=1)
    citations: tuple[Citation, ...] = ()


class ExhibitModel(Contract):
    """Model provenance as declared; the exhibit rule checks licence and size."""

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1)
    source_url: str = Field(min_length=1)
    author: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    credit: str = Field(min_length=1)


class Exhibit(Contract):
    """An ordered narrated exhibit and the teacher's optional approval."""

    exhibit_id: str = Field(pattern=r"^[a-z0-9-]+$")
    subject: Literal["Mathematics", "Accounting"]
    grade: int = Field(ge=10, le=12)
    title: str = Field(min_length=1)
    atp_topic: str = Field(min_length=1)
    model: ExhibitModel
    stops: tuple[Stop, ...] = Field(min_length=1, max_length=6)
    approved_at: datetime | None = None

    @field_validator("stops")
    @classmethod
    def _unique_stop_ids(cls, stops: tuple[Stop, ...]) -> tuple[Stop, ...]:
        if len({stop.stop_id for stop in stops}) != len(stops):
            raise ValueError("stop_ids must be unique")
        return stops
