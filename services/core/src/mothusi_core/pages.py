"""Uploaded pages to answer crops, inside the core (intake.md §11.4; no model is called here).

Each upload is split into pages, deskewed on its corner squares, identified by its QR code, has
the name band blanked on page 1, and is cut into one crop per answer box from the learner's
manifest. A page that cannot be read is refused with a message naming the file; nothing is guessed.
Crops are written under the assessment's data directory and are reached by signed, short-lived
links (`crop_link`), never by a path a learner's name could appear in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from mothusi_contracts import AnswerSheetManifest, PageImage
from mothusi_core.agents.reader import CropRef
from mothusi_intake.pages import (
    blank_name_band,
    crop_answers,
    decode_qr,
    deskew,
    pages_from_upload,
)

CROP_LINK_S = 15 * 60
_SAFE = re.compile(r"[A-Za-z0-9._-]+")
_KEY = (os.environ.get("MOTHUSI_NOTE_KEY") or "").encode() or secrets.token_bytes(32)


@dataclass
class PageIntake:
    """Crops per learner (pseudonym -> crops in paper order) and the refusals, one per page."""

    crops: dict[str, list[CropRef]] = field(default_factory=dict[str, list[CropRef]])
    refused: list[str] = field(default_factory=list[str])
    read: dict[str, set[int]] = field(default_factory=dict[str, set[int]])
    """Page numbers cut into crops, per learner."""


def load_manifests(inputs: Path) -> dict[str, AnswerSheetManifest]:
    """The printed sheets' layouts by learner pseudonym, written when the assessment was made."""
    return {
        m.learner_pseudonym: m
        for m in (
            AnswerSheetManifest.model_validate(x)
            for x in json.loads((inputs / "manifests.json").read_text())
        )
    }


def missing_pages(
    intake: PageIntake, manifests: Mapping[str, AnswerSheetManifest]
) -> dict[str, list[int]]:
    """Printed pages that no upload supplied, per learner (every page for an absent learner)."""
    out: dict[str, list[int]] = {}
    for pseudonym in sorted(manifests):
        printed = {page.page_no for page in manifests[pseudonym].pages}
        gone = sorted(printed - intake.read.get(pseudonym, set()))
        if gone:
            out[pseudonym] = gone
    return out


def crop_key(question_id: str) -> str:
    """An opaque, filesystem-safe name for a question's crop: any memo id ("1(a)", "2/1") works."""
    return hashlib.sha256(question_id.encode()).hexdigest()[:24]


def crop_path(crop_dir: Path, pseudonym: str, question_id: str) -> Path:
    return crop_dir / safe_name(pseudonym) / f"{crop_key(question_id)}.png"


def safe_name(name: str) -> str:
    """An upload's file name as stored: letters, digits, '.', '_' and '-' only."""
    base = Path(name).name
    if not base or not _SAFE.fullmatch(base) or base.startswith("."):
        raise ValueError(f"{name!r}: use a file name of letters, digits, '.', '_' and '-'")
    return base


def read_uploads(
    assessment_id: str,
    upload_dir: Path,
    manifests: Mapping[str, AnswerSheetManifest],
    crop_dir: Path,
) -> PageIntake:
    """Every uploaded page to crops; unreadable, foreign or duplicate pages are refused."""
    out = PageIntake()
    seen: dict[tuple[str, int], str] = {}
    ordered: dict[str, list[tuple[int, int, CropRef]]] = {}
    for upload in sorted(p for p in upload_dir.iterdir() if p.is_file()):
        try:
            pages = pages_from_upload(upload.read_bytes(), upload.name)
        except ValueError as error:
            out.refused.append(str(error))
            continue
        for number, page in enumerate(pages, start=1):
            label = upload.name if len(pages) == 1 else f"{upload.name} page {number}"
            try:
                flat = deskew(page)
            except ValueError as error:
                out.refused.append(f"{label}: {error}")
                continue
            qr = decode_qr(flat) or decode_qr(page)
            if qr is None:
                out.refused.append(
                    f"{label}: no Mothusi QR code (only Mothusi answer sheets can be read)"
                )
                continue
            if qr.assessment_id != assessment_id:
                out.refused.append(f"{label}: QR code for another assessment")
                continue
            manifest = manifests.get(qr.learner_pseudonym)
            if manifest is None:
                out.refused.append(f"{label}: QR code for a learner not on this class list")
                continue
            key = (qr.learner_pseudonym, qr.page_no)
            if key in seen:
                out.refused.append(f"{label}: the same page as {seen[key]}")
                continue
            seen[key] = label
            if qr.page_no == 1:
                flat = blank_name_band(flat, manifest.name_band)
            image = PageImage(
                assessment_id=assessment_id,
                learner_pseudonym=qr.learner_pseudonym,
                page_no=qr.page_no,
                source_key=label,
            )
            try:
                crops = crop_answers(image, flat, manifest)
            except ValueError as error:
                out.refused.append(str(error))
                continue
            for index, crop in enumerate(crops):
                path = crop_path(crop_dir, qr.learner_pseudonym, crop.question_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(crop.png)
                ref = CropRef(question_id=crop.question_id, path=str(path))
                ordered.setdefault(qr.learner_pseudonym, []).append((qr.page_no, index, ref))
            out.read.setdefault(qr.learner_pseudonym, set()).add(qr.page_no)
    for pseudonym, refs in ordered.items():
        out.crops[pseudonym] = [ref for _page, _index, ref in sorted(refs, key=lambda r: r[:2])]
    return out


def _sign(assessment_id: str, pseudonym: str, key: str, expires: int) -> str:
    message = f"{assessment_id}/{pseudonym}/{key}/{expires}".encode()
    return hmac.new(_KEY, message, hashlib.sha256).hexdigest()


def crop_link(
    assessment_id: str, pseudonym: str, question_id: str, now: float | None = None
) -> str:
    """A 15-minute signed API path to one crop (images cannot send the teacher's token).

    Relative to the core's API base, like every other path the console calls (the console adds
    its `/api` prefix). GET /assessments/{id}/reading re-signs the links of a pending check.
    """
    expires = int((now if now is not None else time.time()) + CROP_LINK_S)
    key = crop_key(question_id)
    sig = _sign(assessment_id, pseudonym, key, expires)
    return f"/c/{assessment_id}/{pseudonym}/{key}?exp={expires}&sig={sig}"


def check_crop_link(assessment_id: str, pseudonym: str, key: str, expires: int, sig: str) -> bool:
    """True only for an unexpired link this process signed."""
    if expires < time.time():
        return False
    return hmac.compare_digest(sig, _sign(assessment_id, pseudonym, key, expires))
