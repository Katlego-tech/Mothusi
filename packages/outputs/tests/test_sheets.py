"""Sheet geometry and the paper drawn from it agree, including vector QR modules."""

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
import segno
from pypdf import PdfReader
from pypdf.generic import DictionaryObject
from reportlab.lib.units import mm

from mothusi_contracts import Memo, MemoItem, MmRect
from mothusi_intake import load_memo
from mothusi_outputs.sheets import answer_sheets_pdf, layout_answer_sheet


@pytest.fixture
def memo() -> Memo:
    return load_memo(
        Path(__file__).resolve().parents[3] / "services/core/fixtures/gr10-maths-t1/memo.toml"
    )


def overlaps(a: MmRect, b: MmRect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def test_sheets_fixture_geometry(memo: Memo) -> None:
    manifest = layout_answer_sheet("test", memo, "L-001")
    assert manifest == layout_answer_sheet("test", memo, "L-001")
    assert manifest.name_band == MmRect(x=Decimal(14), y=Decimal(14), w=Decimal(148), h=Decimal(25))
    assert manifest.qr == MmRect(x=Decimal(166), y=Decimal(14), w=Decimal(30), h=Decimal(30))
    assert manifest.fiducials == tuple(
        MmRect(x=Decimal(x), y=Decimal(y), w=Decimal(8), h=Decimal(8))
        for x, y in ((4, 4), (198, 4), (4, 285), (198, 285))
    )
    boxes = [box for page in manifest.pages for box in page.boxes]
    assert [(box.question_id, box.marks) for box in boxes] == [
        (i.question_id, i.max_marks) for i in memo.items
    ]
    for page in manifest.pages:
        assert page.qr_payload == f"mothusi:v1:test:L-001:{page.page_no}"
        assert page.boxes[0].box.y == 48
        for index, box in enumerate(page.boxes):
            assert box.box.x == 29 and box.box.w == 167
            assert box.box.h == min(120, max(30, 12 * box.marks))
            assert box.box.y + box.box.h <= 280
            assert box.label == MmRect(x=Decimal(14), y=box.box.y, w=Decimal(15), h=box.box.h)
            obstacles = [manifest.qr, *manifest.fiducials, *(b.box for b in page.boxes[:index])]
            if page.page_no == 1:
                obstacles.append(manifest.name_band)
            assert all(not overlaps(box.box, other) for other in obstacles)
            if index:
                previous = page.boxes[index - 1].box
                assert box.box.y == previous.y + previous.h + 4


def test_sheets_height_limits_and_page_break() -> None:
    marks = (1, 10, 20, 2)
    memo = Memo(
        assessment_id="a",
        total_marks=sum(marks),
        items=tuple(
            MemoItem(question_id=str(n), max_marks=m, answer="x", level="knowledge")
            for n, m in enumerate(marks)
        ),
    )
    manifest = layout_answer_sheet("a", memo, "L")
    assert [[b.box.h for b in page.boxes] for page in manifest.pages] == [[30, 120], [120, 30]]
    assert manifest.pages[1].boxes[0].box.y == 48


def test_sheets_pdf_pages_text_geometry_and_determinism(memo: Memo) -> None:
    learners = [("Ṱhani Ḓavhana", "L-002"), ("Other Learner", "L-001")]
    pdf, manifests = answer_sheets_pdf("test", memo, learners)
    assert (pdf, manifests) == answer_sheets_pdf("test", memo, learners)
    assert [m.learner_pseudonym for m in manifests] == ["L-002", "L-001"]
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == sum(len(m.pages) for m in manifests)
    offset = 0
    for (name, _), manifest in zip(learners, manifests, strict=True):
        for sheet in manifest.pages:
            page = reader.pages[offset]
            assert float(page.mediabox.width) == pytest.approx(210 * mm)
            assert float(page.mediabox.height) == pytest.approx(297 * mm)
            text = page.extract_text()
            assert (name in text) == (sheet.page_no == 1)
            assert ("Name" in text) == (sheet.page_no == 1)
            assert all(box.question_id in text for box in sheet.boxes)
            contents = page.get_contents()
            assert contents is not None
            operations = contents.operations
            rectangles = [tuple(float(v) for v in args) for args, op in operations if op == b"re"]

            def present(
                x: float,
                y: float,
                w: float,
                h: float,
                drawn: list[tuple[float, ...]] = rectangles,
            ) -> bool:
                return any(
                    rect == pytest.approx((x * mm, (297 - y - h) * mm, w * mm, h * mm), abs=0.0001)
                    for rect in drawn
                )

            for rect in (*manifest.fiducials, *(box.box for box in sheet.boxes)):
                assert present(float(rect.x), float(rect.y), float(rect.w), float(rect.h))
            matrix = segno.make(sheet.qr_payload, error="q").matrix
            size = 30 / (len(matrix) + 4)
            qr_rectangles = [
                r
                for r in rectangles
                if r[2] == pytest.approx(size * mm, abs=0.0001)
                and r[3] == pytest.approx(size * mm, abs=0.0001)
            ]
            assert len(qr_rectangles) == sum(sum(row) for row in matrix)
            for row_no, row in enumerate(matrix):
                for col_no, dark in enumerate(row):
                    if dark:
                        assert present(
                            166 + (col_no + 2) * size, 14 + (row_no + 2) * size, size, size
                        )
            offset += 1
    resources = cast(DictionaryObject, reader.pages[0]["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"])
    assert any(
        "/FontFile2" in cast(DictionaryObject, descriptor)
        for font in fonts.values()
        if (descriptor := cast(DictionaryObject, font.get_object()).get("/FontDescriptor"))
        is not None
    )


@pytest.mark.parametrize("assessment,pseudonym", [("bad:id", "L"), ("a", "bad:id")])
def test_sheets_reject_colon(memo: Memo, assessment: str, pseudonym: str) -> None:
    with pytest.raises(ValueError, match=":"):
        layout_answer_sheet(assessment, memo, pseudonym)
    with pytest.raises(ValueError, match=":"):
        answer_sheets_pdf(assessment, memo, [("Name", pseudonym)])


@pytest.mark.parametrize("learners", [[("", "L")], [("  ", "L")], [("A", "L"), ("B", "L")]])
def test_sheets_reject_bad_roster(memo: Memo, learners: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError):
        answer_sheets_pdf("a", memo, learners)


def test_sheets_empty_roster(memo: Memo) -> None:
    pdf, manifests = answer_sheets_pdf("a", memo, [])
    assert manifests == ()
    assert len(PdfReader(BytesIO(pdf)).pages) == 0
