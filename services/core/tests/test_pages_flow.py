"""Pages mode end to end through the API, up to and through the reading check.

Sheets are printed by the API, rendered to page images (standing in for phone photos), uploaded,
read by a fake vision model, and the teacher's reading answers move the run on to memo_check.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Contract, Transcription
from mothusi_core.agents.reader import CropRef, read_script
from mothusi_core.graph.planner import Planner, apply_readings
from mothusi_core.main import Runs, create_app
from mothusi_core.pages import crop_link
from mothusi_intake import load_memo
from mothusi_intake.pages import pages_from_upload
from mothusi_llm import ProviderError, Usage

from .conftest import ROOT

sys.path.insert(0, str(ROOT / "services/core/fixtures"))
from scripted_model import ScriptedModel

FIXTURE = ROOT / "services/core/fixtures/gr10-maths-t1"
CLASS = "learner\nZanele Mokoena\nAarav Pillay\nThabo Dlamini\n"


class FakeReader:
    """Reads every crop as two lines, the second unclear; mentions a learner by name once."""

    name = "fake-vl"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_images: list[int] = []

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.calls += 1
        self.saw_images.append(len(images))
        first = "Thabo Dlamini helped: x = 3" if self.calls == 1 else "x = 3"
        reply = output.model_validate(
            {"lines": [{"text": first, "uncertain": False}, {"text": "y = 2", "uncertain": True}]}
        )
        return reply, Usage(model=self.name, input_tokens=1, output_tokens=1, cost_zar=Decimal(0))


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def client(tmp_path: Path, reader: FakeReader) -> Iterator[TestClient]:
    planner = Planner(
        primary=ScriptedModel(),
        fallback=None,
        roles={"reader": reader},
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as c:
        yield c


def runs(client: TestClient) -> Runs:
    return cast(Runs, client.app.state.runs)  # type: ignore[attr-defined]


def events(client: TestClient, aid: str) -> list[dict[str, Any]]:
    text = (runs(client).planner.data_dir / aid / "events.jsonl").read_text()
    return [json.loads(line) for line in text[: text.rfind("\n") + 1].splitlines()]


type Event = dict[str, Any]


def wait_for(client: TestClient, aid: str, test: Callable[[Event], bool]) -> Event:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for event in events(client, aid):
            if test(event):
                return event
        time.sleep(0.05)
    tail = [(e["kind"], e["summary"]) for e in events(client, aid)[-6:]]
    raise AssertionError(f"timed out in {aid}; last events: {tail}")


def create_pages(client: TestClient) -> str:
    response = client.post(
        "/assessments",
        data={
            "goal": "Mark my Grade 10 Term 1 Maths test",
            "pack_id": "dbe-caps/mathematics-gr10",
            "poa_task": "Test",
            "budget_zar": "40.00",
        },
        files={
            "memo": ("memo.toml", (FIXTURE / "memo.toml").read_bytes()),
            "class_list": ("class.csv", CLASS.encode()),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "pages"
    return response.json()["assessment_id"]


def upload_sheets(client: TestClient, aid: str) -> int:
    pdf = client.get(f"/assessments/{aid}/answer-sheets.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    pages = pages_from_upload(pdf.content, "sheets.pdf")
    names = [f"photo-{n:02d}.png" for n in range(len(pages))]
    urls = client.post(
        f"/assessments/{aid}/uploads",
        json={"files": [{"name": n, "content_type": "image/png", "size": 1} for n in names]},
    ).json()["uploads"]
    for entry, page in zip(urls, pages, strict=True):
        put = client.put(entry["url"], content=page, headers=entry["headers"])
        assert put.status_code == 204
    return len(pages)


def test_pages_mode_reads_asks_and_moves_on(client: TestClient, reader: FakeReader) -> None:
    aid = create_pages(client)
    assert client.post(f"/assessments/{aid}/pages/ready").status_code == 409  # nothing uploaded
    upload_sheets(client, aid)
    assert client.post(f"/assessments/{aid}/pages/ready").status_code == 202
    asked = wait_for(
        client,
        aid,
        lambda e: e["kind"] == "needs_teacher" and e["payload"].get("topic") == "reading",
    )
    items = asked["payload"]["items"]
    refused = [e["payload"]["detail"] for e in events(client, aid) if e["kind"] == "check"]
    assert refused == []  # every printed page is read
    assert reader.calls == len(items) and set(reader.saw_images) == {1}  # one image per crop
    assert {i["line_no"] for i in items} == {1} and {i["line"] for i in items} == {"y = 2"}
    log = json.dumps(events(client, aid))
    assert "Thabo Dlamini" not in log and "Zanele" not in log  # names never reach the log
    crop = client.get(items[0]["crop_url"])
    assert crop.status_code == 200 and crop.content.startswith(b"\x89PNG")
    while runs(client).busy(aid):
        time.sleep(0.05)
    answers = [
        {
            **{k: i[k] for k in ("script_id", "question_id", "line_no")},
            "kind": "reading",
            "action": "confirm",
        }
        for i in items
    ]
    assert (
        client.post(f"/assessments/{aid}/reading", json={"answers": answers[1:]}).status_code == 422
    )
    assert client.post(f"/assessments/{aid}/reading", json={"answers": answers}).status_code == 202
    wait_for(
        client,
        aid,
        lambda e: (
            e["kind"] == "state"
            and e["payload"].get("from") == "reading_check"
            and e["payload"].get("to") == "memo_check"
        ),
    )


def test_a_page_without_a_mothusi_qr_is_refused_by_name(client: TestClient) -> None:
    aid = create_pages(client)
    sheets = client.get(f"/assessments/{aid}/answer-sheets.pdf").content
    first = pages_from_upload(sheets, "x.pdf")[0]
    from io import BytesIO

    from PIL import Image, ImageDraw

    image = Image.open(BytesIO(first)).convert("L")
    # Wipe the QR only (166-196 x 14-44 mm at 150 dpi); the corner squares stay.
    ImageDraw.Draw(image).rectangle((980, 83, 1158, 260), fill=255)
    out = BytesIO()
    image.save(out, format="PNG")
    client.put(
        f"/assessments/{aid}/uploads/no-qr.png",
        content=out.getvalue(),
        headers={"Content-Type": "image/png"},
    )
    held = client.post(f"/assessments/{aid}/pages/ready", json={"accept_gaps": True})
    assert held.status_code == 409  # nothing readable: not even the teacher can start it
    assert held.json()["can_accept"] is False
    assert held.json()["refused"] == [
        "no-qr.png: no Mothusi QR code (only Mothusi answer sheets can be read)"
    ]
    # Still waiting: the teacher removes the refused photo and uploads the retake.
    assert client.delete(f"/assessments/{aid}/uploads/no-qr.png").status_code == 204
    assert client.delete(f"/assessments/{aid}/uploads/no-qr.png").status_code == 404
    assert client.delete(f"/assessments/{aid}/uploads/..%2Fmanifests.json").status_code in {
        404,
        422,
    }
    assert upload_sheets(client, aid) > 0
    assert client.post(f"/assessments/{aid}/pages/ready").status_code == 202
    assert client.delete(f"/assessments/{aid}/uploads/photo-00.png").status_code == 409


def test_the_reader_marks_a_box_unreadable_after_two_failures(tmp_path: Path) -> None:
    import asyncio

    from mothusi_contracts import AgentRole, Brief

    class Down:
        name = "down"

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise ProviderError("timed out", retryable=True)

    crop = tmp_path / "1.1.png"
    crop.write_bytes(b"\x89PNG\r\n\x1a\n")
    brief = Brief(
        brief_id="a/read/L-001",
        assessment_id="a",
        agent=AgentRole.INTAKE,
        task="read",
        inputs={
            "pseudonym": "L-001",
            "crops": [CropRef(question_id="1.1", path=str(crop)).model_dump()],
        },
    )
    result = asyncio.run(read_script(brief, Down()))  # pyright: ignore[reportArgumentType]
    answer = result.outputs["script"]["answers"][0]
    assert answer["unreadable"] is True and answer["text"] == ""


def test_apply_readings_corrects_one_of_two_identical_lines() -> None:
    from mothusi_contracts import Answer, Script

    script = Script(
        script_id="L-001",
        learner_pseudonym="L-001",
        answers=(Answer(question_id="2", text="x = 1\nx = 1", uncertain_lines=(0, 1)),),
    )
    items = [
        {"script_id": "L-001", "question_id": "2", "line_no": 0, "line": "x = 1"},
        {"script_id": "L-001", "question_id": "2", "line_no": 1, "line": "x = 1"},
    ]
    answers = [
        {
            "kind": "reading",
            "script_id": "L-001",
            "question_id": "2",
            "line_no": 0,
            "action": "confirm",
        },
        {
            "kind": "reading",
            "script_id": "L-001",
            "question_id": "2",
            "line_no": 1,
            "action": "correct",
            "text": "x = 7",
        },
    ]
    (fixed,) = apply_readings([script], items, answers, {})
    assert fixed.answers[0].text == "x = 1\nx = 7" and fixed.answers[0].uncertain_lines == ()


def test_crop_links_expire_and_cannot_be_forged(client: TestClient) -> None:
    link = crop_link("a", "L-001", "1.1", now=time.time() - 3600)  # expired an hour ago
    assert client.get(link).status_code == 404
    assert client.get("/c/a/L-001/1.1?exp=9999999999&sig=00").status_code == 404


def test_transcription_contract_is_what_the_reader_returns() -> None:
    assert Transcription.model_validate({"lines": []}).lines == ()


def test_a_correction_naming_a_learner_is_swept() -> None:
    from mothusi_contracts import Answer, Script

    script = Script(
        script_id="L-002",
        learner_pseudonym="L-002",
        answers=(Answer(question_id="1(a)", text="", unreadable=True),),
    )
    items = [{"script_id": "L-002", "question_id": "1(a)", "line_no": None, "line": ""}]
    answers = [
        {"kind": "reading", "script_id": "L-002", "question_id": "1(a)", "line_no": None}
        | {"action": "correct", "text": "Zanele Mokoena showed me x = 3"},
    ]
    (fixed,) = apply_readings([script], items, answers, {"Zanele Mokoena": "L-001"})
    assert fixed.answers[0].text == "L-001 showed me x = 3"


def test_a_missing_page_is_asked_about_not_marked_zero(
    client: TestClient, reader: FakeReader
) -> None:
    aid = create_pages(client)
    sheets = client.get(f"/assessments/{aid}/answer-sheets.pdf").content
    pages = pages_from_upload(sheets, "s.pdf")
    first_only = pages[0]  # one learner's first page: their later pages never arrive
    client.put(
        f"/assessments/{aid}/uploads/p1.png",
        content=first_only,
        headers={"Content-Type": "image/png"},
    )
    held = client.post(f"/assessments/{aid}/pages/ready")
    assert held.status_code == 409  # the gaps are named before any page is read
    per_learner = len(pages) // 3
    assert held.json()["refused"] == []
    assert held.json()["can_accept"] is True
    assert held.json()["missing"] == [
        *([{"script_id": "L-001", "pages": list(range(2, per_learner + 1))}] * (per_learner > 1)),
        {"script_id": "L-002", "pages": list(range(1, per_learner + 1))},
        {"script_id": "L-003", "pages": list(range(1, per_learner + 1))},
    ]
    accepted = client.post(f"/assessments/{aid}/pages/ready", json={"accept_gaps": True})
    assert accepted.status_code == 202

    def reading_asked(e: Event) -> bool:
        return e["kind"] == "needs_teacher" and e["payload"].get("topic") == "reading"

    items = wait_for(client, aid, reading_asked)["payload"]["items"]
    missing = [i for i in items if i["page_missing"]]
    # Learners with no pages at all stay on the roster: every box is asked about.
    for absent in ("L-002", "L-003"):
        theirs = [i for i in missing if i["script_id"] == absent]
        assert len(theirs) == len(load_memo(FIXTURE / "memo.toml").items)
    if len(pages) > 3:  # the fixture memo needs more than one page per learner
        assert missing and all(i["crop_url"] is None and i["line_no"] is None for i in missing)
    while runs(client).busy(aid):
        time.sleep(0.05)
    fresh = client.get(f"/assessments/{aid}/reading").json()["items"]
    assert len(fresh) == len(items)
    with_crop = [i for i in fresh if i["crop_url"]]
    assert with_crop and client.get(with_crop[0]["crop_url"]).status_code == 200


def test_two_ready_requests_start_one_run(client: TestClient) -> None:
    aid = create_pages(client)
    upload_sheets(client, aid)

    async def both() -> list[int]:
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as http:
            replies = await asyncio.gather(
                *(http.post(f"/assessments/{aid}/pages/ready") for _ in range(2))
            )
        return sorted(r.status_code for r in replies)

    assert client.portal is not None
    assert client.portal.call(both) == [202, 409]  # the second finds it no longer waiting


def test_a_reading_answer_before_the_run_starts_is_409(client: TestClient) -> None:
    aid = create_pages(client)
    answer = {"kind": "reading", "script_id": "L-001", "question_id": "1", "line_no": 0}
    body = {"answers": [answer | {"action": "confirm"}]}
    assert client.post(f"/assessments/{aid}/reading", json=body).status_code == 409
    assert client.get(f"/assessments/{aid}/reading").status_code == 409
