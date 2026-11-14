"""The API of docs/architecture/design/console-ui.md §6 over the Planner, on the scripted provider.

The golden path over HTTP (create, the memo question, approval, the SSE log to `delivered`) and
the refusals the console relies on: 422 with intake's own sentence, 409 when nothing is pending.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Contract
from mothusi_core.graph.planner import Planner
from mothusi_core.main import Runs, create_app
from mothusi_core.smoke import FIXTURE
from mothusi_llm import Usage

from .conftest import ROOT

sys.path.insert(0, str(ROOT / "services/core/fixtures"))
from scripted_model import ScriptedModel


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    planner = Planner(
        primary=ScriptedModel(),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner)) as c:
        yield c


def create(client: TestClient, tmp_path: Path, **overrides: Any) -> Any:
    files = {
        "memo": ("memo.toml", (FIXTURE / "memo.toml").read_bytes()),
        "scripts": ("scripts.csv", (FIXTURE / "scripts.csv").read_bytes()),
        "atp": ("atp-weeks.toml", (FIXTURE / "atp-weeks.toml").read_bytes()),
    }
    files.update(overrides.pop("files", {}))
    data = {
        "goal": "Mark my Grade 10 Term 1 Maths test and get it ready for my HOD.",
        "pack_id": "dbe-caps/mathematics-gr10",
        "poa_task": "Test",
        "budget_zar": "40.00",
        **overrides,
    }
    return client.post("/assessments", data=data, files=files)


def events_of(client: TestClient, assessment_id: str) -> list[dict[str, Any]]:
    runs = cast(Runs, client.app.state.runs)  # type: ignore[attr-defined]
    path = runs.planner.data_dir / assessment_id / "events.jsonl"
    text = path.read_text(encoding="utf-8")
    complete = text[: text.rfind("\n") + 1]  # the run may be mid-append: whole lines only
    return [json.loads(line) for line in complete.splitlines()]


def wait_for(client: TestClient, assessment_id: str, kind: str, **match: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for event in events_of(client, assessment_id):
            payload = event["payload"]
            matched = event["kind"] == kind and all(payload.get(k) == v for k, v in match.items())
            if matched and not cast(Runs, client.app.state.runs).busy(assessment_id):  # type: ignore[attr-defined]
                return event
        time.sleep(0.05)
    raise AssertionError(f"no {kind} {match} in {assessment_id}")


def test_packs_lists_every_installed_pack_with_its_poa(client: TestClient) -> None:
    packs = {p["pack_id"]: p for p in client.get("/packs").json()}
    assert {"dbe-caps/mathematics-gr10", "dbe-caps/accounting-gr10"} <= set(packs)
    assert "Test" in [t["name"] for t in packs["dbe-caps/mathematics-gr10"]["tasks"]]


def test_the_golden_path_over_http(client: TestClient, tmp_path: Path) -> None:
    created = create(client, tmp_path)
    assert created.status_code == 201, created.text
    aid = created.json()["assessment_id"]
    first = events_of(client, aid)[0]
    assert first["seq"] == 0 and first["kind"] == "created"
    assert len(first["payload"]["script_ids"]) == 32

    question = wait_for(client, aid, "needs_teacher", topic="memo")
    assert question["payload"]["question_id"] == "2.3"
    assert client.get(f"/assessments/{aid}/bundle").status_code == 409
    assert (
        client.post(f"/assessments/{aid}/approval", json={"decision": "approve"}).status_code == 409
    )
    answer = {"decision": "use_proposed", "question_id": "2.3"}
    assert client.post(f"/assessments/{aid}/memo", json=answer).status_code == 202

    wait_for(client, aid, "state", to="awaiting_approval")
    bundle = client.get(f"/assessments/{aid}/bundle").json()
    assert bundle["summary"]["scripts"] == 32 and bundle["summary"]["re_marked"] == 1
    assert bundle["mark_sheet_csv"].startswith("learner,raw,out_of,sba_points,year_points")
    assert len(bundle["learner_notes"]) == 32
    assert all(not n["learner"].startswith("L-") for n in bundle["learner_notes"])  # names here
    reject = client.post(f"/assessments/{aid}/approval", json={"decision": "reject"})
    assert reject.status_code == 422 and reject.json()["errors"][0]["field"] == "note"
    assert (
        client.post(f"/assessments/{aid}/approval", json={"decision": "approve"}).status_code == 202
    )

    wait_for(client, aid, "delivered")
    with client.stream("GET", f"/assessments/{aid}/events", headers={"Last-Event-ID": "5"}) as r:
        frames = [line for line in r.iter_lines() if line.startswith("id: ")]
    seqs = [int(f[4:]) for f in frames]
    assert seqs[0] == 6 and seqs == list(range(6, len(events_of(client, aid))))


def test_a_scripts_file_intake_refuses_is_a_422_with_its_sentence(
    client: TestClient, tmp_path: Path
) -> None:
    bad = b"learner,question_id,answer\n,1.1,x = 3\n"
    response = create(client, tmp_path, files={"scripts": ("scripts.csv", bad)})
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors[0]["field"] == "scripts" and errors[0]["message"]


def test_an_unknown_pack_task_and_budget_are_each_named(client: TestClient, tmp_path: Path) -> None:
    response = create(client, tmp_path, pack_id="dbe-caps/../secret", budget_zar="-1")
    fields = {e["field"] for e in response.json()["errors"]}
    assert response.status_code == 422 and fields == {"pack_id", "budget_zar"}
    response = create(client, tmp_path, poa_task="Essay")
    assert {e["field"] for e in response.json()["errors"]} == {"poa_task"}


def test_an_unknown_assessment_is_a_404(client: TestClient) -> None:
    assert client.get("/assessments/nope/events").status_code == 404
    assert (
        client.post(
            "/assessments/nope/memo", json={"decision": "keep_memo", "question_id": "1"}
        ).status_code
        == 404
    )


def test_a_memo_that_does_not_fit_the_task_is_a_422(client: TestClient, tmp_path: Path) -> None:
    response = create(client, tmp_path, poa_task="Mid-year examination")
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "field": "poa_task",
            "message": "the memo totals 50 marks but Mid-year examination is out of 100",
        }
    ]


def test_two_approvals_at_once_resume_the_run_once(tmp_path: Path) -> None:
    """Codex P1: answers are validated and launched under one lock per assessment."""
    import asyncio

    import httpx

    from mothusi_core.main import Runs

    planner = Planner(
        primary=ScriptedModel(),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    app = create_app(planner)

    async def run() -> list[int]:
        app.state.runs = Runs(planner)
        r = await client_create(app)
        aid = r.json()["assessment_id"]
        runs: Runs = app.state.runs
        while runs.busy(aid) or not (await planner.snapshot(aid)).get("question"):
            await asyncio.sleep(0.02)
        await planner.resume(aid, {"decision": "use_proposed", "question_id": "2.3"})
        assert (await planner.snapshot(aid))["state"] == "awaiting_approval"
        slow = planner.snapshot

        async def delayed(assessment_id: str) -> Any:
            await asyncio.sleep(0.2)  # widen the window between reading and launching
            return await slow(assessment_id)

        planner.snapshot = delayed  # type: ignore[method-assign]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = {"decision": "approve"}
            replies = await asyncio.gather(
                c.post(f"/assessments/{aid}/approval", json=body),
                c.post(f"/assessments/{aid}/approval", json=body),
            )
        planner.snapshot = slow  # type: ignore[method-assign]
        await runs.tasks[aid]
        return sorted(x.status_code for x in replies)

    async def client_create(app: Any) -> Any:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            files = {
                "memo": ("memo.toml", (FIXTURE / "memo.toml").read_bytes()),
                "scripts": ("scripts.csv", (FIXTURE / "scripts.csv").read_bytes()),
            }
            data = {
                "goal": "Two approvals",
                "pack_id": "dbe-caps/mathematics-gr10",
                "poa_task": "Test",
                "budget_zar": "40",
            }
            return await c.post("/assessments", data=data, files=files)

    assert asyncio.run(run()) == [202, 409]
    events = [
        json.loads(x)
        for x in next(
            d for d in (tmp_path / "data").iterdir() if (d / "events.jsonl").exists()
        )  # data/ also holds published/, the local bundle store
        .joinpath("events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert sum(1 for e in events if e["kind"] == "delivered") == 1


@pytest.mark.parametrize("budget", ["Infinity", "NaN", "-5", "0", "1e9", "forty"])
def test_a_budget_that_is_not_a_finite_positive_amount_is_a_422(
    client: TestClient, tmp_path: Path, budget: str
) -> None:
    """Codex P2: Infinity and NaN were a 500, not a 422."""
    response = create(client, tmp_path, budget_zar=budget)
    assert response.status_code == 422
    assert [e["field"] for e in response.json()["errors"]] == ["budget_zar"]


def test_a_scripts_file_with_no_learners_is_a_422(client: TestClient, tmp_path: Path) -> None:
    """Codex P2: a header-only CSV created a run that crashed at once."""
    header_only = b"learner,question_id,answer\n"
    response = create(client, tmp_path, files={"scripts": ("scripts.csv", header_only)})
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"field": "scripts", "message": "the file holds no learners' answers"}
    ]
    assert not list((tmp_path / "data").glob("*"))  # nothing left behind


class DropsQ4OnL014(ScriptedModel):
    """Marks one script without question 4 every time: three attempts, none complete."""

    target = "L-014"

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        reply, usage = await super().complete(system, user, output)
        if output.__name__ == "MarkerReply" and f"script {self.target}" in user:
            data = reply.model_dump(mode="json")
            data["marks"] = [m for m in data["marks"] if m["question_id"] != "4"]
            return output.model_validate(data), usage
        return reply, usage


def test_an_attempt_that_misses_a_question_cannot_be_adopted(tmp_path: Path) -> None:
    """Codex P2: use_attempt on an incomplete marking is a 422 pointing at set_marks."""
    planner = Planner(
        primary=DropsQ4OnL014(),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner)) as c:
        aid = create(c, tmp_path).json()["assessment_id"]
        wait_for(c, aid, "needs_teacher", topic="memo")
        c.post(f"/assessments/{aid}/memo", json={"decision": "use_proposed", "question_id": "2.3"})
        wait_for(c, aid, "needs_teacher", topic="script", script_id="L-014")
        reply = c.post(
            f"/assessments/{aid}/scripts/L-014", json={"decision": "use_attempt", "attempt": 2}
        )
        assert reply.status_code == 422
        assert "set the marks" in reply.json()["errors"][0]["message"]


class DropsQ4OnL001(DropsQ4OnL014):
    target = "L-001"


def test_marks_for_a_blank_answer_are_a_422_not_a_crash(tmp_path: Path) -> None:
    """Codex P2: a blank answer row is not an answer; credit for it has nothing to quote."""
    planner = Planner(
        primary=DropsQ4OnL001(),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    csv = (FIXTURE / "scripts.csv").read_bytes() + b"Amogelang Khumalo,2.3,\n"
    with TestClient(create_app(planner)) as c:
        aid = create(c, tmp_path, files={"scripts": ("scripts.csv", csv)}).json()["assessment_id"]
        wait_for(c, aid, "needs_teacher", topic="memo")
        c.post(f"/assessments/{aid}/memo", json={"decision": "use_proposed", "question_id": "2.3"})
        wait_for(c, aid, "needs_teacher", topic="script", script_id="L-001")
        ids = ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3", "3.1", "3.2", "4"]
        marks = [{"question_id": q, "awarded": 1 if q == "2.3" else 0} for q in ids]
        reply = c.post(
            f"/assessments/{aid}/scripts/L-001", json={"decision": "set_marks", "marks": marks}
        )
        assert reply.status_code == 422
        assert "2.3: no answer to give marks for" in reply.json()["errors"][0]["message"]


class OmitsSetter23(ScriptedModel):
    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        reply, usage = await super().complete(system, user, output)
        if output.__name__ == "SetterReply":
            data = reply.model_dump(mode="json")
            data["answers"] = [a for a in data["answers"] if a["question_id"] != "2.3"]
            return output.model_validate(data), usage
        return reply, usage


def test_use_proposed_with_nothing_proposed_is_a_422(tmp_path: Path) -> None:
    """Codex P2: it was silently taken as keep_memo."""
    planner = Planner(
        primary=OmitsSetter23(),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner)) as c:
        aid = create(c, tmp_path).json()["assessment_id"]
        question = wait_for(c, aid, "needs_teacher", topic="memo")
        assert question["payload"]["proposed_answer"] == ""
        reply = c.post(
            f"/assessments/{aid}/memo", json={"decision": "use_proposed", "question_id": "2.3"}
        )
        assert reply.status_code == 422
        assert reply.json()["errors"][0]["field"] == "decision"


def test_a_scripts_header_csv_cannot_parse_is_a_422(client: TestClient, tmp_path: Path) -> None:
    """Codex P2: csv.Error from the header was a 500."""
    broken = b'learner,"question_id,answer\nA,1.1,x\n'
    response = create(client, tmp_path, files={"scripts": ("scripts.csv", broken)})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "scripts"


def test_replay_never_reads_the_live_price_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex P2: with pricing.toml present, a replayed 'scripted' call must not need a rate."""
    import asyncio

    from mothusi_core import providers
    from mothusi_core.smoke import check_planted, run_planted

    def live_only(model: str, i: int, o: int) -> Any:
        raise ValueError(f"unknown model {model!r}")

    monkeypatch.setattr(providers, "pricer", lambda: live_only)
    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")
    monkeypatch.delenv("MOTHUSI_REPLAY", raising=False)
    found = providers.from_environment()
    assert found.fallback is None and found.priced
    assert found.reader is found.text is found.moderator  # replay serves every role
    state, events, names = asyncio.run(run_planted(found.text, tmp_path))
    assert check_planted(state, events, names) == []
