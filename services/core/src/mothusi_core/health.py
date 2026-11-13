"""GET /health and /health/live (deployment-aws.md §6.5).

/health/live says only that the process serves requests: it is the load balancer's check, so a
stopped or still-loading model host never makes ECS restart a healthy core. /health is the deep
check `infra/ops.sh` reads: the database and the Ollama model host, and the models it holds.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
import psycopg
from pydantic import BaseModel, ValidationError

TIMEOUT_S = 2.0

type Reach = Literal["ok", "unreachable"]


class _Tag(BaseModel):
    name: str


class _Tags(BaseModel):
    """The part of Ollama's GET /api/tags reply this check reads."""

    models: list[_Tag] = []


async def database() -> Reach:
    """Postgres answers `select 1`; with no POSTGRES_URL the checkpoints are in memory: ok."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        return "ok"
    try:
        async with await psycopg.AsyncConnection.connect(url, connect_timeout=int(TIMEOUT_S)) as c:
            await c.execute("select 1")
    except psycopg.Error, OSError:
        return "unreachable"
    return "ok"


async def ollama_models() -> tuple[Reach, list[str]]:
    """The model host's pulled models (GET /api/tags), when the provider is Ollama."""
    if os.environ.get("MOTHUSI_PROVIDER") != "ollama":
        return "ok", []
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(f"{url}/api/tags")
            response.raise_for_status()
        tags = _Tags.model_validate_json(response.content)
    except httpx.HTTPError, ValidationError:
        return "unreachable", []
    return "ok", sorted(model.name for model in tags.models)


async def report() -> tuple[int, dict[str, Any]]:
    """(HTTP status, body): 200 when everything answers, else 503 with the same body."""
    db = await database()
    ollama, models = await ollama_models()
    status = "ok" if db == "ok" and ollama == "ok" else "degraded"
    body = {"status": status, "db": db, "ollama": ollama, "models": models}
    return (200 if status == "ok" else 503), body
