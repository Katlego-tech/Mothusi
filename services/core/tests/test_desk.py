"""The desk reads sourced ATP rows and event metadata, never learner bundles."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import EventKind
from mothusi_core.events import EventSink
from mothusi_core.graph.planner import Planner, new_run
from mothusi_core.main import create_app
from mothusi_core.providers import free
from mothusi_core.smoke import FIXTURE, PLANTED, REPLAY
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import ReplayProvider

from .conftest import ROOT


@pytest.fixture
def desk(tmp_path: Path) -> Iterator[tuple[TestClient, Planner]]:
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as client:
        yield client, planner


def seed(planner: Planner, aid: str, day: int = 1) -> EventSink:
    planner.clock = lambda: datetime(2026, 11, day, tzinfo=UTC)
    all_scripts, _ = load_scripts(FIXTURE / "scripts.csv")
    scripts = tuple(s for s in all_scripts if s.script_id in PLANTED)
    new_run(
        planner,
        assessment_id=aid,
        goal="Mark the test",
        memo=load_memo(FIXTURE / "memo.toml"),
        scripts=scripts,
        names={s.script_id: "Private Learner" for s in scripts},
        pack_path=ROOT / "packs/dbe-caps/mathematics-gr10.toml",
        poa_task="Test",
        budget_zar=Decimal("40"),
    )
    return planner.sink(aid)


def test_list_metadata_newest_first_without_bundle_or_names(
    desk: tuple[TestClient, Planner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, planner = desk
    assert client.get("/assessments").json() == []
    old = seed(planner, "old")
    old.emit(EventKind.STATE, "marking", {"to": "marking"})
    old.emit(EventKind.NEEDS_TEACHER, "flag", {"topic": "script", "script_id": "L-001"})
    recent = seed(planner, "recent", 2)
    recent.emit(EventKind.STATE, "approval", {"to": "awaiting_approval"})
    recent.decision("halt", "delivery unavailable")
    bundle = planner.data_dir / "recent/bundle"
    bundle.mkdir()
    (bundle / "names.json").write_text('{"learner": "Private Learner"}')

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("desk must not read checkpoints or bundles")

    monkeypatch.setattr(planner, "snapshot", forbidden)
    response = client.get("/assessments")
    assert response.status_code == 200
    rows = response.json()
    assert [r["assessment_id"] for r in rows] == ["recent", "old"]
    assert rows[0] == {
        "assessment_id": "recent",
        "title": "Grade 10 Mathematics · Term 1 Test",
        "state": "awaiting_approval",
        "waiting": False,
        "halted": "delivery unavailable",
        "started_at": "2026-11-02T00:00:00+00:00",
        "scripts": 6,
        "subject": "Mathematics",
        "grade": 10,
    }
    assert rows[1]["state"] == "marking" and rows[1]["waiting"] is True
    assert "Private Learner" not in response.text and "learner" not in response.text
    assert "budget" not in response.text


def test_waiting_tracks_matching_answers_and_state_progress(
    desk: tuple[TestClient, Planner],
) -> None:
    client, planner = desk
    sink = seed(planner, "questions")

    def waiting() -> bool:
        return bool(client.get("/assessments").json()[0]["waiting"])

    assert not waiting()  # no state event yet means draft
    assert client.get("/assessments").json()[0]["state"] == "draft"
    sink.emit(EventKind.STATE, "memo", {"to": "memo_check"})
    sink.emit(EventKind.NEEDS_TEACHER, "memo", {"topic": "memo", "question_id": "2.3"})
    assert waiting()
    sink.emit(EventKind.TEACHER, "other item", {"topic": "memo", "question_id": "1"})
    assert waiting()
    sink.emit(EventKind.TEACHER, "answered", {"topic": "memo", "question_id": "2.3"})
    assert not waiting()
    sink.emit(EventKind.NEEDS_TEACHER, "budget", {"topic": "budget"})
    assert waiting()
    sink.emit(EventKind.STATE, "marking", {"to": "marking"})
    assert not waiting()
    for sid in ("L-001", "L-002"):
        sink.emit(
            EventKind.NEEDS_TEACHER,
            "flag",
            {"topic": "script", "script_id": sid, "question_id": "2.3"},
        )
    sink.emit(EventKind.STATE, "moderating", {"to": "moderating"})
    assert waiting()
    sink.emit(EventKind.TEACHER, "fixed", {"topic": "script", "script_id": "L-001"})
    assert waiting()
    sink.emit(EventKind.TEACHER, "fixed", {"topic": "script", "script_id": "L-002"})
    assert not waiting()


def test_older_created_event_uses_pack_metadata(desk: tuple[TestClient, Planner]) -> None:
    client, planner = desk
    sink = planner.sink("legacy")
    sink.emit(
        EventKind.CREATED,
        "created",
        {
            "title": "Older test",
            "pack_id": "dbe-caps/mathematics-gr10",
            "script_ids": ["L-001"],
        },
    )
    row = client.get("/assessments").json()[0]
    assert row["subject"] == "Mathematics" and row["grade"] == 10 and row["scripts"] == 1


@pytest.mark.parametrize("subject", ["Mathematics", "Accounting", "MATHEMATICS"])
@pytest.mark.parametrize("pack_id", ["dbe-caps", "dbe-caps/mathematics-gr10"])
def test_atp_all_sourced_rows_and_term_boundaries(
    desk: tuple[TestClient, Planner],
    subject: str,
    pack_id: str,
) -> None:
    client, _ = desk
    response = client.get(f"/packs/{pack_id}/atp", params={"subject": subject})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 43
    for week, row in enumerate(rows, 1):
        assert row["week"] == week
        assert row["term"] == (week - 1) // 11 + 1
        assert row["term_week"] == (week - 1) % 11 + 1
        assert row["source"] and row["topic"]


def test_missing_atp(desk: tuple[TestClient, Planner]) -> None:
    client, _ = desk
    for pack, subject in [("dbe-caps", "Geography"), ("absent", "Mathematics")]:
        response = client.get(f"/packs/{pack}/atp", params={"subject": subject})
        assert response.status_code == 404 and set(response.json()) == {"message"}


def test_desk_requires_same_teacher_auth(tmp_path: Path) -> None:
    class Reject:
        def verify(self, token: str) -> dict[str, Any]:
            raise jwt.InvalidTokenError("invalid")

    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, verifier=Reject())) as client:
        for endpoint in ("/assessments", "/packs/dbe-caps/atp?subject=Mathematics"):
            assert client.get(endpoint).status_code == 401
            assert client.get(endpoint, headers={"Authorization": "Bearer bad"}).status_code == 401
