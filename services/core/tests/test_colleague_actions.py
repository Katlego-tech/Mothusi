"""Screen actions are grounded in the planted run and keep names out of events."""

import asyncio
import json
from copy import deepcopy
from typing import Literal

import pytest

from mothusi_contracts import EventKind
from mothusi_core.colleague import Intent, answer, compose, deferred_reply, match_rules, respond
from mothusi_core.graph.planner import Planner
from mothusi_core.graph.state import RunState

from .test_colleague import planted as planted


@pytest.mark.parametrize("verb", ["open", "show me", "take me to", "go to"])
@pytest.mark.parametrize(
    "target,view,sid",
    [
        ("L-001's script", "script", "L-001"),
        ("l-001's sheet", "script", "L-001"),
        ("L-001's paper", "script", "L-001"),
        ("L-001's feedback", "note", "L-001"),
        ("L-001's note", "note", "L-001"),
        ("the stats", "stats", None),
        ("the statistics", "stats", None),
        ("the item analysis", "stats", None),
        ("the results", "stats", None),
        ("the approval", "approval", None),
        ("my desk", "desk", None),
        ("home", "desk", None),
        ("a new set", "new", None),
    ],
)
def test_open_phrasings(verb: str, target: str, view: str, sid: str | None) -> None:
    intent = match_rules(f"{verb} {target}")
    assert intent == Intent(skill="open", view=view, script_id=sid)


@pytest.mark.parametrize("text", ["start a new set", "mark a set", "mark a new set"])
def test_new_set_phrasings(text: str) -> None:
    assert match_rules(text) == Intent(skill="open", view="new")


@pytest.mark.parametrize("stage", ["marking", "awaiting_approval"])
def test_each_navigation_row(
    planted: tuple[Planner, RunState], stage: Literal["marking", "awaiting_approval"]
) -> None:
    _, settled = planted
    state = deepcopy(settled)
    state["state"] = stage
    ready = stage == "awaiting_approval"
    if not ready:
        state["moderation_closed"] = False
        state.pop("bundle", None)
        state.pop("drafts", None)
        state.pop("converted", None)
        state.pop("item_stats", None)
    report = compose(Intent(skill="run_report"), state, set(), set())
    cases = [
        ("Open L-001's script", "Opening L-001's script.", "script", "L-001"),
        (
            "Open L-001's feedback",
            "Opening L-001's note."
            if ready
            else "L-001's note isn't written yet. I'll open the script instead.",
            "note" if ready else "script",
            "L-001",
        ),
        ("Show me the stats", report["say"], "stats" if ready else None, None),
        ("Show me the item analysis", report["say"], "stats" if ready else None, None),
        (
            "Take me to the approval",
            "Opening the approval."
            if ready
            else "Approval will be ready after marking, moderation and review.",
            "approval" if ready else None,
            None,
        ),
        ("Go to my desk", "Opening your desk.", "desk", None),
        ("Start a new set", "Starting a new set.", "new", None),
    ]
    for text, say, target, sid in cases:
        reply = asyncio.run(answer(text, state=state))["reply"]
        assert reply["skill"] == "open", text
        assert reply["say"] == say, text
        assert reply["action"] == ({"open": target, "script_id": sid} if target else None), text
        assert reply["card"] is None and reply["promise"] is None
    assert report["action"] == ({"open": "stats", "script_id": None} if ready else None)
    assert ("Final" if ready else "Provisional") in report["say"]
    marked = state.get("marked", {})
    mark = marked["L-001"]["marks"][0]
    reply = compose(
        Intent(skill="explain_mark", script_id="L-001", question_id=mark["question_id"]),
        state,
        set(),
        set(),
    )
    assert reply["action"] == {"open": "script", "script_id": "L-001"}
    assert mark["evidence_quote"] in reply["say"] and mark["memo_line"] in reply["say"]
    reply = compose(Intent(skill="send_feedback", script_id="L-001"), state, set(), set())
    assert reply["action"] == ({"open": "note", "script_id": "L-001"} if ready else None)
    assert reply["say"] == (
        "L-001's note is ready. It's released only if you approve it on the card."
        if ready
        else f"L-001 has a provisional {marked['L-001']['total']}/"
        f"{state['memo']['total_marks']}. Marks are final only after moderation, because "
        "the blind sample is drawn once every script is marked. "
        "I'll bring you the note to approve then."
    )


def test_bundle_rows_and_name_free_event_log(planted: tuple[Planner, RunState]) -> None:
    planner, state = planted
    bundle = state.get("bundle", {})
    for key, source in [
        ("mark_sheet", set(state.get("converted", {}))),
        ("learner_notes", {n["pseudonym"] for n in state.get("drafts", {})["learner_notes"]}),
    ]:
        assert {row["pseudonym"] for row in bundle[key]} == source
        for row in bundle[key]:
            assert row["learner"] == state["names"][row["pseudonym"]]

    async def chat() -> None:
        for index, text in enumerate(
            [
                f"Open {state['names']['L-001']}'s script",
                f"Open {state['names']['L-001']}'s feedback",
                "Show me the stats",
                "Take me to the approval",
                "Go to my desk",
                "Start a new set",
                "explain L-001 question 2.1",
                f"send {state['names']['L-001']} their feedback",
            ]
        ):
            await respond(planner, "chat", f"action-{index}", text)

    asyncio.run(chat())
    events = planner.sink("chat").read()
    replies = [e for e in events if e.kind == EventKind.REPLY]
    assert len(replies) == 8 and all(e.payload["action"] is not None for e in replies)
    for event in events:
        payload = json.dumps(event.payload, ensure_ascii=False)
        assert all(name not in payload for name in state["names"].values())


def test_missing_targets_and_default_actions(planted: tuple[Planner, RunState]) -> None:
    _, state = planted
    for text in ["Open L-999's script", "Open L-999's note", "Open the script"]:
        reply = asyncio.run(answer(text, state=state))["reply"]
        assert reply["action"] is None
        assert reply["say"]
    for intent in [
        Intent(skill="run_status"),
        Intent(skill="fallback"),
        Intent(skill="curriculum"),
    ]:
        reply = asyncio.run(answer("", intent=intent, state=state))["reply"]
        assert reply["action"] is None
    assert deferred_reply("the test")["action"] is None
    for text, target in [("Go home", "desk"), ("Mark a set", "new")]:
        assert asyncio.run(answer(text))["reply"]["action"] == {
            "open": target,
            "script_id": None,
        }
    for text in ["Open L-001's script", "Show me the stats", "Take me to approval"]:
        assert asyncio.run(answer(text))["reply"]["action"] is None
    failed = deepcopy(state)
    failed["verdicts"] = [{"pseudonym": "L-001", "passed": False, "reason": "unsupported"}]
    assert (
        compose(Intent(skill="send_feedback", script_id="L-001"), failed, set(), set())["action"]
        is None
    )
