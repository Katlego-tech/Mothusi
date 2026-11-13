"""Practice tests set from an ATP week, checked by code, sat blind, approved once."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Contract, Passage, PassageKind
from mothusi_core.graph.planner import Planner
from mothusi_core.main import create_app
from mothusi_core.practice import atp_weeks_for
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY
from mothusi_llm import ProviderError, ReplayProvider, Usage

WEEK_5 = "ATP 2023-24 Gr 10 Mathematics (DBE), PDF p 1, Term 1 week 5"
CITED = "ATP 2023-24 Gr 10 Mathematics (DBE), PDF p 1"


class Shelf:
    """A knowledge service holding one ATP passage (or none)."""

    def __init__(self, empty: bool = False) -> None:
        self.empty = empty

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        subject: str | None = None,
        grade: int | None = None,
        kinds: Sequence[PassageKind] = (),
    ) -> tuple[Passage, ...]:
        if self.empty:
            return ()
        return (
            Passage(
                passage_id="atp-p1",
                source="ATP 2023-24 Gr 10 Mathematics (DBE)",
                source_key="atp.pdf",
                kind=PassageKind.ATP,
                subject="Mathematics",
                grade=10,
                page=1,
                locator="PDF p 1",
                text="Term 1 weeks 5-8: Exponents, equations and inequalities",
                score=0.9,
            ),
        )


LEVELS = (
    ("knowledge", 4),
    ("routine_procedures", 7),
    ("complex_procedures", 6),
    ("problem_solving", 3),
)


def items(citation: str = CITED, levels: Sequence[tuple[str, int]] = LEVELS) -> dict[str, Any]:
    rows = [
        {
            "question_id": str(n),
            "question": f"Solve for x: 2^x = {2**n}",
            "max_marks": marks,
            "answer": f"x = {n}",
            "marking_notes": "",
            "level": level,
            "citation": citation,
        }
        for n, (level, marks) in enumerate(levels, start=1)
    ]
    return {"items": rows}


def answers(wrong: str | None = None) -> dict[str, Any]:
    return {
        "answers": [
            {
                "question_id": str(n),
                "answer": wrong if (wrong and n == 1) else f"x = {n}",
                "working": "",
            }
            for n in range(1, 5)
        ]
    }


class Setter:
    """Replies from a queue per output contract; a ProviderError in a queue is raised."""

    name = "fake-setter"

    def __init__(self, replies: dict[str, list[Any]]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.prompts.append(user)
        reply = self.replies[output.__name__].pop(0)
        if isinstance(reply, ProviderError):
            raise reply
        return output.model_validate(reply), Usage(
            model=self.name, input_tokens=1, output_tokens=1, cost_zar=Decimal(0)
        )


def client_for(
    tmp_path: Path, setter: Setter, monkeypatch: pytest.MonkeyPatch, shelf: Shelf | None = None
) -> Iterator[TestClient]:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        roles={"text": setter},
        data_dir=tmp_path / "data",
        checkpointer=InMemorySaver(),
        knowledge=shelf or Shelf(),
    )
    with TestClient(create_app(planner, auth_from_environment=False)) as c:
        yield c


def a_class(client: TestClient) -> str:
    made = client.post(
        "/classes",
        data={"label": "10B", "pack_id": "dbe-caps/mathematics-gr10"},
        files={"roster": ("r.csv", b"learner\nAnn\nBen\n")},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["class_id"])


def set_and_wait(client: TestClient, class_id: str, week: int = 5) -> dict[str, Any]:
    started = client.post(
        f"/classes/{class_id}/practice-tests", json={"atp_week": week, "marks": 20}
    )
    assert started.status_code == 202, started.text
    test_id = started.json()["test_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = client.get(f"/practice-tests/{test_id}").json()
        if record["state"] != "drafting":
            return record
        time.sleep(0.02)
    raise AssertionError("the practice test was never set")


def test_a_clean_test_is_checked_approved_opened_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = Setter({"PracticeReply": [items()], "SetterReply": [answers()]})
    for client in client_for(tmp_path, setter, monkeypatch):
        class_id = a_class(client)
        record = set_and_wait(client, class_id)
        assert record["state"] == "checked" and record["problems"] == [], record
        assert record["test"]["atp_week"]["source"] == WEEK_5
        assert "Exponents, equations and inequalities" in setter.prompts[0]
        test_id = record["test_id"]
        assert client.post(f"/practice-tests/{test_id}/open").status_code == 409  # not approved
        approved = client.post(f"/practice-tests/{test_id}/approve").json()
        assert approved["test"]["approved_by"] == "local" and approved["test"]["approved_at"]
        assert client.post(f"/practice-tests/{test_id}/open").json()["state"] == "open"
        assert client.post(f"/practice-tests/{test_id}/close").json()["state"] == "closed"
        assert client.post(f"/practice-tests/{test_id}/open").json()["state"] == "open"
        assert client.post(f"/practice-tests/{test_id}/approve").status_code == 409
        listed = client.get(f"/classes/{class_id}/practice-tests").json()
        assert [r["test_id"] for r in listed] == [test_id]


def test_an_invented_citation_is_sent_back_and_the_fix_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = Setter({"PracticeReply": [items("CAPS p 999"), items()], "SetterReply": [answers()]})
    for client in client_for(tmp_path, setter, monkeypatch):
        record = set_and_wait(client, a_class(client))
        assert record["state"] == "checked" and record["problems"] == []
        assert "citation not a listed passage" in setter.prompts[1]


def test_a_memo_the_setter_cannot_reproduce_cannot_be_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = Setter({"PracticeReply": [items()], "SetterReply": [answers(wrong="x = 7")]})
    for client in client_for(tmp_path, setter, monkeypatch):
        record = set_and_wait(client, a_class(client))
        assert record["state"] == "checked"
        assert any(p.startswith("1: the Setter's answer 'x = 7'") for p in record["problems"])
        refused = client.post(f"/practice-tests/{record['test_id']}/approve")
        assert refused.status_code == 409 and "x = 7" in refused.text


def test_a_test_off_the_cognitive_grid_cannot_be_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lopsided = (
        ("knowledge", 17),
        ("routine_procedures", 1),
        ("complex_procedures", 1),
        ("problem_solving", 1),
    )
    setter = Setter({"PracticeReply": [items(levels=lopsided)], "SetterReply": [answers()]})
    for client in client_for(tmp_path, setter, monkeypatch):
        record = set_and_wait(client, a_class(client))
        assert any(p.startswith("cognitive grid:") for p in record["problems"])
        assert client.post(f"/practice-tests/{record['test_id']}/approve").status_code == 409


def test_a_setter_that_fails_every_time_refuses_the_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = ProviderError("not JSON", retryable=True)
    setter = Setter({"PracticeReply": [bad, bad, bad], "SetterReply": []})
    for client in client_for(tmp_path, setter, monkeypatch):
        record = set_and_wait(client, a_class(client))
        assert record["state"] == "refused" and "could not set it" in record["message"]


def test_weeks_and_classes_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for client in client_for(tmp_path, Setter({}), monkeypatch):
        class_id = a_class(client)
        late = client.post(f"/classes/{class_id}/practice-tests", json={"atp_week": 50})
        assert late.status_code == 422 and "not in the ATP (1-43)" in late.text
        assert (
            client.post(
                "/classes/class-nope000001/practice-tests", json={"atp_week": 5}
            ).status_code
            == 404
        )
        assert client.get("/practice-tests/practice-nope").status_code == 404
    with pytest.raises(ValueError, match="no ATP weeks are held for dbe-caps/nope"):
        atp_weeks_for("dbe-caps/nope")


def test_the_postgres_store_keeps_tests_and_drops_them_with_the_class() -> None:
    import asyncio
    import os

    import psycopg

    from mothusi_contracts import AtpWeek, ClassGroup
    from mothusi_core.learners import PostgresClassStore, make_accounts
    from mothusi_core.practice import PostgresPracticeStore, PracticeRecord

    url = os.environ.get("CLASS_TEST_DB")
    if not url:
        pytest.skip("CLASS_TEST_DB is not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS learners CASCADE")

    async def go() -> None:
        classes, tests = PostgresClassStore(url), PostgresPracticeStore(url)
        accounts, names = make_accounts("class-pgprac0001", ["Ann"])
        group = ClassGroup(class_id="class-pgprac0001", teacher_sub="t", pack_id="p", label="10B")
        await classes.add_class(group, accounts, names)
        week = AtpWeek(week=5, topic="Exponents", source=WEEK_5)
        record = PracticeRecord(
            test_id="practice-pg00000001",
            class_id=group.class_id,
            atp_week=week,
            marks=20,
            state="drafting",
        )
        await tests.save(record)
        await tests.save(record.model_copy(update={"state": "refused", "message": "m"}))
        assert (await tests.get("practice-pg00000001")).state == "refused"  # type: ignore[union-attr]
        assert [r.test_id for r in await tests.of_class(group.class_id)] == ["practice-pg00000001"]
        await classes.delete_class(group.class_id)
        assert await tests.get("practice-pg00000001") is None  # ON DELETE CASCADE

    asyncio.run(go())


def test_without_retrieved_passages_no_test_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = Setter({"PracticeReply": [items()], "SetterReply": [answers()]})
    for client in client_for(tmp_path, setter, monkeypatch, Shelf(empty=True)):
        record = set_and_wait(client, a_class(client))
        assert record["state"] == "refused"
        assert "no curriculum passages were retrieved" in record["message"]
        assert setter.prompts == []  # no model call: nothing to ground a question in


def test_answers_code_cannot_compare_are_kept_for_the_teacher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prose = items()
    prose["items"][0]["answer"] = "The exponents are equal because the bases are equal"
    sat = answers(wrong="Equal bases, so the exponents match")
    setter = Setter({"PracticeReply": [prose], "SetterReply": [sat]})
    for client in client_for(tmp_path, setter, monkeypatch):
        record = set_and_wait(client, a_class(client))
        assert record["problems"] == []
        assert record["unverified"] and record["unverified"][0].startswith("1: the Setter wrote")
        assert client.post(f"/practice-tests/{record['test_id']}/approve").status_code == 200


def test_a_test_finished_after_its_class_closed_is_dropped(tmp_path: Path) -> None:
    import asyncio

    from mothusi_contracts import AtpWeek
    from mothusi_core.practice import ClassGoneError, FilePracticeStore, PracticeRecord

    store = FilePracticeStore(tmp_path)
    record = PracticeRecord(
        test_id="practice-gone000001",
        class_id="class-gone00000001",
        atp_week=AtpWeek(week=5, topic="Exponents", source=WEEK_5),
        marks=20,
        state="checked",
    )
    with pytest.raises(ClassGoneError):
        asyncio.run(store.save(record))
    assert not (tmp_path / "classes/class-gone00000001").exists()  # never recreated
