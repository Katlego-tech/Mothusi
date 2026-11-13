"""Classes, learner codes and consent (learner-section.md §3, §6.2, §7)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import ClassGroup
from mothusi_core.graph.planner import Planner
from mothusi_core.learners import (
    ClassStore,
    FileClassStore,
    PostgresClassStore,
    make_accounts,
    record_consent,
)
from mothusi_core.main import create_app
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY
from mothusi_llm import ReplayProvider

ROSTER = b"learner\nZanele Mokoena\nAarav Pillay\nThabo Dlamini\n"
NOW = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)


def test_no_save_moves_an_account_backwards() -> None:
    from mothusi_core.learners import may_replace

    assert may_replace("invited", "consented") and may_replace("consented", "active")
    assert not may_replace("consented", "invited") and not may_replace("active", "consented")
    assert may_replace("active", "disabled") and may_replace("disabled", "active")


def test_every_learner_gets_a_fresh_code_even_when_the_rng_repeats() -> None:
    draws = iter([b"\x00" * 5, b"\x00" * 5, b"\x01" * 5, b"\x02" * 5])
    accounts, roster = make_accounts(
        "c1", ["A", "B"], taken={"0000000Z"}, rng=lambda n: next(draws)
    )
    codes = [a.code for a in accounts]
    assert len(set(codes)) == 2 and roster == {codes[0]: "A", codes[1]: "B"}
    assert all(a.state == "invited" and a.consent is None for a in accounts)


def test_consent_moves_invited_to_consented_and_refuses_strangers() -> None:
    accounts, _ = make_accounts("c1", ["A", "B"])
    a, b = accounts
    updated = record_consent(accounts, [a.code], "teacher-1", NOW)
    assert [x.state for x in updated] == ["consented", "invited"]
    assert updated[0].consent is not None and updated[0].consent.recorded_by == "teacher-1"
    again = record_consent(updated, [a.code], "teacher-2", NOW)  # already consented: unchanged
    assert again[0].consent == updated[0].consent
    with pytest.raises(ValueError, match="not in this class: ZZZZZZZZ"):
        record_consent(accounts, [b.code, "ZZZZZZZZ"], "teacher-1", NOW)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as c:
        yield c


def make_class(client: TestClient) -> dict[str, Any]:
    reply = client.post(
        "/classes",
        data={"label": "10B Mathematics", "pack_id": "dbe-caps/mathematics-gr10"},
        files={"roster": ("roster.csv", ROSTER)},
    )
    assert reply.status_code == 201, reply.text
    return reply.json()


def test_a_class_from_a_roster_and_consent_through_the_api(
    client: TestClient, tmp_path: Path
) -> None:
    made = make_class(client)
    learners: list[dict[str, Any]] = made["learners"]
    assert len(learners) == 3
    assert {row["name"] for row in learners} == {"Zanele Mokoena", "Aarav Pillay", "Thabo Dlamini"}
    assert "teacher_sub" not in made
    class_id = made["class_id"]
    assert client.get("/classes").json() == [
        {"class_id": class_id, "pack_id": "dbe-caps/mathematics-gr10", "label": "10B Mathematics"}
    ]
    code = learners[0]["code"]
    after = client.post(f"/classes/{class_id}/consent", json={"codes": [code]}).json()
    assert [row["state"] for row in after["learners"] if row["code"] == code] == ["consented"]
    refused = client.post(f"/classes/{class_id}/consent", json={"codes": ["ZZZZZZZZ"]})
    assert refused.status_code == 422 and "ZZZZZZZZ" in refused.text
    # The name lives only in the roster file; the accounts file holds codes and consent.
    folder = tmp_path / "data/classes" / str(class_id)
    assert "Zanele" not in (folder / "accounts.json").read_text()
    assert "Zanele" in (folder / "roster.json").read_text()


def test_bad_rosters_and_packs_are_named(client: TestClient) -> None:
    form = {"label": "10B", "pack_id": "dbe-caps/mathematics-gr10"}
    bad = client.post("/classes", data=form, files={"roster": ("r.csv", b"name\nA\n")})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "roster"
    dup = client.post("/classes", data=form, files={"roster": ("r.csv", b"learner\nA\nA\n")})
    assert dup.status_code == 422
    pack = client.post(
        "/classes", data={**form, "pack_id": "nope"}, files={"roster": ("r.csv", ROSTER)}
    )
    assert pack.json()["errors"][0]["field"] == "pack_id"


def test_another_teachers_class_is_not_found(client: TestClient, tmp_path: Path) -> None:
    store = FileClassStore(tmp_path / "data")
    accounts, names = make_accounts("class-other00001", ["X"])
    group = ClassGroup(
        class_id="class-other00001", teacher_sub="someone-else", pack_id="p", label="L"
    )
    asyncio.run(store.add_class(group, accounts, names))
    assert client.get("/classes/class-other00001").status_code == 404
    assert (
        client.post(
            "/classes/class-other00001/consent", json={"codes": [accounts[0].code]}
        ).status_code
        == 404
    )
    assert client.get("/classes").json() == []
    assert client.get("/classes/..%2F..%2Fetc").status_code == 404


STORE_DB = os.environ.get("CLASS_TEST_DB")


def stores(tmp_path: Path) -> list[ClassStore]:
    out: list[ClassStore] = [FileClassStore(tmp_path)]
    if STORE_DB:
        with psycopg.connect(STORE_DB, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS learners CASCADE")
        out.append(PostgresClassStore(STORE_DB))
    return out


async def exercise(store: ClassStore) -> None:
    accounts, names = make_accounts("class-store00001", ["Ann", "Ben"])
    group = ClassGroup(class_id="class-store00001", teacher_sub="t1", pack_id="p", label="10B")
    await store.add_class(group, accounts, names)
    assert await store.get_class("class-store00001") == group
    assert await store.classes_of("t1") == [group]
    assert await store.classes_of("t2") == []
    assert await store.names("class-store00001") == names
    updated = record_consent(accounts, [accounts[0].code], "t1", NOW)
    await store.save_accounts(updated)
    saved = {a.code: a for a in await store.accounts("class-store00001")}
    assert saved[accounts[0].code] == updated[0]
    assert saved[accounts[1].code].state == "invited"
    assert await store.get_class("class-missing001") is None
    # Two consent requests read the same snapshot of a class whose learners are both invited, and
    # each records a different learner: neither may undo the other.
    pair, pair_names = make_accounts("class-race000001", ["Cal", "Dee"])
    race = ClassGroup(class_id="class-race000001", teacher_sub="t1", pack_id="p", label="10C")
    await store.add_class(race, pair, pair_names)
    stale = await store.accounts("class-race000001")
    x, y = (a.code for a in stale)
    first = record_consent(stale, [x], "t1", NOW)
    second = record_consent(stale, [y], "t1", NOW)
    await store.save_accounts([a for a, b in zip(first, stale, strict=True) if a != b])
    await store.save_accounts(second)  # the whole stale class, as the first version saved it
    assert {a.code: a.state for a in await store.accounts("class-race000001")} == {
        x: "consented",
        y: "consented",
    }
    await store.delete_class("class-race000001")
    await store.delete_class("class-store00001")
    assert await store.get_class("class-store00001") is None
    assert await store.accounts("class-store00001") == []
    assert await store.names("class-store00001") == {}


def test_both_stores_keep_the_same_class(tmp_path: Path) -> None:
    for store in stores(tmp_path):
        asyncio.run(exercise(store))
    if not STORE_DB:
        pytest.skip("file store checked; CLASS_TEST_DB is not set for the Postgres store")


def test_the_file_store_writes_json_a_person_can_read(tmp_path: Path) -> None:
    store = FileClassStore(tmp_path)
    accounts, names = make_accounts("class-json000001", ["Ann"])
    group = ClassGroup(class_id="class-json000001", teacher_sub="t1", pack_id="p", label="10B")
    asyncio.run(store.add_class(group, accounts, names))
    assert json.loads((tmp_path / "classes/class-json000001/roster.json").read_text()) == names


def test_closing_a_class_deletes_its_learner_data(client: TestClient, tmp_path: Path) -> None:
    made = make_class(client)
    class_id = made["class_id"]
    assert client.delete(f"/classes/{class_id}").status_code == 204
    assert client.get(f"/classes/{class_id}").status_code == 404
    assert not (tmp_path / "data/classes" / str(class_id)).exists()
    assert client.delete(f"/classes/{class_id}").status_code == 404
