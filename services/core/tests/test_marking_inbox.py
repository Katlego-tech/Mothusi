"""Answers accepted while replay marking is running, and across a checkpoint restart."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from mothusi_contracts import Contract, EventKind
from mothusi_core.graph.planner import Planner, _build  # pyright: ignore[reportPrivateUsage]
from mothusi_core.main import Runs, create_app
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY
from mothusi_llm import ReplayProvider, Usage

from .test_graph import start_small


class GatedReplay:
    """Break L-014's evidence on every attempt; hold the next batch until released."""

    name = "replay"

    def __init__(self) -> None:
        self.inner = ReplayProvider(REPLAY, free)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.third_started = asyncio.Event()
        self.finish = asyncio.Event()
        self.finish.set()
        self.fail_ids = {"L-014"}
        self.failed_replies: dict[str, tuple[dict[str, Any], Usage]] = {}

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        if output.__name__ == "MarkerReply" and "script L-001" in user:
            self.started.set()
            await self.release.wait()
        if output.__name__ == "MarkerReply" and "script L-003" in user:
            self.third_started.set()
            await self.finish.wait()
        sid = next((sid for sid in self.fail_ids if f"script {sid}" in user), None)
        failing = output.__name__ == "MarkerReply" and sid is not None
        if failing and sid in self.failed_replies:
            data, usage = self.failed_replies[sid]
            return output.model_validate(data), usage
        reply, usage = await self.inner.complete(system, user, output)
        if failing and sid is not None:
            data = reply.model_dump(mode="json")
            for mark in data["marks"]:
                if mark["awarded"]:
                    mark["evidence_quote"] = "a quotation absent from this script"
            self.failed_replies[sid] = data, usage
            reply = output.model_validate(data)
        return reply, usage


def make_planner(tmp_path: Path, model: GatedReplay, **kwargs: Any) -> Planner:
    planner = Planner(
        primary=model,
        fallback=None,
        data_dir=tmp_path,
        checkpointer=kwargs.pop("checkpointer", InMemorySaver()),
        store=kwargs.pop("store", InMemoryStore()),
        batch_size=1,
        **kwargs,
    )
    planner.graph = _build(planner).compile(  # pyright: ignore[reportUnknownMemberType]
        checkpointer=planner.checkpointer, store=planner.store, interrupt_before=["moderating"]
    )
    return planner


async def begin(p: Planner) -> None:
    await start_small(p, "inbox", ("L-014", "L-001", "L-003"), memo_answer=None)
    # Put the flagged script before the two gated batches.
    s = await p.snapshot("inbox")
    await p.graph.aupdate_state(
        p.config("inbox"),
        {
            "scripts": sorted(
                s["scripts"], key=lambda x: {"L-014": 0, "L-001": 1, "L-003": 2}[x["script_id"]]
            )
        },
    )


def test_answer_during_marking_is_applied_at_the_boundary(tmp_path: Path) -> None:
    async def run() -> None:
        model = GatedReplay()
        model.finish.clear()
        p = make_planner(tmp_path, model)
        await begin(p)
        app = create_app(p, auth_from_environment=False)
        runs = Runs(p)
        app.state.runs = runs
        runs.launch("inbox", p.resume("inbox", {"decision": "use_proposed"}))
        await asyncio.wait_for(model.started.wait(), 5)
        s = await p.snapshot("inbox")
        assert "L-014" in s.get("pending", {})
        assert s.get("question") is None
        assert any(
            e.kind is EventKind.NEEDS_TEACHER and e.payload.get("script_id") == "L-014"
            for e in p.sink("inbox").read()
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            url = "/assessments/inbox/scripts/L-014"
            body = {"decision": "use_attempt", "attempt": 1}
            assert (
                await c.post("/assessments/unknown/scripts/L-014", json=body)
            ).status_code == 404
            bad = await c.post(url, json={"decision": "use_attempt", "attempt": 99})
            assert bad.status_code == 422
            accepted = await c.post(url, json=body)
            assert accepted.status_code == 202 and accepted.json() == {"state": "marking"}
            entry = await p.store.aget(("inbox", "inbox"), "L-014")
            assert entry is not None and entry.value["answer"] == body
            assert datetime.fromisoformat(entry.value["received_at"]).utcoffset() == timedelta(0)
            assert (
                await c.post("/assessments/inbox/approval", json={"decision": "approve"})
            ).status_code == 409
            duplicate = await c.post(url, json=body)
            assert duplicate.status_code == 409
            assert (
                duplicate.json()["message"] == "L-014 already has an answer waiting to be applied"
            )
            model.release.set()
            await asyncio.wait_for(model.third_started.wait(), 5)
            s = await p.snapshot("inbox")
            assert s.get("pending") == {}
            assert s.get("marked", {})["L-014"]["marker"] == "teacher"
            assert runs.busy("inbox") and s["state"] == "marking"
            model.finish.set()
            await runs.tasks["inbox"]
            s = await p.snapshot("inbox")
            assert s.get("pending") == {}
            assert s.get("marked", {})["L-014"]["marker"] == "teacher"
            assert s.get("teacher_steps") == 2
            assert await p.store.aget(("inbox", "inbox"), "L-014") is None
            settled = await c.post(url, json=body)
            assert settled.status_code == 409
            assert settled.json()["message"] == "L-014 is not pending a teacher decision"
        events = p.sink("inbox").read()
        applied = next(
            e.seq
            for e in events
            if e.kind is EventKind.TEACHER and e.payload.get("script_id") == "L-014"
        )
        moderation = next(
            e.seq
            for e in events
            if e.kind is EventKind.STATE and e.payload.get("to") == "moderating"
        )
        assert applied < moderation

    asyncio.run(run())


def test_unanswered_script_interrupts_only_after_last_batch(tmp_path: Path) -> None:
    async def run() -> None:
        model = GatedReplay()
        p = make_planner(tmp_path, model)
        await begin(p)
        task = asyncio.create_task(p.resume("inbox", {"decision": "use_proposed"}))
        await asyncio.wait_for(model.started.wait(), 5)
        assert (await p.snapshot("inbox")).get("question") is None
        model.release.set()
        s = await task
        assert s["state"] == "marking"
        question = s.get("question")
        assert question is not None and question["script_id"] == "L-014"
        assert "L-001" in s.get("marked", {})
        assert not any(
            e.kind is EventKind.STATE and e.payload.get("to") == "moderating"
            for e in p.sink("inbox").read()
        )

    asyncio.run(run())


def test_inbox_survives_a_new_planner(tmp_path: Path) -> None:
    async def run() -> None:
        from mothusi_core.graph import inbox

        model = GatedReplay()
        saver, store = InMemorySaver(), InMemoryStore()
        p = make_planner(tmp_path, model, checkpointer=saver, store=store)
        await begin(p)
        task = asyncio.create_task(p.resume("inbox", {"decision": "use_proposed"}))
        await asyncio.wait_for(model.started.wait(), 5)
        await inbox.put(store, "inbox", "L-014", {"decision": "use_attempt", "attempt": 1})
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        restarted = make_planner(tmp_path, model, checkpointer=saver, store=store)
        model.release.set()
        await restarted.graph.ainvoke(None, restarted.config("inbox"))  # pyright: ignore[reportUnknownMemberType]
        s = await restarted.snapshot("inbox")
        assert s.get("pending") == {}
        assert s.get("marked", {})["L-014"]["marker"] == "teacher"
        assert await store.aget(("inbox", "inbox"), "L-014") is None

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["use_attempt", "set_marks"])
def test_other_pending_answers_are_used_before_the_next_interrupt(
    tmp_path: Path, decision: str
) -> None:
    async def run() -> None:
        model = GatedReplay()
        model.fail_ids.add("L-003")
        model.release.set()
        p = make_planner(tmp_path, model)
        await begin(p)
        s = await p.resume("inbox", {"decision": "use_proposed"})
        question = s.get("question")
        assert question is not None and question["script_id"] == "L-014"
        assert set(s.get("pending", {})) == {"L-014", "L-003"}
        app = create_app(p, auth_from_environment=False)
        runs = Runs(p)
        app.state.runs = runs
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            body: dict[str, Any] = {"decision": "use_attempt", "attempt": 1}
            queued = await c.post("/assessments/inbox/scripts/L-003", json=body)
            assert queued.status_code == 202 and queued.json() == {"state": "marking"}
            assert not runs.busy("inbox")
            if decision == "set_marks":
                body = {
                    "decision": "set_marks",
                    "marks": [
                        {"question_id": i["question_id"], "awarded": 0} for i in s["memo"]["items"]
                    ],
                }
            resumed = await c.post("/assessments/inbox/scripts/L-014", json=body)
            assert resumed.status_code == 202
            await runs.tasks["inbox"]
        s = await p.snapshot("inbox")
        assert s["state"] == "moderating" and s.get("pending") == {}
        assert s.get("teacher_steps") == 3
        assert s.get("marked", {})["L-003"]["marker"] == "teacher"
        assert s.get("marked", {})["L-014"]["marker"] == "teacher"
        if decision == "set_marks":
            assert s.get("marked", {})["L-014"]["total"] == 0
        assert await p.store.asearch(("inbox", "inbox")) == []
        questions = [
            e
            for e in p.sink("inbox").read()
            if e.kind is EventKind.NEEDS_TEACHER and e.payload.get("script_id") == "L-003"
        ]
        assert len(questions) == 1  # flagged once, never asked again after its inbox answer

    asyncio.run(run())


def test_script_answer_is_accepted_at_the_budget_wait(tmp_path: Path) -> None:
    async def run() -> None:
        model = GatedReplay()
        model.release.set()
        p = make_planner(tmp_path, model)
        await begin(p)
        await p.graph.aupdate_state(p.config("inbox"), {"spent_zar": "41"})
        s = await p.resume("inbox", {"decision": "use_proposed"})
        question = s.get("question")
        assert question is not None and question["topic"] == "budget"
        app = create_app(p, auth_from_environment=False)
        runs = Runs(p)
        app.state.runs = runs
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            reply = await c.post(
                "/assessments/inbox/scripts/L-014", json={"decision": "use_attempt", "attempt": 1}
            )
            assert reply.status_code == 202 and reply.json() == {"state": "marking"}
            assert not runs.busy("inbox")
            reply = await c.post("/assessments/inbox/budget", json={"raise_to_zar": "100"})
            assert reply.status_code == 202
            await runs.tasks["inbox"]
        s = await p.snapshot("inbox")
        assert s["state"] == "moderating" and s.get("pending") == {}
        assert s.get("teacher_steps") == 3
        assert s.get("marked", {})["L-014"]["marker"] == "teacher"

    asyncio.run(run())


def test_answer_arriving_as_the_final_interrupt_opens_is_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mothusi_core.graph import inbox
    from mothusi_core.graph.state import Json

    async def run() -> None:
        model = GatedReplay()
        model.release.set()
        p = make_planner(tmp_path, model)
        await begin(p)
        before_interrupt, release_interrupt = asyncio.Event(), asyncio.Event()
        take_all = inbox.take_all

        async def delayed(store: Any, assessment_id: str) -> dict[str, Json]:
            entries = await take_all(store, assessment_id)
            q = (await p.snapshot(assessment_id)).get("question") or {}
            if q.get("topic") == "script" and not before_interrupt.is_set():
                before_interrupt.set()
                await release_interrupt.wait()
            return entries

        monkeypatch.setattr(inbox, "take_all", delayed)
        app = create_app(p, auth_from_environment=False)
        runs = Runs(p)
        app.state.runs = runs
        runs.launch("inbox", p.resume("inbox", {"decision": "use_proposed"}))
        await asyncio.wait_for(before_interrupt.wait(), 5)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            reply = await c.post(
                "/assessments/inbox/scripts/L-014", json={"decision": "use_attempt", "attempt": 1}
            )
            assert reply.status_code == 202
            assert runs.busy("inbox")
        release_interrupt.set()
        await runs.tasks["inbox"]
        s = await p.snapshot("inbox")
        assert s["state"] == "moderating" and s.get("pending") == {}
        assert s.get("marked", {})["L-014"]["marker"] == "teacher"
        assert await p.store.aget(("inbox", "inbox"), "L-014") is None

    asyncio.run(run())
