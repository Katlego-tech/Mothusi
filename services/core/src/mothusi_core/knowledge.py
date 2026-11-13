"""The core's knowledge client (knowledge.md §6.6-6.7)."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import ValidationError

from mothusi_contracts import Contract, Passage, PassageKind


class Knowledge(Protocol):
    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        subject: str | None = None,
        grade: int | None = None,
        kinds: Sequence[PassageKind] = (),
    ) -> tuple[Passage, ...]: ...


class KnowledgeError(Exception):
    """A knowledge request failed; the message never contains the query or response body."""


class _SearchResponse(Contract):
    passages: tuple[Passage, ...]
    model: str


class HttpKnowledge:
    """POST searches to the knowledge service and validate its response.

    An injected client remains owned by the caller and is never closed here. Without one,
    each search creates an AsyncClient and closes it before returning or raising.
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 5,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/search"
        self._timeout_s = timeout_s
        self._client = client

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        subject: str | None = None,
        grade: int | None = None,
        kinds: Sequence[PassageKind] = (),
    ) -> tuple[Passage, ...]:
        try:
            body: dict[str, object] = {
                "query": query,
                "k": k,
                "subject": subject,
                "grade": grade,
                "kinds": [kind.value for kind in kinds],
                "min_score": 0.0,
            }
            try:
                if self._client is not None:
                    response = await self._post(self._client, body)
                else:
                    async with httpx.AsyncClient() as client:
                        response = await self._post(client, body)
            except httpx.TimeoutException:
                raise KnowledgeError("Knowledge request timeout") from None
            except httpx.RequestError:
                raise KnowledgeError("Knowledge connection error") from None

            if response.status_code != 200:
                raise KnowledgeError(f"Knowledge returned non-200 status {response.status_code}")
            try:
                payload = json.loads(response.content)
            except ValueError, UnicodeError:
                raise KnowledgeError("Knowledge returned invalid JSON") from None
            try:
                result = _SearchResponse.model_validate(payload)
            except ValidationError:
                raise KnowledgeError("Knowledge returned an invalid response shape") from None
            if len(result.passages) > k:
                raise KnowledgeError(
                    "Knowledge returned an invalid response shape: too many passages"
                )
            return result.passages
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError("Knowledge request failed") from None

    async def _post(self, client: httpx.AsyncClient, body: dict[str, object]) -> httpx.Response:
        return await client.post(
            self._url, json=body, timeout=self._timeout_s, follow_redirects=False
        )


class NoKnowledge:
    """Knowledge is unconfigured: no passages are available."""

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        subject: str | None = None,
        grade: int | None = None,
        kinds: Sequence[PassageKind] = (),
    ) -> tuple[Passage, ...]:
        return ()


def knowledge_from_environment() -> Knowledge:
    """Use KNOWLEDGE_URL when non-empty; otherwise run without retrieved passages."""
    base_url = os.environ.get("KNOWLEDGE_URL")
    return HttpKnowledge(base_url.rstrip("/")) if base_url else NoKnowledge()
