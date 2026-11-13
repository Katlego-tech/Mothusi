"""The knowledge service's HTTP API (knowledge.md §6.6).

POST /search embeds the query (bge-m3 on CPU, off the event loop) and returns the nearest passages
with their exact page citations. GET /health reports the loaded snapshot. One database connection
is shared under a lock: the service answers a handful of searches per run.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from mothusi_contracts import Contract, Passage, PassageKind
from mothusi_knowledge import store
from mothusi_knowledge.embed import BgeM3Embedder, Embedder

log = logging.getLogger("mothusi.knowledge")


class SearchRequest(Contract):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=20)
    subject: str | None = None
    grade: int | None = Field(default=None, ge=1, le=12)
    kinds: tuple[PassageKind, ...] = ()
    min_score: float = Field(default=0.0, ge=0, le=1)


class SearchResponse(Contract):
    passages: tuple[Passage, ...]
    model: str


class _State:
    def __init__(self, conn: store.Conn, embedder: Embedder) -> None:
        self.conn = conn
        self.embedder = embedder
        self.lock = asyncio.Lock()
        self.snapshot: str | None = None
        self.stale = False


def create_app(database_url: str, embedder: Embedder, snapshot: str | None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        conn = await store.connect(database_url)
        await store.ensure_schema(conn)
        state = _State(conn, embedder)
        current = await store.loaded(conn)
        state.snapshot = current[0] if current else None
        if snapshot:
            try:
                with store.local_snapshot(snapshot) as path:
                    sha, n, fresh = await store.load(conn, path)
                state.snapshot = sha
                log.info("%s %s (%d passages)", "loaded" if fresh else "already loaded", sha, n)
            except Exception as error:  # the service still serves what it has (§4.2 failure paths)
                log.error("snapshot %s not loaded: %s", snapshot, error)
                state.stale = state.snapshot is not None
        app.state.knowledge = state
        try:
            yield
        finally:
            await conn.close()

    app = FastAPI(title="Mothusi knowledge", lifespan=lifespan)

    def unavailable() -> JSONResponse:
        return JSONResponse({"message": "no snapshot is loaded"}, status_code=503)

    @app.post("/search", response_model=None)
    async def search(request: Request, body: SearchRequest) -> SearchResponse | JSONResponse:  # pyright: ignore[reportUnusedFunction]
        state: _State = request.app.state.knowledge
        if state.snapshot is None:
            return unavailable()
        (vector,) = await asyncio.to_thread(state.embedder.embed, [body.query])
        async with state.lock:
            passages = await store.search(
                state.conn,
                vector,
                k=body.k,
                subject=body.subject,
                grade=body.grade,
                kinds=body.kinds,
                min_score=body.min_score,
            )
        return SearchResponse(passages=passages, model=state.embedder.model)

    @app.get("/health", response_model=None)
    async def health(request: Request) -> dict[str, Any] | JSONResponse:  # pyright: ignore[reportUnusedFunction]
        state: _State = request.app.state.knowledge
        if state.snapshot is None:
            return unavailable()
        async with state.lock:
            count = await store.passage_count(state.conn)
        return {"status": "ok", "passages": count, "snapshot": state.snapshot, "stale": state.stale}

    return app


def app_from_environment() -> FastAPI:
    """uvicorn --factory mothusi_knowledge.api:app_from_environment (the container's command)."""
    return create_app(
        os.environ["POSTGRES_URL"], BgeM3Embedder(), os.environ.get("KNOWLEDGE_SNAPSHOT") or None
    )
