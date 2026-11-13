"""A typed memo PDF or Word file drafted into rows the teacher confirms (intake.md §12)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator, Sequence
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from mothusi_contracts import Contract
from mothusi_core.agents.setter import DraftItem, MemoDraftError, MemoDraftReply, draft_memo
from mothusi_core.graph.planner import Planner
from mothusi_core.main import create_app
from mothusi_intake import load_memo
from mothusi_llm import ProviderError, Usage

from .conftest import ROOT

LEVELS = ("knowledge", "routine_procedures", "complex_procedures", "problem_solving")
FIXTURE = ROOT / "services/core/fixtures/gr10-maths-t1"
GOOD: dict[str, Any] = {
    "total_marks": 8,
    "items": [
        {
            "question_id": "1.1",
            "question": "Solve x^2 - x - 6 = 0",
            "max_marks": 5,
            "answer": "x = 3 or x = -2",
            "level": "routine_procedures",
        },
        {
            "question_id": "1.2",
            "question": "Expand (x+1)^2",
            "max_marks": 3,
            "answer": "x^2 + 2x + 1",
            "level": "knowledge",
        },
    ],
}


class Drafter:
    """Replies from a list; a ProviderError in the list is raised instead."""

    name = "fake-drafter"

    def __init__(self, replies: Sequence[dict[str, Any] | ProviderError]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, ProviderError):
            raise reply
        usage = Usage(model=self.name, input_tokens=10, output_tokens=10, cost_zar=Decimal("0.01"))
        return output.model_validate(reply), usage


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_a_clean_memo_drafts_with_no_problems() -> None:
    result = run(draft_memo("memo text", Drafter([GOOD]), LEVELS))
    assert result.problems == ()
    assert [i.question_id for i in result.items] == ["1.1", "1.2"]
    assert result.total_marks == 8 and result.cost_zar == Decimal("0.01")


def test_marks_that_do_not_add_up_and_unknown_levels_are_named_for_the_teacher() -> None:
    reply = {
        "total_marks": 10,
        "items": [
            {**GOOD["items"][0], "level": "hard"},
            {**GOOD["items"][1], "level": ""},
            {"question_id": "2", "max_marks": 0, "answer": ""},
        ],
    }
    result = run(draft_memo("memo text", Drafter([reply]), LEVELS))
    assert [i.level for i in result.items] == ["", "", ""]  # never a level outside the pack's list
    assert result.problems == (
        "1.1: level 'hard' is not the pack's",
        "1.2: the memo gives no level; choose one",
        "2: no marks found; give its marks",
        "2: no answer found; type the memo's answer",
        "2: the memo gives no level; choose one",
        "memo items add up to 8, not the 10 the memo states",
    )


def test_a_missing_total_is_named_with_the_sum() -> None:
    result = run(draft_memo("memo text", Drafter([{**GOOD, "total_marks": None}]), LEVELS))
    assert result.problems == ("the memo's total was not found; the rows add up to 8",)


def test_a_rejected_reply_is_retried_once_naming_the_error_then_fails() -> None:
    bad = ProviderError("items: field required", retryable=True)
    drafter = Drafter([bad, GOOD])
    assert run(draft_memo("memo text", drafter, LEVELS)).problems == ()
    assert "Your last reply was rejected: items: field required" in drafter.prompts[1]
    with pytest.raises(MemoDraftError, match="after two tries"):
        run(draft_memo("memo text", Drafter([bad, bad]), LEVELS))
    with pytest.raises(MemoDraftError, match="could not be read"):
        run(draft_memo("memo text", Drafter([ProviderError("no key", retryable=False)]), LEVELS))


def test_the_prompt_copies_the_memo_and_lists_the_allowed_levels() -> None:
    drafter = Drafter([GOOD])
    run(draft_memo("1.1 Solve x^2 - x - 6 = 0 (5)", drafter, LEVELS))
    assert "1.1 Solve x^2 - x - 6 = 0 (5)" in drafter.prompts[0]
    assert "Allowed levels: knowledge, routine_procedures" in drafter.prompts[0]


def test_a_memo_too_long_for_one_draft_is_refused_not_cut() -> None:
    from mothusi_core.agents.setter import MEMO_DRAFT_LIMIT

    drafter = Drafter([GOOD])
    with pytest.raises(MemoDraftError, match="over the 60,000 one draft can read"):
        run(draft_memo("x" * (MEMO_DRAFT_LIMIT + 1), drafter, LEVELS))
    assert drafter.prompts == []  # nothing was sent


def test_draft_item_accepts_empty_fields_for_the_teacher_to_fill() -> None:
    assert DraftItem(question_id="3").max_marks == 0
    assert MemoDraftReply.model_validate({"items": [{"question_id": "3"}]}).total_marks is None


# ------------------------------------------------------------------ the API


def memo_pdf(lines: Sequence[str]) -> bytes:
    out = BytesIO()
    canvas = Canvas(out, pagesize=A4)
    for n, line in enumerate(lines):
        canvas.drawString(72, 780 - 18 * n, line)
    canvas.save()
    return out.getvalue()


@pytest.fixture
def drafter() -> Drafter:
    return Drafter([GOOD])


@pytest.fixture
def client(tmp_path: Path, drafter: Drafter) -> Iterator[TestClient]:
    import sys

    sys.path.insert(0, str(FIXTURE.parent))
    from scripted_model import ScriptedModel

    planner = Planner(
        primary=ScriptedModel(),
        fallback=None,
        roles={"text": drafter},
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as c:
        yield c


def poll(client: TestClient, draft_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        view = client.get(f"/memo-drafts/{draft_id}").json()
        if view["state"] != "drafting":
            return view
        time.sleep(0.02)
    raise AssertionError("the draft never finished")


def test_a_typed_memo_pdf_is_drafted_and_polled(client: TestClient, drafter: Drafter) -> None:
    pdf = memo_pdf(["Grade 10 Test memo (8 marks)", "1.1 x = 3 or x = -2 (5)", "1.2 x^2+2x+1 (3)"])
    started = client.post(
        "/memo-drafts",
        data={"pack_id": "dbe-caps/mathematics-gr10"},
        files={"memo": ("memo.pdf", pdf, "application/pdf")},
    )
    assert started.status_code == 202, started.text
    view = poll(client, started.json()["draft_id"])
    assert view["state"] == "ready" and view["problems"] == []
    assert view["source_name"] == "memo.pdf" and view["total_marks"] == 8
    assert view["levels"] == list(LEVELS)
    assert "1.1 x = 3 or x = -2 (5)" in drafter.prompts[0]  # the PDF's text reached the Setter
    assert client.get("/memo-drafts/nope").status_code == 404


def test_a_scan_and_an_unknown_pack_are_refused(client: TestClient) -> None:
    blank = memo_pdf([])
    scan = client.post(
        "/memo-drafts",
        data={"pack_id": "dbe-caps/mathematics-gr10"},
        files={"memo": ("scan.pdf", blank, "application/pdf")},
    )
    assert scan.status_code == 422
    assert "has no text to read (it is a scan)" in scan.json()["errors"][0]["message"]
    pack = client.post(
        "/memo-drafts",
        data={"pack_id": "nope"},
        files={"memo": ("memo.pdf", memo_pdf(["x" * 40]), "application/pdf")},
    )
    assert pack.status_code == 422 and pack.json()["errors"][0]["field"] == "pack_id"


def test_a_confirmed_draft_creates_the_assessment_as_the_toml_would(client: TestClient) -> None:
    memo = load_memo(FIXTURE / "memo.toml")
    common = {
        "goal": "Mark my Grade 10 Term 1 Maths test",
        "pack_id": "dbe-caps/mathematics-gr10",
        "poa_task": "Test",
        "budget_zar": "40.00",
    }
    created = client.post(
        "/assessments",
        data=common,
        files={
            "memo": ("memo.json", memo.model_dump_json().encode(), "application/json"),
            "class_list": ("class.csv", b"learner\nZanele Mokoena\nThabo Dlamini\n"),
        },
    )
    assert created.status_code == 201, created.text
    bad = memo.model_dump(mode="json") | {"total_marks": 51}
    import json

    refused = client.post(
        "/assessments",
        data=common,
        files={
            "memo": ("memo.json", json.dumps(bad).encode(), "application/json"),
            "class_list": ("class.csv", b"learner\nZanele Mokoena\n"),
        },
    )
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["field"] == "memo"
