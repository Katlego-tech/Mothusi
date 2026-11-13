"""The core's knowledge seam, exercised entirely through MockTransport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from mothusi_contracts import Passage, PassageKind
from mothusi_core import knowledge
from mothusi_core.knowledge import (
    HttpKnowledge,
    Knowledge,
    KnowledgeError,
    NoKnowledge,
    knowledge_from_environment,
)

QUERY = "private learner query"


def passage_data() -> dict[str, object]:
    return {
        "passage_id": "caps-1",
        "source": "DBE CAPS Mathematics Grades 10-12",
        "source_key": "caps/maths.pdf",
        "kind": "caps",
        "subject": "Mathematics",
        "grade": 10,
        "page": 12,
        "locator": "p. 12",
        "text": "Algebraic expressions",
        "score": 0.9,
    }


def test_search_posts_contract_and_preserves_passages_and_citations() -> None:
    first = passage_data()
    second = {
        **first,
        "passage_id": "pack-2",
        "kind": "pack",
        "page": None,
        "locator": "conventions.algebra",
        "score": 0.7,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://knowledge.internal/prefix/search"
        assert json.loads(request.content) == {
            "query": QUERY,
            "k": 2,
            "subject": "Mathematics",
            "grade": 10,
            "kinds": ["caps", "pack"],
            "min_score": 0.0,
        }
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 1.25
        )
        return httpx.Response(200, json={"passages": [first, second], "model": "BAAI/bge-m3"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            api: Knowledge = HttpKnowledge("http://knowledge.internal/prefix/", 1.25, client=client)
            result = await api.search(
                QUERY,
                k=2,
                subject="Mathematics",
                grade=10,
                kinds=[PassageKind.CAPS, PassageKind.PACK],
            )
            assert result == (Passage.model_validate(first), Passage.model_validate(second))
            assert [p.citation for p in result] == [
                "DBE CAPS Mathematics Grades 10-12, p. 12",
                "DBE CAPS Mathematics Grades 10-12, conventions.algebra",
            ]
            assert not client.is_closed

    asyncio.run(run())


def test_default_request_and_empty_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "query": QUERY,
            "k": 5,
            "subject": None,
            "grade": None,
            "kinds": [],
            "min_score": 0.0,
        }
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 5.0
        )
        return httpx.Response(200, json={"passages": [], "model": "BAAI/bge-m3"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await HttpKnowledge("http://knowledge", client=client).search(QUERY) == ()

    asyncio.run(run())


@pytest.mark.parametrize("status", [201, 302, 422, 500, 503])
def test_non_200_is_a_private_knowledge_error(status: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=QUERY)

    assert_failure(respond, str(status))


@pytest.mark.parametrize(
    ("error_type", "message"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection")],
)
def test_transport_failures_are_private_knowledge_errors(
    error_type: type[httpx.RequestError],
    message: str,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise error_type(QUERY, request=request)

    assert_failure(respond, message)


def test_non_json_is_a_private_knowledge_error() -> None:
    assert_failure(lambda request: httpx.Response(200, text=QUERY), "JSON")


def test_lone_surrogate_is_a_private_knowledge_error_before_io() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"passages": [], "model": "BAAI/bge-m3"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(KnowledgeError) as caught:
                await HttpKnowledge("http://knowledge", client=client).search("abc\ud800def")
            assert str(caught.value) == "Knowledge request failed"
            assert caught.value.__cause__ is None
            assert caught.value.__suppress_context__
            assert not client.is_closed
        assert calls == []

    asyncio.run(run())


def test_deeply_nested_json_is_a_private_knowledge_error() -> None:
    assert_failure(
        lambda request: httpx.Response(200, content=b"[" * 100_000),
        "^Knowledge returned invalid JSON$",
    )


def test_unexpected_transport_failure_is_a_private_knowledge_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(QUERY)

    assert_failure(respond, "^Knowledge request failed$")


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"passages": []},
        {"passages": [], "model": 3},
        {"passages": None, "model": "BAAI/bge-m3"},
        {"passages": {}, "model": "BAAI/bge-m3"},
        {"passages": [{"text": QUERY}], "model": "BAAI/bge-m3"},
        {"passages": [{**passage_data(), "score": 2}], "model": "BAAI/bge-m3"},
        {"passages": [{**passage_data(), "kind": "invented"}], "model": "BAAI/bge-m3"},
        {"passages": [], "model": "BAAI/bge-m3", "unexpected": QUERY},
    ],
)
def test_invalid_response_shape_is_a_private_knowledge_error(body: object) -> None:
    assert_failure(lambda request: httpx.Response(200, content=json.dumps(body)), "shape")


def test_more_passages_than_requested_is_invalid() -> None:
    assert_failure(
        lambda request: httpx.Response(
            200,
            json={
                "passages": [passage_data()] * 6,
                "model": "BAAI/bge-m3",
            },
        ),
        "shape",
    )


def assert_failure(
    respond: Callable[[httpx.Request], httpx.Response],
    message: str,
) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(KnowledgeError, match=message) as caught:
                await HttpKnowledge("http://knowledge", client=client).search(QUERY)
            assert QUERY not in str(caught.value)
            assert not client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize("fails", [False, True])
def test_owned_clients_close_after_each_call(
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    made: list[httpx.AsyncClient] = []
    client_type = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        if fails:
            raise httpx.ReadTimeout(QUERY, request=request)
        return httpx.Response(200, json={"passages": [], "model": "BAAI/bge-m3"})

    def make_client() -> httpx.AsyncClient:
        client = client_type(transport=httpx.MockTransport(respond))
        made.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    async def run() -> None:
        api = HttpKnowledge("http://knowledge")
        for _ in range(2):
            if fails:
                with pytest.raises(KnowledgeError):
                    await api.search(QUERY)
            else:
                assert await api.search(QUERY) == ()
        assert len(made) == 2
        assert all(client.is_closed for client in made)

    asyncio.run(run())


def test_no_knowledge_supports_the_full_protocol() -> None:
    api: Knowledge = NoKnowledge()
    assert asyncio.run(api.search(QUERY)) == ()
    assert (
        asyncio.run(
            api.search(
                QUERY,
                k=2,
                subject="Mathematics",
                grade=10,
                kinds=[PassageKind.ATP],
            )
        )
        == ()
    )


@pytest.mark.parametrize("url", [None, "", "http://knowledge/prefix///"])
def test_factory_uses_environment(monkeypatch: pytest.MonkeyPatch, url: str | None) -> None:
    if url is None:
        monkeypatch.delenv("KNOWLEDGE_URL", raising=False)
    else:
        monkeypatch.setenv("KNOWLEDGE_URL", url)
    if not url:
        assert isinstance(knowledge_from_environment(), NoKnowledge)
        return

    bases: list[str] = []

    def make_http(base_url: str) -> Knowledge:
        bases.append(base_url)
        return NoKnowledge()

    monkeypatch.setattr(knowledge, "HttpKnowledge", make_http)
    assert isinstance(knowledge_from_environment(), NoKnowledge)
    assert bases == [url.rstrip("/")]


def test_factory_returns_http_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_URL", "http://knowledge")
    assert isinstance(knowledge_from_environment(), HttpKnowledge)
