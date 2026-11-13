"""Answer-sheet geometry consumed by both printing and intake (§3, §6.3)."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from mothusi_contracts.models import Contract

# Identifiers are opaque: preserve them byte-for-byte for the QR and manifest to agree.
SheetId = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=1, pattern=r"^[^:]+$")
]


class MmRect(Contract):
    """Rectangle in millimetres, top-left origin with y increasing downwards."""

    x: Decimal = Field(ge=0, decimal_places=1)
    y: Decimal = Field(ge=0, decimal_places=1)
    w: Decimal = Field(gt=0, decimal_places=1)
    h: Decimal = Field(gt=0, decimal_places=1)

    @field_validator("x", "y", "w", "h")
    @classmethod
    def _one_decimal_place(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.1"))


class SheetBox(Contract):
    """One unsplit answer region and its separate question label column."""

    question_id: str = Field(min_length=1)
    marks: int = Field(gt=0)
    box: MmRect
    label: MmRect


class SheetPage(Contract):
    """Paper-ordered boxes and the identity encoded on one page."""

    page_no: int = Field(ge=1)
    qr_payload: str = Field(min_length=1)
    boxes: tuple[SheetBox, ...]


class AnswerSheetManifest(Contract):
    """One learner's sheet; name_band applies only to the first page."""

    version: Literal["mothusi:v1"] = "mothusi:v1"
    assessment_id: SheetId
    learner_pseudonym: SheetId
    name_band: MmRect
    qr: MmRect
    fiducials: tuple[MmRect, MmRect, MmRect, MmRect]
    pages: tuple[SheetPage, ...]
