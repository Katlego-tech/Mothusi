"""GET /n/{token}, the learner's note page (outputs-and-delivery.md §6.2)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import LearnerNote
from mothusi_core.graph.planner import Planner
from mothusi_core.main import create_app
from mothusi_delivery import note_link, note_page
from mothusi_delivery.store import LocalBundleStore

from .conftest import TEST_NOTE_KEY

NOTE = LearnerNote(
    pseudonym="L-007",
    outcome="Solving linear equations",
    mark_with_quote="Q3.2, 2 of 3: you wrote 2x = 16",
    next_task="Redo Q3.3 dividing both sides",
    self_assessment_prompt="Which step would you check first?",
)


class Silent:
    name = "silent"

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no model is called")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    planner = Planner(
        primary=Silent(), fallback=None, data_dir=tmp_path, checkpointer=InMemorySaver()
    )
    expires = datetime.now(UTC) + timedelta(days=14)
    store = LocalBundleStore(tmp_path / "published")
    page = note_page(NOTE, "Grade 10 Mathematics · Term 1 Test", expires)
    store.put(
        "a1/bundle/note-pages/L-007.json", page.model_dump_json().encode(), "application/json"
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as c:
        yield c


def token(days: float, pseudonym: str = "L-007") -> str:
    at = datetime.now(UTC) + timedelta(days=days)
    return note_link(TEST_NOTE_KEY.encode(), "a1", pseudonym, at)


def test_a_valid_link_opens_the_note_with_no_name_and_no_caching(client: TestClient) -> None:
    response = client.get(f"/n/{token(14)}")
    assert response.status_code == 200 and "Solving linear equations" in response.text
    assert "L-007" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["x-robots-tag"] == "noindex"


def test_a_forged_or_unknown_link_is_one_fixed_404(client: TestClient) -> None:
    good = token(14)
    flipped = good[:-1] + ("A" if good[-1] != "A" else "B")
    bodies = {client.get(f"/n/{t}").text for t in (flipped, "nonsense", token(14, "L-999"))}
    assert len(bodies) == 1
    assert client.get(f"/n/{flipped}").status_code == 404


def test_an_expired_link_is_410(client: TestClient) -> None:
    response = client.get(f"/n/{token(-1)}")
    assert response.status_code == 410 and "expired" in response.text
