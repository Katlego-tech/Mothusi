"""Geometry and privacy checks use real rendered sheets, never stored image fixtures."""

from decimal import Decimal
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from mothusi_contracts import (
    Answer,
    AnswerSheetManifest,
    MmRect,
    PageImage,
    TranscribedLine,
    Transcription,
)
from mothusi_intake import load_memo
from mothusi_intake.pages import (
    blank_name_band,
    crop_answers,
    decode_qr,
    deskew,
    pages_from_upload,
    sweep_names,
    to_answer,
)
from mothusi_outputs.sheets import answer_sheets_pdf

SCALE = 150 / 25.4


def pixels(data: bytes) -> NDArray[np.uint8]:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return cast(NDArray[np.uint8], image)


def png(image: cv2.typing.MatLike) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def bounds(rect: MmRect, margin: float = 0) -> tuple[int, int, int, int]:
    return (
        max(0, int(np.floor((float(rect.x) - margin) * SCALE))),
        max(0, int(np.floor((float(rect.y) - margin) * SCALE))),
        min(1240, int(np.ceil((float(rect.x + rect.w) + margin) * SCALE))),
        min(1754, int(np.ceil((float(rect.y + rect.h) + margin) * SCALE))),
    )


@pytest.fixture(scope="module")
def sheet() -> tuple[tuple[bytes, ...], AnswerSheetManifest]:
    memo = load_memo(
        Path(__file__).resolve().parents[3] / "services/core/fixtures/gr10-maths-t1/memo.toml"
    )
    pdf, manifests = answer_sheets_pdf("geometry", memo, [("Synthetic Learner", "L-001")])
    return pages_from_upload(pdf, "sheets.pdf"), manifests[0]


def distorted(data: bytes, mode: str) -> bytes:
    image = pixels(data)
    h, w = image.shape[:2]
    # Leave the entire paper visible, as in a phone photo taken above a desk.
    image = cv2.copyMakeBorder(image, 160, 160, 160, 160, cv2.BORDER_CONSTANT, value=(225,) * 3)
    h, w = image.shape[:2]
    if mode in {"rotation", "phone"}:
        image = cv2.warpAffine(
            image,
            cv2.getRotationMatrix2D((w / 2, h / 2), 7, 1),
            (w, h),
            borderValue=(225,) * 3,
        )
    if mode in {"perspective", "phone"}:
        source = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        target = np.array(
            [[90, 20], [w - 100, 70], [w - 15, h - 20], [20, h - 95]], dtype=np.float32
        )
        image = cv2.warpPerspective(
            image,
            cv2.getPerspectiveTransform(source, target),
            (w, h),
            borderValue=(225,) * 3,
        )
    if mode in {"noise", "phone"}:
        noise = np.random.default_rng(64).normal(0, 7, image.shape)
        image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if mode == "upside_down":
        image = cv2.rotate(image, cv2.ROTATE_180)
    return png(image)


@pytest.mark.parametrize(
    "mode", ["plain", "rotation", "perspective", "noise", "phone", "upside_down"]
)
def test_generated_pages_round_trip(
    sheet: tuple[tuple[bytes, ...], AnswerSheetManifest], mode: str
) -> None:
    images, manifest = sheet
    for data, page in zip(images, manifest.pages, strict=True):
        photo = distorted(data, mode)
        payload = decode_qr(photo)
        assert payload is not None
        assert (payload.assessment_id, payload.learner_pseudonym, payload.page_no) == (
            manifest.assessment_id,
            manifest.learner_pseudonym,
            page.page_no,
        )
        restored = deskew(photo)
        assert decode_qr(restored) == payload
        actual = pixels(restored)
        assert actual.shape == (1754, 1240, 3)
        # Each known border must be present within 2 mm, including both ends.
        tolerance = round(2 * SCALE)
        gray = cv2.cvtColor(actual, cv2.COLOR_BGR2GRAY)
        for box in page.boxes:
            x0, y0, x1, y1 = bounds(box.box)
            for y in (y0, y1 - 1):
                assert (
                    np.mean(
                        np.min(gray[y - tolerance : y + tolerance + 1, x0 + 20 : x1 - 20], axis=0)
                        < 190
                    )
                    > 0.98
                )
            for x in (x0, x1 - 1):
                assert (
                    np.mean(
                        np.min(gray[y0 + 20 : y1 - 20, x - tolerance : x + tolerance + 1], axis=1)
                        < 190
                    )
                    > 0.98
                )
        if page.page_no == 1:
            restored = blank_name_band(restored, manifest.name_band)
            x0, y0, x1, y1 = bounds(manifest.name_band, 2)
            assert np.all(pixels(restored)[y0:y1, x0:x1] == 255)
        identity = PageImage(
            assessment_id=manifest.assessment_id,
            learner_pseudonym=manifest.learner_pseudonym,
            page_no=page.page_no,
            source_key="sheets/page.png",
        )
        crops = crop_answers(identity, restored, manifest)
        assert [c.question_id for c in crops] == [b.question_id for b in page.boxes]
        for crop, box in zip(crops, page.boxes, strict=True):
            x0, y0, x1, y1 = bounds(box.box)
            assert crop.learner_pseudonym == identity.learner_pseudonym
            assert crop.page_no == page.page_no
            np.testing.assert_array_equal(pixels(crop.png), pixels(restored)[y0:y1, x0:x1])


def test_blank_band_uses_supplied_rectangle_and_clips_margin() -> None:
    image = np.zeros((1754, 1240, 3), np.uint8)
    band = MmRect(x=Decimal(0), y=Decimal(2), w=Decimal(40), h=Decimal(15))
    result = pixels(blank_name_band(png(image), band))
    x0, y0, x1, y1 = bounds(band, 2)
    assert np.all(result[y0:y1, x0:x1] == 255)
    assert np.all(result[y1:, :] == 0)
    assert np.all(result[:y1, x1:] == 0)
    assert np.all(image == 0)


@pytest.mark.parametrize("corner", range(4))
def test_missing_corner_is_refused(
    sheet: tuple[tuple[bytes, ...], AnswerSheetManifest], corner: int
) -> None:
    images, manifest = sheet
    image = pixels(images[0])
    x0, y0, x1, y1 = bounds(manifest.fiducials[corner], 2)
    image[y0:y1, x0:x1] = 255
    with pytest.raises(
        ValueError, match="retake the photo: all four corner squares must be visible"
    ):
        deskew(png(image))


def test_no_qr_returns_none(sheet: tuple[tuple[bytes, ...], AnswerSheetManifest]) -> None:
    images, manifest = sheet
    image = pixels(images[0])
    x0, y0, x1, y1 = bounds(manifest.qr, 2)
    image[y0:y1, x0:x1] = 255
    assert decode_qr(png(image)) is None
    assert decode_qr(png(np.full_like(image, 255))) is None


def test_payload_the_contour_detector_misses_is_still_read() -> None:
    # This ID's QR pattern defeated cv2.QRCodeDetector alone on a clean render.
    memo = load_memo(
        Path(__file__).resolve().parents[3] / "services/core/fixtures/gr10-maths-t1/memo.toml"
    )
    aid = "mark-my-grade-10-term-1-maths-te-e9d1b92a"
    pdf, _ = answer_sheets_pdf(aid, memo, [("A B", "L-001"), ("C D", "L-002"), ("E F", "L-003")])
    payload = decode_qr(pages_from_upload(pdf, "sheets.pdf")[0])
    assert payload is not None
    assert (payload.assessment_id, payload.learner_pseudonym, payload.page_no) == (aid, "L-001", 1)


def test_decoder_error_falls_through_to_the_next_attempt(
    sheet: tuple[tuple[bytes, ...], AnswerSheetManifest], monkeypatch: pytest.MonkeyPatch
) -> None:
    # OpenCV can raise from detectAndDecode instead of returning "" (qrcode.cpp, -2);
    # the error is a miss and the next detector still runs.
    images, _ = sheet
    real = cv2.QRCodeDetector
    raised: list[bool] = []

    class Flaky:
        def detectAndDecode(  # noqa: N802
            self, image: NDArray[np.uint8]
        ) -> tuple[str, object, object]:
            if not raised:
                raised.append(True)
                raise cv2.error("decode failed")
            payload, points, straight = real().detectAndDecode(image)
            return payload, points, straight

    monkeypatch.setattr(cv2, "QRCodeDetector", Flaky)
    monkeypatch.setattr(cv2, "QRCodeDetectorAruco", Flaky)
    payload = decode_qr(images[0])
    assert raised == [True]
    assert payload is not None
    assert payload.page_no == 1


@pytest.mark.parametrize(
    "payload",
    [
        "https://example.org",
        "mothusi:v2:a:L:1",
        "mothusi:v1:a:L:0",
        "mothusi:v1:a:L:-1",
        "mothusi:v1::L:1",
        "mothusi:v1:a:L:1:2",
    ],
)
def test_non_mothusi_or_malformed_qr_returns_none(payload: str) -> None:
    from io import BytesIO

    import segno

    stream = BytesIO()
    segno.make(payload, micro=False).save(stream, kind="png", scale=8, border=4)
    assert decode_qr(stream.getvalue()) is None


@pytest.mark.parametrize(
    "field,value", [("assessment_id", "foreign"), ("learner_pseudonym", "L-999"), ("page_no", 999)]
)
def test_foreign_identity_or_unknown_page_is_refused(
    sheet: tuple[tuple[bytes, ...], AnswerSheetManifest], field: str, value: str | int
) -> None:
    images, manifest = sheet
    identity = dict(
        assessment_id="geometry", learner_pseudonym="L-001", page_no=1, source_key="foreign.png"
    )
    identity[field] = value
    with pytest.raises(ValueError, match=r"foreign\.png"):
        crop_answers(PageImage.model_validate(identity), images[0], manifest)


def test_to_answer_keeps_crop_order_and_identical_uncertain_lines() -> None:
    def transcription(*lines: tuple[str, bool]) -> Transcription:
        return Transcription(lines=tuple(TranscribedLine(text=t, uncertain=u) for t, u in lines))

    result = to_answer(
        "Q1",
        [
            transcription(("x = 2", True), ("x = 2", False)),
            transcription(("x = 2", True), ("so 2", False)),
        ],
    )
    assert result == Answer(
        question_id="Q1", text="x = 2\nx = 2\nx = 2\nso 2", uncertain_lines=(0, 2)
    )
    assert to_answer("Q1", []).text == ""


def test_name_sweep_is_whole_word_case_insensitive_simultaneous_and_preserves_flags() -> None:
    answer = Answer(
        question_id="Q", text="ANN and Ann Lee; Joanne\nann + BOB", uncertain_lines=(1,)
    )
    result, count = sweep_names(answer, {"Ann": "BOB", "Ann Lee": "L-002", "Bob": "L-003"})
    assert result.text == "BOB and L-002; Joanne\nBOB + L-003"
    assert result.uncertain_lines == (1,)
    assert count == 4
    assert answer.text.startswith("ANN")
    assert sweep_names(answer, {}) == (answer, 0)
    unreadable = Answer(question_id="Q", text="", unreadable=True)
    assert sweep_names(unreadable, {"Ann": "L-001"}) == (unreadable, 0)


def test_foreign_qr_identity_is_refused_against_expected_manifest(
    sheet: tuple[tuple[bytes, ...], AnswerSheetManifest],
) -> None:
    _, expected = sheet
    memo = load_memo(
        Path(__file__).resolve().parents[3] / "services/core/fixtures/gr10-maths-t1/memo.toml"
    )
    pdf, _ = answer_sheets_pdf("other-assessment", memo, [("Synthetic Learner", "L-001")])
    image = pages_from_upload(pdf, "foreign.pdf")[0]
    payload = decode_qr(image)
    assert payload is not None
    identity = PageImage(
        assessment_id=payload.assessment_id,
        learner_pseudonym=payload.learner_pseudonym,
        page_no=payload.page_no,
        source_key="foreign.pdf",
    )
    with pytest.raises(ValueError, match=r"foreign\.pdf: QR code for another assessment"):
        crop_answers(identity, image, expected)


@pytest.mark.parametrize("data", [b"", b"broken image"])
def test_invalid_image_is_unreadable_and_cannot_be_deskewed(data: bytes) -> None:
    assert decode_qr(data) is None
    with pytest.raises(
        ValueError, match="retake the photo: all four corner squares must be visible"
    ):
        deskew(data)


@pytest.mark.parametrize(
    "name,text", [("Straße", "Straße"), ("İpek", "İPEK"), ("Ipek", "\u0131pek")]
)
def test_name_sweep_handles_unicode_without_losing_names(name: str, text: str) -> None:
    answer, count = sweep_names(Answer(question_id="Q", text=text), {name: "L-001"})
    assert answer.text == "L-001"
    assert count == 1


def test_multiline_reader_text_retains_uncertainty_for_each_line() -> None:
    result = to_answer("Q", [Transcription(lines=(TranscribedLine(text="a\nb", uncertain=True),))])
    assert result.text == "a\nb"
    assert result.uncertain_lines == (0, 1)


@pytest.mark.parametrize("uncertain", [False, True])
def test_all_blank_transcription_collapses_uncertainty_to_one_line(uncertain: bool) -> None:
    transcription = Transcription(
        lines=(
            TranscribedLine(text="", uncertain=False),
            TranscribedLine(text="", uncertain=uncertain),
        )
    )
    result = to_answer("Q", [transcription])
    assert result.text == ""
    assert result.uncertain_lines == ((0,) if uncertain else ())
