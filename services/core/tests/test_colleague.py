"""Conversation rules, grounded replies and approval on the planted replay run."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import EventKind
from mothusi_core.colleague import Intent, compose, keep_promises, match_rules, respond, understand
from mothusi_core.graph.planner import Planner
from mothusi_core.graph.state import RunState
from mothusi_core.main import Runs, create_app
from mothusi_core.providers import free
from mothusi_core.smoke import PLANTED, REPLAY
from mothusi_llm import Provider, ReplayProvider

from .test_graph import start_small


@pytest.mark.parametrize(
    "text,skill",
    [
        ("how far are you?", "run_status"),
        ("status", "run_status"),
        ("why did L-014 get 3 on 2.1?", "explain_mark"),
        ("explain l-014 question 4", "explain_mark"),
        ("why L-014 on 3.1.2?", "explain_mark"),
        ("send L-001 their feedback", "send_feedback"),
    ],
)
def test_rules(text: str, skill: str) -> None:
    intent = match_rules(text)
    assert intent is not None
    assert intent.skill == skill
    if skill == "explain_mark":
        assert intent.script_id == "L-014"
        assert intent.question_id == (
            "4" if "question" in text else "3.1.2" if "3.1.2" in text else "2.1"
        )


@pytest.mark.parametrize("text", ["why l-14 on 2.1?", "send L-0001 feedback", "yes send it"])
def test_invalid_or_unapproved_text(text: str) -> None:
    assert match_rules(text) is None
    assert asyncio.run(understand(text, None)).skill == "fallback"


@pytest.fixture
def planted(tmp_path: Path) -> tuple[Planner, Any]:
    p = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
        teacher_email="teacher@mothusi.test",
    )
    state = asyncio.run(start_small(p, "chat", PLANTED, memo_answer="use_proposed"))
    assert state["state"] == "awaiting_approval"
    return p, state


def test_grounded_explanation_and_status(planted: tuple[Planner, Any]) -> None:
    p, settled = planted
    state = deepcopy(settled)
    state.update(state="marking", moderated={}, moderation_closed=False)
    mark = state["marked"]["L-014"]["marks"][0]
    reply = compose(
        Intent(skill="explain_mark", script_id="L-014", question_id=mark["question_id"]),
        state,
        set(),
        set(),
    )
    assert mark["evidence_quote"] in reply["say"]
    assert mark["memo_line"] in reply["say"]
    assert f"{mark['awarded']}/{mark['max_marks']}" in reply["say"]
    assert "provisional, not yet moderated" in reply["say"]
    for final in [dict(state, moderated={"L-014": {}}), dict(state, moderation_closed=True)]:
        assert (
            "provisional"
            not in compose(
                Intent(skill="explain_mark", script_id="L-014", question_id=mark["question_id"]),
                cast(RunState, final),
                set(),
                set(),
            )["say"]
        )
    state["observed_at"] = p.clock().isoformat()
    state["started_at"] = (
        datetime.fromisoformat(state["observed_at"]) - timedelta(minutes=5)
    ).isoformat()
    status = compose(Intent(skill="run_status"), state, set(), set())["say"]
    assert "Marking" in status and "6 of 6" in status and "5 minutes" in status
    assert "R" in status and "40.00" in status


def test_every_send_feedback_row(planted: tuple[Planner, Any]) -> None:
    _, state = planted
    intent = Intent(skill="send_feedback", script_id="L-001")
    reply = compose(intent, state, set(), set())
    assert reply["card"] is not None
    assert reply["card"]["script_id"] == "L-001" and reply["promise"] is None
    before = dict(state, state="marking")
    reply = compose(intent, cast(RunState, before), set(), set())
    assert "provisional" in reply["say"] and "blind sample" in reply["say"]
    assert reply["promise"] == "L-001" and reply["card"] is None
    reply = compose(intent, cast(RunState, dict(before, marked={})), set(), set())
    assert "isn't marked yet" in reply["say"] and reply["promise"] == "L-001"
    failed = deepcopy(state)
    failed["verdicts"] = [{"pseudonym": "L-001", "passed": False, "reason": "unhelpful tone"}]
    reply = compose(intent, failed, set(), set())
    assert "unhelpful tone" in reply["say"] and reply["card"] is None
    assert (
        "went out with the set"
        in compose(intent, cast(RunState, dict(state, state="delivered")), set(), set())["say"]
    )
    sent_state = dict(state, early_sent_at={"L-001": "2026-11-14T04:12:00+02:00"})
    released = compose(intent, cast(RunState, sent_state), set(), {"L-001"})["say"]
    assert released == "L-001's note was released at 04:12."
    assert (
        compose(Intent(skill="send_feedback", script_id="L-999"), state, set(), set())["say"]
        == "There's no learner L-999 in this class."
    )


def test_promises_kept_once_and_names_are_mapped(planted: tuple[Planner, Any]) -> None:
    p, state = planted

    async def run() -> None:
        await p.store.aput(("promises", "chat"), "L-001", {"message_id": "m-1"})
        await keep_promises(p, "chat", state)
        await keep_promises(p, "chat", state)
        await respond(p, "chat", "m-2", f"send {state['names']['L-001']} their feedback")
        await respond(p, "chat", "m-3", "why L-001 question 999?")

    asyncio.run(run())
    replies = [
        cast(dict[str, Any], e.payload) for e in p.sink("chat").read() if e.kind is EventKind.REPLY
    ]
    assert len([r for r in replies if r["message_id"] == "m-1"]) == 1
    assert cast(dict[str, Any], replies[-2]["card"])["script_id"] == "L-001"
    assert replies[-1]["skill"] == "fallback"


def test_send_requires_card_and_skips_early_sent_at_delivery(planted: tuple[Planner, Any]) -> None:
    p, _ = planted
    with TestClient(create_app(p)) as client:
        path = "/assessments/chat"
        assert client.post(path + "/notes/L-999/send").status_code == 404
        assert client.post(path + "/notes/L-001/send").status_code == 409
        response = client.post(path + "/messages", json={"text": "send L-001 their feedback"})
        assert response.status_code == 202 and response.json() == {"message_id": "m-1"}

        # Wait for background replies using the application's own retained tasks.
        async def wait() -> None:
            runs = cast(Runs, cast(Any, client.app).state.runs)
            await asyncio.gather(*runs.replies)

        assert client.portal is not None
        client.portal.call(wait)
        sent = client.post(path + "/notes/L-001/send")
        assert sent.status_code == 202, sent.text
        assert len(list((p.data_dir / "chat/early/L-001/notes").glob("*.md"))) == 1
        from mothusi_delivery import note_link

        from .conftest import TEST_NOTE_KEY

        token = note_link(TEST_NOTE_KEY.encode(), "chat", "L-001", p.clock() + timedelta(days=1))
        assert client.get(f"/n/{token}").status_code == 200
        assert client.post(path + "/notes/L-001/send").status_code == 409
        assert client.post(path + "/messages", json={"text": "  "}).status_code == 422
        assert client.post(path + "/messages", json={"text": "x" * 1001}).status_code == 422
        assert (
            client.post("/assessments/unknown/messages", json={"text": "status"}).status_code == 404
        )

        async def deliver() -> Any:
            return await p.resume("chat", {"decision": "approve"})

        final = client.portal.call(deliver)
        assert final["state"] == "delivered"
        assert not (p.data_dir / "chat/bundle/notes/L-001.md").exists()
        assert len(list((p.data_dir / "chat/bundle/notes").glob("*.md"))) == 5
        assert len(list((p.data_dir / "chat/outbox").rglob("*.eml"))) == 2
        assert client.get(f"/n/{token}").status_code == 200
        assert "1 sent earlier" in p.sink("chat").read()[-1].summary
        assert client.post(path + "/messages", json={"text": "status"}).json() == {
            "message": "the run is delivered; start a new run to ask about it"
        }
    notes = [e for e in p.sink("chat").read() if e.kind is EventKind.NOTE_SENT]
    assert len(notes) == 1 and notes[0].agent is None
    executor = [
        e
        for e in p.sink("chat").read()
        if e.kind is EventKind.BRIEF and e.agent and e.agent.value == "executor"
    ]
    assert executor


def test_messages_do_not_hold_the_run_lock_or_delay_marking(tmp_path: Path) -> None:
    import httpx

    from mothusi_core.main import Runs

    from .test_marking_inbox import GatedReplay

    async def run() -> None:
        model = GatedReplay()
        model.fail_ids = set()
        p = Planner(
            primary=model,
            fallback=None,
            data_dir=tmp_path,
            checkpointer=InMemorySaver(),
            batch_size=1,
        )
        await start_small(p, "live-chat", PLANTED, memo_answer=None)
        app = create_app(p, auth_from_environment=False)
        runs = Runs(p)
        app.state.runs = runs
        work = asyncio.create_task(p.resume("live-chat", {"decision": "use_proposed"}))
        try:
            await asyncio.wait_for(model.started.wait(), 5)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = "/assessments/live-chat"
                assert (await client.post(path + "/notes/L-001/send")).status_code == 409
                # A chat POST and its reply must work even while another endpoint holds this lock.
                async with runs.lock("live-chat"):
                    response = await asyncio.wait_for(
                        client.post(path + "/messages", json={"text": " status "}), 2
                    )
                    assert response.status_code == 202
                    await asyncio.wait_for(asyncio.gather(*runs.replies), 2)
                    promised = await client.post(
                        path + "/messages", json={"text": "send L-001 feedback"}
                    )
                    assert promised.json()["message_id"] == "m-2"
                    await asyncio.gather(*runs.replies)
                    assert await p.store.aget(("promises", "live-chat"), "L-001") is not None
                    # Hold understanding of an unknown message until marking finishes.
                    import mothusi_core.colleague as colleague

                    original = colleague.understand
                    waiting = asyncio.Event()
                    release = asyncio.Event()

                    async def blocked(*args: Any, **kwargs: Any) -> Intent:
                        waiting.set()
                        await release.wait()
                        return await original(*args, **kwargs)

                    colleague.understand = blocked
                    try:
                        response = await client.post(
                            path + "/messages", json={"text": "something else"}
                        )
                        assert response.json()["message_id"] == "m-3"
                        await asyncio.wait_for(waiting.wait(), 2)
                        model.release.set()
                        final = await asyncio.wait_for(work, 10)
                        assert final["state"] == "awaiting_approval"
                        assert runs.replies  # graph completed while the chat model is still waiting
                    finally:
                        release.set()
                        colleague.understand = original
                        await asyncio.gather(*runs.replies)
                log = p.sink("live-chat").read()
                assert any(
                    e.kind is EventKind.RESULT
                    and e.seq > next(e.seq for e in log if e.kind is EventKind.MESSAGE)
                    for e in log
                )
                kept = [
                    e
                    for e in log
                    if e.kind is EventKind.REPLY
                    and e.payload.get("message_id") == "m-2"
                    and e.payload.get("card")
                ]
                assert len(kept) == 1
                transition = next(
                    e.seq
                    for e in log
                    if e.kind is EventKind.STATE and e.payload.get("to") == "awaiting_approval"
                )
                assert kept[0].seq > transition
                assert await p.store.aget(("promises", "live-chat"), "L-001") is None
        finally:
            model.release.set()
            await asyncio.wait_for(work, 10)

    asyncio.run(run())


@pytest.mark.parametrize(
    "result",
    [
        {"skill": "explain_mark", "script_id": "L-999", "question_id": "2.1"},
        {"skill": "explain_mark", "script_id": "L-001", "question_id": "999"},
        {"skill": "send_feedback", "script_id": "l-14"},
        {"skill": "invent"},
        RuntimeError("model unavailable"),
    ],
)
def test_invalid_model_output_and_exceptions_fall_back(result: Any) -> None:
    class Model:
        name = "intent-test"

        async def complete(self, system: str, user: str, output: Any) -> Any:
            assert "L-001" in system and "2.1" in system
            if isinstance(result, Exception):
                raise result
            return result, None

    intent = asyncio.run(
        understand(
            "could you help?", cast(Provider, Model()), script_ids={"L-001"}, question_ids={"2.1"}
        )
    )
    assert intent.skill == "fallback"


def test_ollama_intent_model_receives_only_pseudonyms(
    planted: tuple[Planner, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimal import Decimal

    from mothusi_llm import Usage

    p, state = planted
    seen: list[str] = []

    class Model:
        name = "ollama-test"

        async def complete(self, system: str, user: str, output: Any, **kwargs: Any) -> Any:
            seen.append(system + user)
            return Intent(skill="send_feedback", script_id="L-001"), Usage(
                model=self.name, input_tokens=1, output_tokens=1, cost_zar=Decimal(0)
            )

    p.primary = Model()
    p._providers.clear()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
    name = state["names"]["L-001"]
    asyncio.run(respond(p, "chat", "m-4", f"please bring {name}'s thoughts"))
    assert len(seen) == 1 and name not in seen[0] and "L-001" in seen[0]
    payload = cast(dict[str, Any], p.sink("chat").read()[-1].payload)
    assert cast(dict[str, Any], payload["card"])["script_id"] == "L-001"


def test_a_failed_review_keeps_the_promise_with_its_reason(planted: tuple[Planner, Any]) -> None:
    p, state = planted
    failed = deepcopy(state)
    failed["verdicts"] = [{"pseudonym": "L-001", "passed": False, "reason": "unsupported praise"}]

    async def run() -> None:
        await p.store.aput(("promises", "chat"), "L-001", {"message_id": "m-failed"})
        await asyncio.gather(keep_promises(p, "chat", failed), keep_promises(p, "chat", failed))

    asyncio.run(run())
    replies = [e for e in p.sink("chat").read() if e.kind is EventKind.REPLY]
    assert len(replies) == 1
    payload = cast(dict[str, Any], replies[0].payload)
    assert payload["card"] is None and "unsupported praise" in payload["say"]


def test_a_halted_run_refuses_new_messages(planted: tuple[Planner, Any]) -> None:
    p, _ = planted

    async def halt() -> None:
        await p.graph.aupdate_state(p.config("chat"), {"halted": "model failed"})

    asyncio.run(halt())
    with TestClient(create_app(p)) as client:
        reply = client.post("/assessments/chat/messages", json={"text": "status"})
        assert reply.status_code == 409
        assert reply.json()["message"] == "the run is delivered; start a new run to ask about it"
