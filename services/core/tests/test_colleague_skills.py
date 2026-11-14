"""The request catalogue, grounded in real packs and the planted replay snapshot."""

import asyncio
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mothusi_contracts import EventKind
from mothusi_core.colleague import Intent, Lesson, answer, compose, match_rules, respond
from mothusi_core.main import create_app

from .conftest import ROOT
from .test_colleague import planted as planted


@pytest.mark.parametrize(
    "text,skill",
    [
        ("Give me the stats for this run", "run_report"),
        ("Which questions did the class struggle with?", "run_report"),
        ("Who needs catch-up?", "run_report"),
        ("What does L-001's mark count towards the year?", "run_report"),
        ("Where did you get stuck?", "run_walls"),
        ("What went wrong?", "run_walls"),
        ("What does the memo give for 2.3?", "memo_item"),
        ("Did you find anything wrong in my memo?", "memo_item"),
        ("How many marks is question 3?", "memo_item"),
        ("How many marks is the whole test?", "memo_item"),
        ("How much does this test count?", "poa"),
        ("What is the PoA for term 2?", "poa"),
        ("How many scripts must be moderated?", "poa"),
        ("What should I teach in term 2 week 3?", "atp"),
        ("When is trigonometry taught?", "atp"),
        ("What does CAPS say about functions?", "curriculum"),
        ("Prepare a lesson plan for week 6", "lesson_plan"),
        ("Change L-014's mark to 5", "cannot"),
        ("Approve the set", "cannot"),
        ("Email the parents", "cannot"),
        ("Contact a learner", "cannot"),
        ("Make report cards", "cannot"),
        ("SA-SAMS export", "cannot"),
        ("Teach grade 11 maths", "cannot"),
        ("Teach geography", "cannot"),
    ],
)
def test_catalogue_routes(text: str, skill: str) -> None:
    intent = match_rules(text)
    assert intent is not None and intent.skill == skill


def test_reports(planted: tuple[Any, Any]) -> None:
    _, state = planted
    say = compose(Intent(skill="run_report"), state, set(), set())["say"]
    raw = [m["raw"] for m in state["converted"].values()]
    assert f"{sum(raw) / len(raw):.2f}" in say
    assert f"highest {max(raw)}" in say and f"lowest {min(raw)}" in say
    assert "Final" in say and f"{len(raw)} scripts" in say
    for stat in state["item_stats"]:
        assert f"{stat['question_id']}: {float(stat['mean_awarded']):.2f}" in say
    early = deepcopy(state)
    early.update(state="marking", moderation_closed=False)
    early.pop("converted")
    early.pop("item_stats")
    assert "Provisional" in compose(Intent(skill="run_report"), early, set(), set())["say"]
    for topic, ready in [("items", "analysing"), ("catch_up", "writing")]:
        early.pop("catch_up_plan", None)
        assert ready in compose(Intent(skill="run_report", view=topic), early, set(), set())["say"]
    weak = compose(Intent(skill="run_report", view="items"), state, set(), set())["say"]
    for row in sorted(state["item_stats"], key=lambda x: float(x["facility_percent"]))[:3]:
        assert row["question_id"] in weak
    catch = compose(Intent(skill="run_report", view="catch_up"), state, set(), set())["say"]
    for row in state["catch_up_plan"]:
        assert row["topic"] in catch and row["activity"] in catch
    year = compose(Intent(skill="run_report", script_id="L-001", view="year"), state, set(), set())[
        "say"
    ]
    assert str(state["converted"]["L-001"]["year_points"]) in year


def test_memo_and_walls(planted: tuple[Any, Any]) -> None:
    p, state = planted
    events = p.sink("chat").read()
    item = next(i for i in state["memo"]["items"] if i["question_id"] == "2.3")
    say = compose(Intent(skill="memo_item", question_id="2.3"), state, set(), set())["say"]
    assert item["answer"] in say and str(item["max_marks"]) in say
    total = compose(Intent(skill="memo_item", view="total", question_id="3"), state, set(), set())[
        "say"
    ]
    assert (
        str(
            sum(i["max_marks"] for i in state["memo"]["items"] if i["question_id"].startswith("3."))
        )
        in total
    )
    checked = compose(Intent(skill="memo_item", view="check"), state, set(), set(), events=events)[
        "say"
    ]
    assert "2.3" in checked and "use_proposed" in checked
    walls = compose(Intent(skill="run_walls"), state, set(), set(), events=events)["say"]
    assert str(len(state["disagreements"])) in walls
    failed = [e for e in events if e.kind == EventKind.CHECK and e.payload.get("passed") is False]
    assert str(len(failed)) in walls
    for event in failed[-3:]:
        assert str(event.payload["detail"]) in walls
    broken = deepcopy(state)
    broken.update(halted="budget exhausted", pending={"L-014": []})
    assert (
        "budget exhausted"
        in compose(Intent(skill="run_walls"), broken, set(), set(), events=events)["say"]
    )


def test_poa_and_atp(planted: tuple[Any, Any]) -> None:
    _, state = planted
    pack = tomllib.loads(Path(state["pack_path"]).read_text())
    say = compose(Intent(skill="poa", term=2), state, set(), set())["say"]
    for row in pack["poa"]:
        if row["term"] == 2:
            assert row["name"] in say and str(row["weight_percent"]) in say
    assert pack["sources"][0] in say
    assert str(pack["moderation"]["min_scripts"]) in say
    rows = tomllib.loads((ROOT / "packs/dbe-caps/atp/mathematics-gr10.toml").read_text())["week"]
    intent = match_rules("What should I teach in term 2 week 3?")
    assert intent is not None
    atp = compose(intent, state, set(), set())["say"]
    row = next(r for r in rows if r["week"] == 14)
    assert row["topic"] in atp and row["source"] in atp
    atp = compose(Intent(skill="atp", topic="trigonometry"), state, set(), set())["say"]
    assert all(r["source"] in atp for r in rows if "trigonometry" in r["topic"].lower())


def test_refusals_and_missing_curriculum() -> None:
    for reason, expected in [
        ("sources", "Grade 10"),
        ("contact", "never contacts"),
        ("export", "not built"),
        ("marks", "Needs-you"),
    ]:
        say = compose(Intent(skill="cannot", refusal=reason), None, set(), set())["say"]
        assert expected in say
        if reason == "sources":
            for path in (ROOT / "packs/dbe-caps").glob("*.toml"):
                assert tomllib.loads(path.read_text())["subject"] in say
    result = asyncio.run(answer("What does CAPS say about functions?"))
    assert result["reply"]["say"] == "I can't reach the curriculum documents right now."


def test_lesson_three_turns_and_endpoint(planted: tuple[Any, Any]) -> None:
    from mothusi_contracts import Passage

    class Knowledge:
        async def search(self, query: str, **kwargs: Any) -> tuple[Passage, ...]:
            assert query
            return (
                Passage(
                    passage_id="p",
                    source="DBE CAPS",
                    source_key="caps",
                    locator="p 12",
                    score=1,
                    page=12,
                    text="Quoted curriculum text.",
                    kind="caps",
                    subject="Mathematics",
                    grade=10,
                ),
            )

    first = asyncio.run(answer("Prepare a lesson plan for week 6"))
    assert first["lesson"] is not None
    assert first["ask"] and first["lesson"]["weeks"] == [6]
    second = asyncio.run(answer("maths, 3 lessons", lesson=Lesson.model_validate(first["lesson"])))
    assert second["ask"] and "long" in second["ask"]
    third = asyncio.run(
        answer("45 minutes", lesson=Lesson.model_validate(second["lesson"]), knowledge=Knowledge())
    )
    assert third["ask"] is None
    assert "DBE CAPS, p 12: Quoted curriculum text." in third["reply"]["say"]
    assert third["reply"]["say"].endswith(
        "Drafting the full plan from this is the next step I'll be able to take."
    )
    p, _ = planted
    with TestClient(create_app(p)) as client:
        for context in [{"subject": "Mathematics"}, {"assessment_id": "chat"}]:
            before = len(p.sink("chat").read())
            result = client.post(
                "/colleague/messages",
                json={"text": "What is the PoA for term 2?", "context": context},
            )
            assert result.status_code == 200 and "Assignment" in result.json()["reply"]["say"]
            assert len(p.sink("chat").read()) == before
        assert (
            client.post(
                "/colleague/messages",
                json={"text": "status", "context": {"assessment_id": "absent"}},
            ).status_code
            == 404
        )
        assert client.post("/colleague/messages", json={"text": " "}).status_code == 422


def test_thread_keeps_lesson_slots(planted: tuple[Any, Any]) -> None:
    p, _ = planted

    async def turns() -> None:
        await respond(p, "chat", "m-1", "Prepare a lesson plan for week 6")
        await respond(p, "chat", "m-2", "3 lessons")
        await respond(p, "chat", "m-3", "45 minutes")

    asyncio.run(turns())
    say = p.sink("chat").read()[-1].payload["say"]
    assert isinstance(say, str) and say.endswith(
        "Drafting the full plan from this is the next step I'll be able to take."
    )


@pytest.mark.parametrize("subject", ["Accounting", "Mathematics"])
def test_given_subject_and_atp_topics(subject: str) -> None:
    pack = tomllib.loads((ROOT / f"packs/dbe-caps/{subject.lower()}-gr10.toml").read_text())
    result = asyncio.run(answer("What is the PoA for term 2?", subject=subject))
    say = result["reply"]["say"]
    assert pack["sources"][0] in say
    assert all(row["name"] in say for row in pack["poa"] if row["term"] == 2)
    result = asyncio.run(answer("What should I teach in term 4 week 10?", subject=subject))
    rows = tomllib.loads((ROOT / f"packs/dbe-caps/atp/{subject.lower()}-gr10.toml").read_text())[
        "week"
    ]
    assert next(r["source"] for r in rows if r["week"] == 43) in result["reply"]["say"]


def test_lesson_week_followup_topic_and_unheld_subject() -> None:
    first = asyncio.run(answer("Prepare a lesson plan", lesson=Lesson(subject="Mathematics")))
    second = asyncio.run(
        answer(
            "term 2 week 3, 3 lessons, 45 minutes", lesson=Lesson.model_validate(first["lesson"])
        )
    )
    assert second["lesson"] is not None
    assert second["ask"] is None and second["lesson"]["weeks"] == [14]
    topic = asyncio.run(
        answer("trigonometry", lesson=Lesson(subject="Mathematics", lessons=3, minutes=45))
    )
    assert topic["ask"] is None and "Trigonometry" in topic["reply"]["say"]
    unknown = asyncio.run(answer("Prepare a lesson plan", lesson=Lesson(subject="Economics")))
    assert unknown["reply"]["skill"] == "cannot"
    assert "Grade 10" in unknown["reply"]["say"]


def test_curriculum_exact_passages_limit_and_failure() -> None:
    from mothusi_contracts import Passage

    class Knowledge:
        async def search(self, query: str, **kwargs: Any) -> tuple[Passage, ...]:
            assert kwargs["k"] == 4 and kwargs["subject"] == "Mathematics"
            return tuple(
                Passage(
                    passage_id=str(i),
                    source=f"DBE {i}",
                    source_key=str(i),
                    kind="caps",
                    page=i + 1,
                    locator=f"p {i + 1}",
                    text="Exact words " * 25,
                    score=1,
                )
                for i in range(6)
            )

    result = asyncio.run(
        answer("What does CAPS say about functions?", subject="Mathematics", knowledge=Knowledge())
    )
    expected = "\n".join(f"DBE {i}, p {i + 1}: {('Exact words ' * 25)[:200]}" for i in range(4))
    assert result["reply"]["say"] == expected

    class Broken:
        async def search(self, query: str, **kwargs: Any) -> tuple[Passage, ...]:
            raise RuntimeError("unreachable")

    result = asyncio.run(answer("What does CAPS say about functions?", knowledge=Broken()))
    assert result["reply"]["say"] == "I can't reach the curriculum documents right now."


def test_walls_clean_waiting_and_recent_details(planted: tuple[Any, Any]) -> None:
    from mothusi_contracts import Event

    p, settled = planted
    state = deepcopy(settled)
    state.update(disagreements=[], pending={}, question=None, widened=False, halted=None)
    say = compose(Intent(skill="run_walls"), state, set(), set())["say"]
    assert "Nothing went wrong" in say
    events = [
        Event(
            seq=i,
            at=p.clock(),
            assessment_id="chat",
            kind=EventKind.CHECK,
            summary=f"failed {i}",
            payload={"passed": False, "detail": f"detail-{i}"},
        )
        for i in range(5)
    ]
    events.append(
        Event(
            seq=5,
            at=p.clock(),
            assessment_id="chat",
            kind=EventKind.NEEDS_TEACHER,
            summary="waiting",
            payload={"script_id": "L-014", "title": "Evidence quote absent"},
        )
    )
    state.update(pending={"L-014": []}, widened=True)
    say = compose(Intent(skill="run_walls"), state, set(), set(), events=events)["say"]
    assert "Evidence quote absent" in say and "5 failed checks" in say
    assert "detail-0" not in say and "detail-1" not in say
    assert all(f"detail-{i}" in say for i in range(2, 5))
    assert "sample widened" in say
    assert f"R{float(state['spent_zar']):.2f}" in say


def test_before_marking_memo_totals_and_check_agreement(planted: tuple[Any, Any]) -> None:
    from mothusi_contracts import Event

    p, settled = planted
    state = deepcopy(settled)
    state.update(marked={}, moderation_closed=False, converted={}, item_stats=[])
    say = compose(Intent(skill="run_report"), state, set(), set())["say"]
    assert "during marking" in say
    say = compose(Intent(skill="memo_item", view="total"), state, set(), set())["say"]
    assert f"{sum(i['max_marks'] for i in state['memo']['items'])} marks" in say
    assert (
        "after memo check"
        in compose(Intent(skill="memo_item", view="check"), state, set(), set())["say"]
    )
    event = Event(
        seq=0,
        at=p.clock(),
        assessment_id="chat",
        kind=EventKind.STATE,
        summary="memo confirmed: the Setter agrees",
    )
    say = compose(Intent(skill="memo_item", view="check"), state, set(), set(), events=[event])[
        "say"
    ]
    assert "Setter agrees" in say


def test_endpoint_lesson_stateless_context_and_validation(planted: tuple[Any, Any]) -> None:
    p, _ = planted
    with TestClient(create_app(p)) as client:
        result = client.post(
            "/colleague/messages",
            json={
                "text": "Prepare a lesson plan for week 6",
                "context": {"pack_id": "dbe-caps/mathematics-gr10"},
            },
        )
        assert result.status_code == 200
        data = result.json()
        assert data["lesson"]["subject"] == "Mathematics"
        assert data["ask"] == "How many lessons should I prepare?"
        data = client.post(
            "/colleague/messages",
            json={"text": "3 lessons, 45 minutes", "context": {"lesson": data["lesson"]}},
        ).json()
        assert data["ask"] is None and "Week 6" in data["reply"]["say"]
        assert client.post("/colleague/messages", json={"text": "x" * 1001}).status_code == 422
        assert (
            client.post(
                "/colleague/messages", json={"text": "ATP", "context": {"pack_id": "missing"}}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/colleague/messages",
                json={"text": "lesson plan", "context": {"lesson": {"weeks": [44]}}},
            ).status_code
            == 422
        )


def test_all_new_skills_work_on_thread(planted: tuple[Any, Any]) -> None:
    p, _ = planted
    requests = [
        "Give me the stats for this run",
        "What went wrong?",
        "What does the memo give for 2.3?",
        "What is the PoA for term 2?",
        "What should I teach in week 6?",
        "What does CAPS say about functions?",
        "Approve the set",
    ]

    async def turns() -> None:
        for index, text in enumerate(requests):
            await respond(p, "chat", f"m-{index}", text)

    asyncio.run(turns())
    replies = [e.payload for e in p.sink("chat").read() if e.kind == EventKind.REPLY]
    assert [r["skill"] for r in replies] == [
        "run_report",
        "run_walls",
        "memo_item",
        "poa",
        "atp",
        "curriculum",
        "cannot",
    ]
    assert all(r["say"] for r in replies)


def test_runless_endpoint_reads_early_releases_without_writing(planted: tuple[Any, Any]) -> None:
    p, _ = planted
    asyncio.run(
        p.store.aput(("early_sent", "chat"), "L-001", {"sent_at": "2026-11-14T05:00:00+02:00"})
    )
    before = len(p.sink("chat").read())
    with TestClient(create_app(p)) as client:
        result = client.post(
            "/colleague/messages",
            json={"text": "send L-001 feedback", "context": {"assessment_id": "chat"}},
        )
    assert result.status_code == 200
    assert "released at 05:00" in result.json()["reply"]["say"]
    assert result.json()["reply"]["card"] is None
    assert len(p.sink("chat").read()) == before


def test_unheld_curriculum_subject_is_refused() -> None:
    result = asyncio.run(answer("What does CAPS say about forces?", subject="Physical Sciences"))
    assert result["reply"]["skill"] == "cannot"
