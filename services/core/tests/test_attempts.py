"""A learner takes an open practice test and sees exactly §6.3 (learner-section.md §4.2)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mothusi_llm import ProviderError

from .test_practice import Setter, a_class, answers, client_for, items, set_and_wait

RESULT_KEYS = {"test_title", "total", "max_total", "questions"}
QUESTION_KEYS = {"question_id", "question", "awarded", "max_marks", "evidence", "worked_answer"}


def marks(full: bool = True) -> dict[str, Any]:
    """The Marker's reply for answers "x = 1".."x = 4" (questions worth 4, 7, 6, 3)."""
    rows: list[dict[str, Any]] = []
    for n, max_marks in enumerate((4, 7, 6, 3), start=1):
        awarded = max_marks if (full or n != 1) else 0
        rows.append(
            {
                "question_id": str(n),
                "awarded": awarded,
                "max_marks": max_marks,
                "evidence_quote": f"x = {n}" if awarded else "",
                "memo_line": f"x = {n}",
                "principle": "method marks",
            }
        )
    return {"marks": rows}


@pytest.fixture
def world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Setter, dict[str, Any]]]:
    setter = Setter({"PracticeReply": [items()], "SetterReply": [answers()], "MarkerReply": []})
    for client in client_for(tmp_path, setter, monkeypatch):
        class_id = a_class(client)
        learners = client.get(f"/classes/{class_id}").json()["learners"]
        ann = next(row["code"] for row in learners if row["name"] == "Ann")
        ben = next(row["code"] for row in learners if row["name"] == "Ben")
        client.post(f"/classes/{class_id}/consent", json={"codes": [ann]})
        record = set_and_wait(client, class_id)
        test_id = record["test_id"]
        client.post(f"/practice-tests/{test_id}/approve")
        client.post(f"/practice-tests/{test_id}/open")
        yield (
            client,
            setter,
            {"class_id": class_id, "ann": ann, "ben": ben, "test_id": test_id, "tmp": tmp_path},
        )


def typed(test_id: str, first: str = "x = 1") -> dict[str, Any]:
    texts = [first, "x = 2", "x = 3", "x = 4"]
    return {
        "test_id": test_id,
        "answers": [{"question_id": str(n), "text": t} for n, t in enumerate(texts, 1)],
    }


def test_who_may_take_a_test(world: tuple[TestClient, Setter, dict[str, Any]]) -> None:
    client, _, w = world
    assert client.get("/learn/tests").status_code == 401
    assert client.get("/learn/tests", headers={"X-Learner-Code": "ZZZZZZZZ"}).status_code == 401
    assert (
        client.get("/learn/tests", headers={"X-Learner-Code": w["ben"]}).status_code == 403
    )  # no consent
    listed = client.get("/learn/tests", headers={"X-Learner-Code": w["ann"].lower()}).json()
    assert [t["test_id"] for t in listed] == [w["test_id"]]


def test_the_questions_never_carry_the_memo(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, _, w = world
    paper = client.get(f"/learn/tests/{w['test_id']}", headers={"X-Learner-Code": w["ann"]}).json()
    assert [q["question_id"] for q in paper["questions"]] == ["1", "2", "3", "4"]
    assert all(set(q) == {"question_id", "question", "max_marks"} for q in paper["questions"])
    assert all(v != "x = 1" for q in paper["questions"] for v in q.values())  # no memo answer


def test_an_attempt_returns_exactly_the_checked_result(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, setter, w = world
    setter.replies["MarkerReply"].append(marks(full=False))
    reply = client.post(
        "/learn/attempts",
        json=typed(w["test_id"], first="x = 9"),
        headers={"X-Learner-Code": w["ann"]},
    )
    assert reply.status_code == 200, reply.text
    result = reply.json()
    assert set(result) == RESULT_KEYS and all(set(q) == QUESTION_KEYS for q in result["questions"])
    assert (result["total"], result["max_total"]) == (16, 20)
    first, second = result["questions"][0], result["questions"][1]
    assert (first["awarded"], first["evidence"], first["worked_answer"]) == (0, [], "x = 1")
    assert (second["awarded"], second["evidence"]) == (7, ["x = 2"])
    assert "method marks" not in reply.text  # the Marker's principle never reaches a learner
    mine = client.get("/learn/attempts", headers={"X-Learner-Code": w["ann"]}).json()
    assert [a["state"] for a in mine] == ["marked"] and mine[0]["result"] == result


def test_a_classmates_name_never_reaches_the_marker(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, setter, w = world
    setter.replies["MarkerReply"].append(marks())
    body = typed(w["test_id"])
    body["answers"][1]["text"] = "Ben said x = 2"
    client.post("/learn/attempts", json=body, headers={"X-Learner-Code": w["ann"]})
    marker_prompt = setter.prompts[-1]
    assert "Ben" not in marker_prompt and w["ben"] in marker_prompt


def test_a_marker_that_cannot_mark_leaves_it_for_the_teacher(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, setter, w = world
    bad = ProviderError("not JSON", retryable=True)
    setter.replies["MarkerReply"].extend([bad, bad, bad])
    reply = client.post(
        "/learn/attempts", json=typed(w["test_id"]), headers={"X-Learner-Code": w["ann"]}
    )
    assert reply.status_code == 202 and reply.json()["state"] == "needs_teacher"
    progress = client.get(f"/practice-tests/{w['test_id']}/progress").json()
    assert progress["rows"] == [
        {"code": w["ann"], "name": "Ann", "best": None, "last": None, "tries": 1, "waiting": 1}
    ]


def test_progress_closed_tests_and_closing_the_class(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, setter, w = world
    setter.replies["MarkerReply"].append(marks())
    client.post("/learn/attempts", json=typed(w["test_id"]), headers={"X-Learner-Code": w["ann"]})
    progress = client.get(f"/practice-tests/{w['test_id']}/progress").json()
    assert progress["attempted"] == 1 and progress["learners"] == 2
    assert progress["questions"][0] == {"question_id": "1", "max_marks": 4, "mean": 4.0}
    assert progress["rows"][0]["best"] == 20
    client.post(f"/practice-tests/{w['test_id']}/close")
    closed = client.post(
        "/learn/attempts", json=typed(w["test_id"]), headers={"X-Learner-Code": w["ann"]}
    )
    assert closed.status_code == 409
    unknown = typed(w["test_id"])
    unknown["answers"].append({"question_id": "9", "text": "x"})
    client.post(f"/practice-tests/{w['test_id']}/open")
    assert (
        client.post(
            "/learn/attempts", json=unknown, headers={"X-Learner-Code": w["ann"]}
        ).status_code
        == 422
    )
    assert client.delete(f"/classes/{w['class_id']}").status_code == 204
    assert not (w["tmp"] / "data/classes" / w["class_id"]).exists()  # attempts went with it
    assert client.get("/learn/tests", headers={"X-Learner-Code": w["ann"]}).status_code == 401


def test_with_sign_in_on_the_header_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from mothusi_core.graph.planner import Planner
    from mothusi_core.main import create_app
    from mothusi_core.providers import free
    from mothusi_core.smoke import REPLAY
    from mothusi_llm import ReplayProvider

    class Teachers:
        def verify(self, token: str) -> dict[str, Any]:
            return {"sub": "teacher-1", "token_use": "id"}

    monkeypatch.delenv("POSTGRES_URL", raising=False)
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    with TestClient(create_app(planner, verifier=Teachers())) as c:  # type: ignore[arg-type]
        headers = {"Authorization": "Bearer t", "X-Learner-Code": "ABCDEFGH"}
        refused = c.get("/learn/tests", headers=headers)
        assert refused.status_code == 401 and "learner sign-in" in refused.text


def test_the_postgres_attempt_store_and_its_cascade() -> None:
    import asyncio
    import os

    import psycopg

    from mothusi_contracts import Answer, Attempt, ClassGroup
    from mothusi_core.learners import PostgresClassStore, make_accounts
    from mothusi_core.practice import ClassGoneError, MarkingReview, PostgresAttemptStore

    url = os.environ.get("CLASS_TEST_DB")
    if not url:
        pytest.skip("CLASS_TEST_DB is not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS learners CASCADE")

    async def go() -> None:
        classes, attempts = PostgresClassStore(url), PostgresAttemptStore(url)
        accounts, names = make_accounts("class-pgatt00001", ["Ann"])
        group = ClassGroup(class_id="class-pgatt00001", teacher_sub="t", pack_id="p", label="10B")
        await classes.add_class(group, accounts, names)
        code = accounts[0].code
        attempt = Attempt(
            attempt_id="attempt-pg0000000001",
            test_id="practice-x",
            code=code,
            answers=(Answer(question_id="1", text="x = 1"),),
            state="needs_teacher",
        )
        await attempts.save(group.class_id, attempt)
        assert [a.attempt_id for a in await attempts.of_test(group.class_id, "practice-x")] == [
            "attempt-pg0000000001"
        ]
        assert len(await attempts.of_learner(group.class_id, code)) == 1
        review = MarkingReview(
            attempt_id=attempt.attempt_id, reason="r", passes=({"status": "retry"},)
        )
        await attempts.save_review(group.class_id, review)
        assert await attempts.review(group.class_id, attempt.attempt_id) == review
        await classes.delete_class(group.class_id)
        assert await attempts.of_test(group.class_id, "practice-x") == []  # ON DELETE CASCADE
        with pytest.raises(ClassGoneError):
            await attempts.save(group.class_id, attempt)

    asyncio.run(go())


def test_last_is_the_latest_attempt(world: tuple[TestClient, Setter, dict[str, Any]]) -> None:
    client, setter, w = world
    setter.replies["MarkerReply"].extend([marks(full=True), marks(full=False)])
    headers = {"X-Learner-Code": w["ann"]}
    client.post("/learn/attempts", json=typed(w["test_id"]), headers=headers)
    client.post("/learn/attempts", json=typed(w["test_id"], first="x = 9"), headers=headers)
    row = client.get(f"/practice-tests/{w['test_id']}/progress").json()["rows"][0]
    assert (row["best"], row["last"], row["tries"]) == (20, 16, 2)
    history = client.get("/learn/attempts", headers=headers).json()
    assert [a["result"]["total"] for a in history] == [20, 16]


def test_a_failed_marking_keeps_its_passes_for_the_teacher_only(
    world: tuple[TestClient, Setter, dict[str, Any]],
) -> None:
    client, setter, w = world
    bad = ProviderError("not JSON", retryable=True)
    setter.replies["MarkerReply"].extend([bad, bad, bad])
    headers = {"X-Learner-Code": w["ann"]}
    attempt_id = client.post("/learn/attempts", json=typed(w["test_id"]), headers=headers).json()[
        "attempt_id"
    ]
    review = client.get(f"/practice-tests/{w['test_id']}/attempts/{attempt_id}/review").json()
    assert review["attempt_id"] == attempt_id and len(review["passes"]) == 3
    assert all("not JSON" in (p["reason"] or "") for p in review["passes"])
    learner_view = client.get("/learn/attempts", headers=headers).text
    assert "passes" not in learner_view and "not JSON" not in learner_view
    missing = client.get(f"/practice-tests/{w['test_id']}/attempts/attempt-nope/review")
    assert missing.status_code == 404
