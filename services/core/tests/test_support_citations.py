"""Support cites retrieved CAPS/ATP passages (knowledge.md §4.2, §6.7)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import AtpWeek, CatchUpItem, EventKind, Passage, PassageKind, ResultStatus
from mothusi_core.agents.analyst import Misconception
from mothusi_core.agents.support import (
    SUPPORT_SYSTEM,
    SupportReply,
    plan_catch_up,
    support_brief,
    support_prompts,
)
from mothusi_core.agents.writer import NO_PASSAGE, catch_up_plan_md
from mothusi_core.graph.planner import Planner
from mothusi_core.knowledge import KnowledgeError
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY, check_planted, run_planted
from mothusi_llm import ReplayProvider

from .conftest import ASSESSMENT, Recorder

FOUND = [
    Misconception(
        question_id="2.3",
        pattern="adds the exponents instead of equating them",
        evidence_quotes=("x + 1 = -2",),
        scripts=4,
    )
]
WEEKS = (
    AtpWeek(week=10, topic="Revision", source="ATP 2023-24 Gr 10 Mathematics, Term 1 week 10"),
)
PASSAGE = Passage(
    passage_id="atp-gr10-maths-p1-1",
    source="ATP 2023-24 Gr 10 Mathematics (DBE)",
    source_key="atp-gr10-maths",
    kind=PassageKind.ATP,
    subject="Mathematics",
    grade=10,
    page=1,
    locator="PDF p 1",
    text="Exponents and surds: simplify expressions and solve exponential equations",
    score=0.82,
)
CITED = {"2.3": [PASSAGE]}


def reply(citation: str) -> SupportReply:
    return SupportReply.model_validate(
        {
            "items": [
                {
                    "question_id": "2.3",
                    "week": 10,
                    "topic": "Exponential equations",
                    "activity": "Starter: equate exponents of the same base; two worked examples",
                    "atp_source": WEEKS[0].source,
                    "citation": citation,
                }
            ]
        }
    )


def test_without_passages_the_prompts_are_the_ones_the_replay_recording_keys_on() -> None:
    assert support_prompts(FOUND, WEEKS) == support_prompts(FOUND, WEEKS, None, {})
    assert support_prompts(FOUND, WEEKS, None, {"2.3": []}) == support_prompts(FOUND, WEEKS)
    assert support_prompts(FOUND, WEEKS)[0] == SUPPORT_SYSTEM
    assert "passages_by_question" not in support_brief(ASSESSMENT, FOUND, WEEKS).inputs


def test_passages_are_listed_under_their_question_with_the_citation_rule() -> None:
    system, user = support_prompts(FOUND, WEEKS, None, CITED)
    assert (
        'citation is "" or copied exactly from a passage listed under that item\'s question'
        in system
    )
    assert f"Question 2.3:\n[{PASSAGE.citation}] {PASSAGE.text}" in user


def test_a_copied_citation_is_kept_and_an_invented_one_is_a_retry(recorder: Recorder) -> None:
    brief = support_brief(ASSESSMENT, FOUND, WEEKS, passages_by_question=CITED)
    recorder.add(
        support_prompts(FOUND, WEEKS, None, CITED),
        "SupportReply",
        reply(PASSAGE.citation).model_dump(mode="json"),
    )
    done = asyncio.run(plan_catch_up(brief, recorder.provider()))
    assert done.status is ResultStatus.DONE, done.reason
    (item,) = [CatchUpItem.model_validate(i) for i in done.outputs["catch_up_plan"]]
    assert item.citation == PASSAGE.citation
    assert f"| {PASSAGE.citation} |" in catch_up_plan_md([item])

    other = Recorder(recorder.path.with_name("invented.jsonl"))
    other.add(
        support_prompts(FOUND, WEEKS, None, CITED),
        "SupportReply",
        reply("CAPS FET Mathematics, PDF p 99").model_dump(mode="json"),
    )
    retried = asyncio.run(plan_catch_up(brief, other.provider()))
    assert retried.status is ResultStatus.RETRY
    assert retried.reason is not None and retried.reason.startswith("citations:")


def test_an_item_without_a_citation_shows_a_dash_in_the_plan() -> None:
    item = CatchUpItem(week=10, topic="t", activity="a", atp_source="s")
    assert f"| s | {NO_PASSAGE} |" in catch_up_plan_md([item])


class Failing:
    calls = 0

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        subject: str | None = None,
        grade: int | None = None,
        kinds: Sequence[PassageKind] = (),
    ) -> tuple[Passage, ...]:
        Failing.calls += 1
        raise KnowledgeError("Knowledge request timeout")


def test_a_failed_search_is_a_decision_and_the_run_still_delivers(tmp_path: Path) -> None:
    planner = Planner(
        primary=ReplayProvider(REPLAY, free),
        fallback=None,
        data_dir=tmp_path,
        checkpointer=InMemorySaver(),
        knowledge=Failing(),
    )
    state, events, names = asyncio.run(
        run_planted(ReplayProvider(REPLAY, free), tmp_path, planner=planner)
    )
    assert check_planted(state, events, names) == []
    assert Failing.calls >= 1
    decisions = [e.summary for e in events if e.kind is EventKind.DECISION]
    assert any(
        "knowledge search failed" in d and "briefed without passages" in d for d in decisions
    )
    assert any(e.kind is EventKind.DELIVERED for e in events)


def test_no_knowledge_service_is_said_once(tmp_path: Path) -> None:
    state, events, names = asyncio.run(run_planted(ReplayProvider(REPLAY, free), tmp_path))
    assert check_planted(state, events, names) == []
    said = [e for e in events if e.kind is EventKind.DECISION and "not configured" in e.summary]
    assert len(said) == 1
