"""Deterministic page-image intake; no model calls or network access."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil, floor
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray as Array

from mothusi_contracts import (
    Answer,
    AnswerCrop,
    AnswerSheetManifest,
    MmRect,
    PageImage,
    Transcription,
)

_PIXELS_PER_MM = 150 / 25.4
_CORNER_ERROR = "retake the photo: all four corner squares must be visible"


@dataclass(frozen=True)
class QrPayload:
    """Identity printed on a Mothusi v1 sheet; no learner name is encoded."""

    assessment_id: str
    learner_pseudonym: str
    page_no: int


def _image(data: bytes) -> Array[np.uint8]:
    if not data:
        raise ValueError("cannot read page image")
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise ValueError("cannot read page image") from exc
    if image is None:
        raise ValueError("cannot read page image")
    return cast(Array[np.uint8], image)


def _png(image: Array[np.uint8]) -> bytes:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("cannot encode page image")
    return encoded.tobytes()


def _qr_text(image: Array[np.uint8]) -> str:
    # The contour detector misses about 3% of clean printed sheets (it depends on the
    # payload's module pattern) and sometimes raises (qrcode.cpp, error -2) instead of
    # returning ""; the ArUco-based detector reads those. An error is a miss, so the
    # next detector or attempt still runs.
    for detector in (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()):
        try:
            payload, _, _ = detector.detectAndDecode(image)
        except cv2.error:
            continue
        if payload:
            return payload
    return ""


def decode_qr(page: bytes) -> QrPayload | None:
    """Decode Mothusi identity; callers validate assessment membership and duplicates."""
    try:
        image = _image(page)
    except ValueError:
        return None
    payload = _qr_text(image)
    if not payload:
        try:
            corrected = _image(deskew(page))
        except ValueError:
            return None
        payload = _qr_text(corrected)
        if not payload:
            # Two resamplings plus sensor noise can fragment QR module edges.
            payload = _qr_text(cast(Array[np.uint8], cv2.GaussianBlur(corrected, (3, 3), 0)))
    match = re.fullmatch(r"mothusi:v1:([^:]+):([^:]+):([1-9][0-9]*)", payload)
    if match is None:
        return None
    return QrPayload(match[1], match[2], int(match[3]))


def deskew(page: bytes) -> bytes:
    """Recover A4 at 150 dpi using four solid corner fiducials.

    Their centres map to (8, 8), (202, 8), (202, 289), (8, 289) mm in
    the fixed v1 paper format. Centres avoid vertex ambiguity under rotation.
    Answer and name rectangles are separately read from the manifest.
    """
    try:
        image = _image(page)
    except ValueError as exc:
        raise ValueError(_CORNER_ERROR) from exc
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    candidates: list[list[tuple[float, float]]] = [[], [], [], []]
    minimum = min(width, height)
    for contour in contours:
        area = cv2.contourArea(contour)
        if not (minimum * 0.018) ** 2 <= area <= (minimum * 0.09) ** 2:
            continue
        polygon = cv2.approxPolyDP(contour, 0.035 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        (cx, cy), (rw, rh), _ = cv2.minAreaRect(contour)
        if not 0.65 <= rw / max(rh, 1) <= 1.55 or area / (rw * rh) < 0.85:
            continue
        mask = np.zeros_like(dark)
        cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
        if cv2.mean(dark, mask=mask)[0] < 240:
            continue
        left, right = cx < width * 0.30, cx > width * 0.70
        top, bottom = cy < height * 0.25, cy > height * 0.75
        if left and top:
            candidates[0].append((cx, cy))
        elif right and top:
            candidates[1].append((cx, cy))
        elif right and bottom:
            candidates[2].append((cx, cy))
        elif left and bottom:
            candidates[3].append((cx, cy))
    if any(len(corner) != 1 for corner in candidates):
        raise ValueError(_CORNER_ERROR)
    source = np.array([corner[0] for corner in candidates], dtype=np.float32)
    target = np.array([(8, 8), (202, 8), (202, 289), (8, 289)], dtype=np.float32) * _PIXELS_PER_MM
    transform = cv2.getPerspectiveTransform(source, target)
    restored = cv2.warpPerspective(image, transform, (1240, 1754), borderValue=(255, 255, 255))
    # Identical fiducials leave a 180-degree ambiguity. In the fixed v1 format
    # the QR occupies (166, 14, 30, 30) mm; its mirrored region is bottom-left.
    # Density works even when resampling/noise prevents decoding the QR itself.
    x0, x1 = floor(166 * _PIXELS_PER_MM), ceil(196 * _PIXELS_PER_MM)
    y0, y1 = floor(14 * _PIXELS_PER_MM), ceil(44 * _PIXELS_PER_MM)
    restored_gray = cv2.cvtColor(restored, cv2.COLOR_BGR2GRAY)
    top_right = restored_gray[y0:y1, x0:x1]
    bottom_left = restored_gray[1754 - y1 : 1754 - y0, 1240 - x1 : 1240 - x0]
    if np.count_nonzero(bottom_left < 128) > np.count_nonzero(top_right < 128):
        restored = cv2.rotate(restored, cv2.ROTATE_180)
    return _png(cast(Array[np.uint8], restored))


def _bounds(band: MmRect, margin: float = 0) -> tuple[int, int, int, int]:
    return (
        max(0, floor((float(band.x) - margin) * _PIXELS_PER_MM)),
        max(0, floor((float(band.y) - margin) * _PIXELS_PER_MM)),
        min(1240, ceil((float(band.x + band.w) + margin) * _PIXELS_PER_MM)),
        min(1754, ceil((float(band.y + band.h) + margin) * _PIXELS_PER_MM)),
    )


def _a4_image(page: bytes) -> Array[np.uint8]:
    image = _image(page)
    if image.shape[:2] != (1754, 1240):
        raise ValueError("expected a deskewed A4 page at 150 dpi (1240 x 1754)")
    return image


def blank_name_band(page: bytes, band: MmRect) -> bytes:
    """Erase the manifest rectangle plus 2 mm; the caller selects page 1."""
    image = _a4_image(page)
    x0, y0, x1, y1 = _bounds(band, 2)
    image[y0:y1, x0:x1] = 255
    return _png(image)


def crop_answers(
    page: PageImage, image: bytes, manifest: AnswerSheetManifest
) -> tuple[AnswerCrop, ...]:
    """Crop manifest boxes in paper order, validating the page identity first."""
    if page.assessment_id != manifest.assessment_id:
        raise ValueError(f"{page.source_key}: QR code for another assessment")
    if page.learner_pseudonym != manifest.learner_pseudonym:
        raise ValueError(f"{page.source_key}: QR code for another learner")
    sheet = next((p for p in manifest.pages if p.page_no == page.page_no), None)
    if sheet is None:
        raise ValueError(f"{page.source_key}: page {page.page_no} is not in the manifest")
    pixels = _a4_image(image)
    crops: list[AnswerCrop] = []
    for box in sheet.boxes:
        rect = box.box
        if rect.x + rect.w > 210 or rect.y + rect.h > 297:
            raise ValueError(f"{page.source_key}: answer box is outside the A4 page")
        x0, y0, x1, y1 = _bounds(rect)
        crops.append(
            AnswerCrop(
                learner_pseudonym=page.learner_pseudonym,
                question_id=box.question_id,
                page_no=page.page_no,
                png=_png(pixels[y0:y1, x0:x1]),
            )
        )
    return tuple(crops)


def to_answer(question_id: str, crops: Sequence[Transcription]) -> Answer:
    """Join reader lines in crop order, retaining uncertainty by line position."""
    lines: list[str] = []
    uncertain: list[int] = []
    for crop in crops:
        for line in crop.lines:
            parts = line.text.split("\n")
            if line.uncertain:
                uncertain.extend(range(len(lines), len(lines) + len(parts)))
            lines.extend(parts)
    text = "\n".join(lines)
    if not text.strip():
        return Answer(question_id=question_id, text="", uncertain_lines=(0,) if uncertain else ())
    return Answer(question_id=question_id, text=text, uncertain_lines=tuple(uncertain))


def sweep_names(answer: Answer, names: Mapping[str, str]) -> tuple[Answer, int]:
    """Replace whole names once, longest first, without re-sweeping pseudonyms."""
    if not names:
        return answer, 0
    ordered = sorted(names, key=len, reverse=True)
    if any(not name.strip() or "\n" in name or "\r" in name for name in ordered):
        raise ValueError("class-list names must be nonempty single lines")
    if any("\n" in p or "\r" in p for p in names.values()):
        raise ValueError("pseudonyms must not contain line breaks")
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join("(" + re.escape(n) + ")" for n in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        # Select the matching alternative, rather than casefolding the matched
        # text: Unicode IGNORECASE also matches dotted and dotless I variants.
        assert match.lastindex is not None
        return names[ordered[match.lastindex - 1]]

    text, count = pattern.subn(replace, answer.text)
    return Answer.model_validate({**answer.model_dump(), "text": text}), count


def pages_from_upload(data: bytes, filename: str) -> tuple[bytes, ...]:
    """Keep PNG/JPEG bytes intact or render every PDF page to PNG at 150 dpi."""
    from pathlib import Path
    from typing import cast

    import cv2
    import numpy as np
    import pypdfium2 as pdfium  # type: ignore[reportMissingTypeStubs]
    from numpy.typing import NDArray

    suffix = Path(filename).suffix.lower()
    error = f"Cannot read upload {filename!r}: expected PNG, JPEG or PDF"
    if suffix in {".png", ".jpg", ".jpeg"}:
        if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise ValueError(error)
        if cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR) is None:
            raise ValueError(error)
        return (data,)
    if suffix != ".pdf":
        raise ValueError(error)

    pages: list[bytes] = []
    scale = 150 / 72
    try:
        with pdfium.PdfDocument(data) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    size = (
                        max(1, round(page.get_width() * scale)),
                        max(1, round(page.get_height() * scale)),
                    )
                    # pypdfium2 has no stubs and infers scale as int from its default.
                    bitmap = cast(
                        pdfium.PdfBitmap,
                        page.render(scale=scale),  # type: ignore[reportUnknownMemberType, reportArgumentType]
                    )
                    try:
                        # PDFium rounds up; retain the whole page at the nearest pixel size.
                        raster = cast(NDArray[np.uint8], bitmap.to_numpy())
                        pixels = cv2.resize(raster, size, interpolation=cv2.INTER_AREA)
                        success, encoded = cv2.imencode(".png", pixels)
                        if not success:
                            raise ValueError(error)
                        pages.append(encoded.tobytes())
                    finally:
                        bitmap.close()  # type: ignore[reportUnknownMemberType]
                finally:
                    page.close()
    except pdfium.PdfiumError as exc:
        raise ValueError(error) from exc
    return tuple(pages)
