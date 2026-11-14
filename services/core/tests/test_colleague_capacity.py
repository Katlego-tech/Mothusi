"""Capacity defers only model routing and resumes both thread and desk requests."""

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Contract, EventKind
from mothusi_core.colleague import Skill, respond
from mothusi_core.graph.planner import Planner
from mothusi_core.graph.state import RunState
from mothusi_core.main import Runs, create_app
from mothusi_core.providers import free
from mothusi_core.smoke import PLANTED, REPLAY
from mothusi_llm import ReplayProvider, Usage

from .test_graph import start_small


@pytest.fixture
def planted(tmp_path: Path) -> tuple[Planner, RunState]:
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
        teacher_email="teacher@mothusi.test",
    )
    state = asyncio.run(start_small(planner, "chat", PLANTED, memo_answer="use_proposed"))
    return planner, cast(RunState, state)


class Model:
    name = "routing-test"

    def __init__(self, skill: Skill) -> None:
        self.skill = skill
        self.calls: list[str] = []

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.calls.append(user)
        return output.model_validate({"skill": self.skill}), Usage(
            model=self.name, input_tokens=1, output_tokens=1, cost_zar=Decimal(0)
        )


@pytest.mark.parametrize("crash", [False, True])
def test_capacity(
    planted: tuple[Planner, RunState],
    monkeypatch: pytest.MonkeyPatch,
    crash: bool,
) -> None:
    planner, state = planted
    model = Model("run_status")

    def provider(*_: object) -> Model:
        return model

    monkeypatch.setattr("mothusi_core.colleague.colleague_provider", provider)
    calls = model.calls

    async def exercise() -> None:
        runs = Runs(planner)
        release = asyncio.Event()

        async def work() -> None:
            await release.wait()
            if crash:
                raise RuntimeError("failed marking stage")

        runs.launch("chat", work())
        await respond(planner, "chat", "m-1", "how far are you?", capacity=runs)
        replies = [e for e in planner.sink("chat").read() if e.kind is EventKind.REPLY]
        assert replies[-1].payload["skill"] == "run_status"
        assert not calls
        await respond(planner, "chat", "m-2", "please take a look", capacity=runs)
        replies = [e for e in planner.sink("chat").read() if e.kind is EventKind.REPLY]
        assert replies[-1].payload["skill"] == "deferred"
        assert replies[-1].payload["say"] == (
            f"I'm at capacity: I'm marking {state['title']} on my GPU right now. "
            "I'll attend to this as soon as that's done, and answer here."
        )
        assert not calls
        await respond(planner, "chat", "m-3", "and have another look", capacity=runs)
        release.set()
        await runs.tasks["chat"]
        await asyncio.gather(*runs.replies)
        replies = [e for e in planner.sink("chat").read() if e.kind is EventKind.REPLY]
        assert replies[-1].payload["message_id"] == "m-3"
        assert any(
            e.payload["message_id"] == "m-2" and e.payload["skill"] == "run_status" for e in replies
        )
        assert replies[-1].payload["skill"] == "run_status"
        assert calls == ["please take a look", "and have another look"]
        assert await runs.busy_run() is None

    asyncio.run(exercise())


def test_desk_polling(planted: tuple[Planner, RunState], monkeypatch: pytest.MonkeyPatch) -> None:
    planner, _ = planted

    model = Model("fallback")

    def provider(*_: object) -> Model:
        return model

    monkeypatch.setattr("mothusi_core.main.colleague_provider", provider)

    async def exercise() -> None:
        app = create_app(planner, auth_from_environment=False)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
        ):
            runs = cast(Runs, app.state.runs)
            release = asyncio.Event()
            runs.launch("chat", release.wait())
            immediate = await c.post("/colleague/messages", json={"text": "how far are you?"})
            assert immediate.json()["reply"]["skill"] == "run_status"
            assert immediate.json()["deferred_id"] is None
            assert not model.calls
            response = await c.post("/colleague/messages", json={"text": "please take a look"})
            assert response.status_code == 200
            body = response.json()
            assert body["reply"]["skill"] == "deferred"
            url = f"/colleague/deferred/{body['deferred_id']}"
            assert (await c.get(url)).status_code == 202
            assert (await c.get(url)).json() == {"state": "waiting"}
            release.set()
            await runs.tasks["chat"]
            await asyncio.gather(*runs.replies)
            assert (await c.get(url)).status_code == 200
            assert (await c.get(url)).json()["reply"]["skill"] == "fallback"
            assert (await c.get("/colleague/deferred/unknown")).status_code == 404

    asyncio.run(exercise())
