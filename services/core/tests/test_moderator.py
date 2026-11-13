"""US2: the sample is blind, a disagreement is found, widening follows the pack's rule."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from mothusi_contracts import AgentRole, Brief, Mark, MarkedScript, Memo, Script, SubjectPack
from mothusi_core.agents.marker import MarkerReply, mark, marker_prompts
from mothusi_core.agents.moderator import (
    blind_check,
    compare,
    moderate_script,
    sample_for_moderation,
)
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import ReplayProvider, record
from mothusi_rules import load_pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "services/core/fixtures/gr10-maths-t1"
ASSESSMENT = "gr10-maths-t1-test"


def no_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return Decimal(0)


@pytest.fixture(scope="module")
def memo() -> Memo:
    return load_memo(FIXTURES / "memo.toml")


@pytest.fixture(scope="module")
def pack() -> SubjectPack:
    return load_pack(ROOT / "packs/dbe-caps/mathematics-gr10.toml")


@pytest.fixture(scope="module")
def loaded() -> tuple[dict[str, Script], dict[str, str]]:
    scripts, names = load_scripts(FIXTURES / "scripts.csv")
    return {s.script_id: s for s in scripts}, names


def reply(memo: Memo, script: Script, awarded_for: dict[str, int] | None = None) -> MarkerReply:
    """Full marks on answered questions unless `awarded_for` says otherwise; quotes the answer."""
    answers = {a.question_id: a.text for a in script.answers}
    marks: list[Mark] = []
    for item in memo.items:
        text = answers.get(item.question_id)
        awarded = (awarded_for or {}).get(item.question_id, item.max_marks if text else 0)
        marks.append(
            Mark(
                question_id=item.question_id,
                awarded=awarded,
                max_marks=item.max_marks,
                evidence_quote=text if awarded and text else "",
                memo_line=item.answer,
                principle="answer matches memo" if awarded else "no attempt",
            )
        )
    return MarkerReply(marks=tuple(marks))


def scored(reply: MarkerReply) -> dict[str, Any]:
    """A reply's fields plus the total code adds, as the Marker builds a MarkedScript."""
    return {**reply.model_dump(), "total": sum(m.awarded for m in reply.marks)}


def test_the_moderator_brief_is_blind_and_a_leak_is_refused(
    memo: Memo, pack: SubjectPack, loaded: tuple[dict[str, Script], dict[str, str]]
) -> None:
    scripts, names = loaded
    script = scripts["L-021"]
    inputs = {
        "memo": memo.model_dump(mode="json"),
        "script": script.model_dump(mode="json"),
        "pack_conventions": list(pack.conventions),
    }
    brief = Brief(
        brief_id="b", assessment_id=ASSESSMENT, agent=AgentRole.MODERATOR, task="t", inputs=inputs
    )
    assert blind_check(brief, list(names)).passed
    leaked = brief.model_copy(
        update={"inputs": {**inputs, "marked": reply(memo, script).model_dump(mode="json")}}
    )
    assert not blind_check(leaked, list(names)).passed

    # and the agent itself refuses to run on such a brief, whatever the guard said
    class Never:
        name = "never"

        async def complete(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("must not be called")

    result = asyncio.run(mark(leaked, Never()))  # type: ignore[arg-type]
    assert result.status.value == "blocked" and "first marking" in result.reason


def test_blind_remark_disagrees_on_the_planted_over_mark(
    tmp_path: Path, memo: Memo, pack: SubjectPack, loaded: tuple[dict[str, Script], dict[str, str]]
) -> None:
    """traps.md: L-021 Q4 is a 2/6 answer a lenient marker gives about 5/6."""
    scripts, _ = loaded
    script = scripts["L-021"]
    first = reply(memo, script, {"4": 5})
    second = reply(memo, script, {"4": 2})
    system, user = marker_prompts(memo, script, pack.conventions, role=AgentRole.MODERATOR)
    assert system.startswith("You are Mothusi's Moderator") and "first marking" not in user
    record(
        tmp_path / "calls.jsonl",
        system,
        user,
        "MarkerReply",
        "replay-model",
        second.model_dump(mode="json"),
        1000,
        300,
    )
    outcome = asyncio.run(
        moderate_script(
            memo,
            script,
            pack,
            ReplayProvider(tmp_path / "calls.jsonl", no_cost),
            assessment_id=ASSESSMENT,
        )
    )
    assert outcome.status == "done" and outcome.marked is not None
    assert outcome.marked.marker == "moderator"
    assert outcome.attempts[0].brief.brief_id == f"{ASSESSMENT}/L-021/moderator/1"
    first_marked = MarkedScript(
        script_id="L-021",
        marker="marker",
        marks=first.marks,
        total=sum(m.awarded for m in first.marks),
    )
    found, compared, widen = compare([first_marked], [outcome.marked], pack)
    assert [(d.question_id, d.first, d.second) for d in found] == [("4", 5, 2)]
    # 1 disagreement in 9 compared questions is 11%, over the pack's 10% rule: the whole class is
    # re-marked blind. With a second clean script in the sample (1 of 18, 5.6%) it is not.
    assert compared == len(memo.items) and widen
    clean = scripts["L-001"]
    clean_marked = MarkedScript(script_id="L-001", marker="marker", **scored(reply(memo, clean)))
    clean_second = clean_marked.model_copy(update={"marker": "moderator"})
    _, compared2, widen2 = compare(
        [first_marked, clean_marked], [outcome.marked, clean_second], pack
    )
    assert compared2 == 2 * len(memo.items) and not widen2


def test_widening_follows_the_pack_rule_and_unmatched_scripts_are_refused(
    memo: Memo, pack: SubjectPack, loaded: tuple[dict[str, Script], dict[str, str]]
) -> None:
    scripts, _ = loaded
    a, b = scripts["L-001"], scripts["L-002"]
    first = [
        MarkedScript(script_id=s.script_id, marker="marker", **scored(reply(memo, s)))
        for s in (a, b)
    ]
    second = [
        MarkedScript(
            script_id=a.script_id,
            marker="moderator",
            **scored(reply(memo, a, {"1.1": 1, "1.2": 1})),
        ),
        MarkedScript(
            script_id=b.script_id, marker="moderator", **scored(reply(memo, b, {"1.1": 1}))
        ),
    ]
    found, compared, widen = compare(first, second, pack)
    assert len(found) == 3 and compared == 2 * len(memo.items) and widen  # 3 of 18 > 10%
    with pytest.raises(ValueError, match="never marked"):
        compare(first[:1], second, pack)


def test_sample_uses_the_pack_rule(
    memo: Memo, pack: SubjectPack, loaded: tuple[dict[str, Script], dict[str, str]]
) -> None:
    scripts, _ = loaded
    marked = [
        MarkedScript(script_id=s.script_id, marker="marker", **scored(reply(memo, s)))
        for s in scripts.values()
    ]
    sample = sample_for_moderation(marked, pack)
    assert len(sample) == 6 and len(set(sample)) == 6 and set(sample) <= set(scripts)
