"""Health checks, the SSE keep-alive and the Cognito check (deployment-aws.md §6.5)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_core.auth import CognitoVerifier, needs_token
from mothusi_core.events import PING, with_pings
from mothusi_core.graph.planner import Planner
from mothusi_core.main import create_app

POOL, CLIENT = "af-south-1_TEST", "client-123"
ISSUER = f"https://cognito-idp.af-south-1.amazonaws.com/{POOL}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Silent:
    name = "silent"

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no model is called in these tests")


def planner(tmp_path: Path) -> Planner:
    return Planner(primary=Silent(), fallback=None, data_dir=tmp_path, checkpointer=InMemorySaver())


def token(key: Any = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT,
        "token_use": "id",
        "iat": now,
        "exp": now + 3600,
        "sub": "teacher-1",
    } | overrides
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def secured(tmp_path: Path) -> Iterator[TestClient]:
    verifier = CognitoVerifier(POOL, CLIENT, signing_key=lambda _token: KEY.public_key())
    with TestClient(create_app(planner(tmp_path), verifier=verifier)) as client:
        yield client


@pytest.fixture
def open_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    with TestClient(create_app(planner(tmp_path))) as client:
        yield client


def test_live_is_always_200_and_needs_no_token(secured: TestClient) -> None:
    response = secured.get("/health/live")
    assert (response.status_code, response.json()) == (200, {"status": "live"})


def test_health_reports_an_unreachable_model_host_as_degraded(
    open_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")  # discard port: nothing listens
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    response = open_client.get("/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "db": "ok",
        "ollama": "unreachable",
        "models": [],
    }


def test_health_is_ok_without_a_model_host_when_the_provider_is_not_ollama(
    open_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    response = open_client.get("/health")
    assert (response.status_code, response.json()["status"]) == (200, "ok")


def test_a_valid_teacher_token_is_let_through(secured: TestClient) -> None:
    response = secured.get("/packs", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer not-a-jwt",
        f"Bearer {token(OTHER)}",  # signed by another key
        f"Bearer {token(aud='someone-else')}",
        f"Bearer {token(iss='https://evil.example/pool')}",
        f"Bearer {token(token_use='access')}",
        f"Bearer {token(exp=int(time.time()) - 10)}",
        f"Basic {token()}",
    ],
)
def test_anything_but_a_valid_id_token_is_401(secured: TestClient, header: str | None) -> None:
    headers = {} if header is None else {"Authorization": header}
    response = secured.get("/packs", headers=headers)
    assert response.status_code == 401
    assert "sign in" in response.json()["message"]


def test_health_note_links_and_preflights_need_no_teacher_token(secured: TestClient) -> None:
    assert secured.get("/health/live").status_code == 200
    assert secured.get("/n/sometoken").status_code != 401  # the note route checks its own token
    assert not needs_token("OPTIONS", "/packs")
    assert needs_token("GET", "/assessments/x/events")
    assert not needs_token("GET", "/n/abc")


def test_with_no_cognito_config_the_api_is_open_as_before(open_client: TestClient) -> None:
    assert open_client.get("/packs").status_code == 200


def test_an_idle_stream_sends_pings_and_passes_frames_through() -> None:
    async def slow() -> AsyncIterator[str]:
        yield "id: 1\n\n"
        await asyncio.sleep(0.25)
        yield "id: 2\n\n"

    async def collect() -> list[str]:
        return [item async for item in with_pings(slow(), every_s=0.05)]

    out = asyncio.run(collect())
    assert out[0] == "id: 1\n\n" and out[-1] == "id: 2\n\n"
    assert PING in out[1:-1]


def test_an_unknown_key_id_is_401_and_an_unreachable_key_set_is_503(tmp_path: Path) -> None:
    from mothusi_core.auth import AuthUnavailableError

    def unknown_kid(_token: str) -> Any:
        raise jwt.InvalidTokenError("Unable to find a signing key that matches")

    def jwks_down(_token: str) -> Any:
        raise AuthUnavailableError("connection refused")

    for signing_key, expected in ((unknown_kid, 401), (jwks_down, 503)):
        verifier = CognitoVerifier(POOL, CLIENT, signing_key=signing_key)
        with TestClient(create_app(planner(tmp_path), verifier=verifier)) as client:
            response = client.get("/packs", headers={"Authorization": f"Bearer {token()}"})
            assert response.status_code == expected


def test_the_default_key_lookup_maps_jwks_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from mothusi_core.auth import AuthUnavailableError

    def missing(self: Any, token: str) -> Any:
        raise jwt.PyJWKClientError("Unable to find a signing key that matches: 'kid'")

    def down(self: Any, token: str) -> Any:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    verifier = CognitoVerifier(POOL, CLIENT)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt", missing)
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(token())
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt", down)
    with pytest.raises(AuthUnavailableError):
        verifier.verify(token())


def test_a_401_carries_cors_headers_for_the_console_origin(secured: TestClient) -> None:
    response = secured.get("/packs", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
