"""The sheet manifest is immutable, serialisable geometry shared with intake."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mothusi_contracts import AnswerSheetManifest, MmRect, SheetBox, SheetPage


def test_sheets_contract_round_trip() -> None:
    rect = MmRect(x=Decimal("14.0"), y=Decimal("48.0"), w=Decimal("15.0"), h=Decimal("30.0"))
    box = SheetBox(question_id="1", marks=2, box=rect, label=rect)
    page = SheetPage(page_no=1, qr_payload="mothusi:v1:a:L-001:1", boxes=(box,))
    manifest = AnswerSheetManifest(
        assessment_id="a",
        learner_pseudonym="L-001",
        name_band=rect,
        qr=rect,
        fiducials=(rect, rect, rect, rect),
        pages=(page,),
    )
    assert manifest.version == "mothusi:v1"
    assert json.loads(manifest.model_dump_json())["name_band"]["x"] == "14.0"
    assert AnswerSheetManifest.model_validate_json(manifest.model_dump_json()) == manifest
    with pytest.raises(ValidationError):
        rect.x = Decimal(1)  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        MmRect(x=1, y=1, w=1, h=1, unknown=1)  # type: ignore[call-arg]
