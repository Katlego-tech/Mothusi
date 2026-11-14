"""Voice sessions use a server-side credential and safe failure messages."""

import asyncio
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_core.graph.planner import Planner
from mothusi_core.main import create_app
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY
from mothusi_llm import ReplayProvider


@pytest.mark.parametrize(
    "failure,reason",
    [
        (None, None),
        ("key", "no ElevenLabs key configured"),
        ("agent", "no ElevenLabs agent configured"),
        (401, "ElevenLabs refused the session: 401"),
        (302, "ElevenLabs refused the session: 302"),
        ("network", "ElevenLabs is unreachable"),
        ("timeout", "ElevenLabs is unreachable"),
        ("http", "ElevenLabs refused the session: 403"),
    ],
)
def test_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | int | None, reason: str | None
) -> None:
    key = "private-test-credential"
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent-test")
    if failure == "key":
        monkeypatch.delenv("ELEVENLABS_API_KEY")
    if failure == "agent":
        monkeypatch.delenv("ELEVENLABS_AGENT_ID")
    calls: list[httpx.Request] = []

    def elevenlabs(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert dict(request.url.params) == {"agent_id": "agent-test"}
        assert request.headers["xi-api-key"] == key
        assert request.extensions["timeout"]["read"] == 10
        if failure == "http":
            raise httpx.HTTPStatusError(key, request=request, response=httpx.Response(403))
        if failure == "network":
            raise httpx.ConnectError(key, request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(key, request=request)
        return httpx.Response(
            failure if isinstance(failure, int) else 200,
            json={"signed_url": "wss://session.test", "error": key},
        )

    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    outbound = httpx.AsyncClient(transport=httpx.MockTransport(elevenlabs))

    async def request_session() -> httpx.Response:
        app = create_app(planner, voice_client=outbound, auth_from_environment=False)
        async with (
            outbound,
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
        ):
            return await c.get("/voice/session")

    response = asyncio.run(request_session())
    assert key not in response.text
    assert response.status_code == (503 if reason else 200)
    assert response.json() == (
        {"message": f"Voice is off: {reason}"} if reason else {"signed_url": "wss://session.test"}
    )
    assert len(calls) == (0 if failure in {"key", "agent"} else 1)
