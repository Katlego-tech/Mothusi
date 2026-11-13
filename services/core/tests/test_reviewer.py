"""US4 scenario 2: the Reviewer rejects an ungrounded note, and the rewrite passes.

Design: docs/design/marking-pipeline.md §6.1; the `reviewer` contract. Grounding is checked in code
(`check_note_grounding`); the model only reviews tone, and only on notes code passed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from mothusi_contracts import CatchUpItem, LearnerNote, MarkedScript, ResultStatus, Script
from mothusi_core.agents.reviewer import ToneReply, review, reviewer_brief, reviewer_prompts

from .conftest import ASSESSMENT, Recorder

CLASS_SHEET = "# Class feedback\n\nQuestion 2.2 needs re-teaching.\n"


def grounded(script: Script, marked: MarkedScript) -> LearnerNote:
    best = max(marked.marks, key=lambda m: (m.awarded, m.question_id))
    return LearnerNote(
        pseudonym=script.learner_pseudonym,
        outcome="Algebra and exponents, Term 1 Test",
        mark_with_quote=(
            f'{best.awarded}/{best.max_marks} on {best.question_id}: "{best.evidence_quote}"'
        ),
        next_task="Redo question 2.2 writing every base as a power of 3",
        self_assessment_prompt="Where did I first change the base in 2.2?",
    )


@pytest.fixture(scope="module")
def three(
    class_set: tuple[tuple[Script, ...], dict[str, str]], marked_class: tuple[MarkedScript, ...]
) -> tuple[list[Script], list[MarkedScript]]:
    """L-001, L-007 and L-014: three learners who each earned full marks on some question."""
    scripts = {s.script_id: s for s in class_set[0]}
    marked = {m.script_id: m for m in marked_class}
    ids = ["L-001", "L-007", "L-014"]
    return [scripts[i] for i in ids], [marked[i] for i in ids]


def passing_tone(notes: Sequence[LearnerNote]) -> ToneReply:
    return ToneReply.model_validate(
        {
            "verdicts": [{"pseudonym": n.pseudonym, "passed": True, "reason": ""} for n in notes],
            "class_sheet_passed": True,
        }
    )


def run_review(
    recorder: Recorder,
    notes: list[LearnerNote],
    scripts: list[Script],
    marked: list[MarkedScript],
    tone_for: list[LearnerNote],
    tone: ToneReply | None = None,
    plan: Sequence[CatchUpItem] = (),
):
    recorder.add(
        reviewer_prompts(tone_for, CLASS_SHEET),
        "ToneReply",
        (tone or passing_tone(tone_for)).model_dump(mode="json"),
    )
    brief = reviewer_brief(ASSESSMENT, notes, marked, scripts, CLASS_SHEET, plan)
    return asyncio.run(review(brief, recorder.provider()))


def verdicts(result_outputs: dict[str, object]) -> dict[str, dict[str, object]]:
    return {v["pseudonym"]: v for v in result_outputs["verdicts"]}  # type: ignore[index,union-attr]


def test_grounded_notes_pass(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    result = run_review(recorder, notes, scripts, marked, notes)
    assert result.status is ResultStatus.DONE, result.reason
    assert result.outputs["passed"] is True
    assert all(v["passed"] for v in verdicts(result.outputs).values())


def test_the_planted_weak_at_algebra_note_is_rejected_in_code(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    """traps.md: L-007 has full marks on algebra; a note calling them weak at it is refused."""
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    notes[1] = notes[1].model_copy(
        update={"outcome": "L-007 is weak at algebra and should practise factorising"}
    )
    clean = [notes[0], notes[2]]  # only these reach the model: code already rejected L-007
    result = run_review(recorder, notes, scripts, marked, clean)
    assert result.status is ResultStatus.DONE, result.reason
    v = verdicts(result.outputs)
    assert v["L-007"]["passed"] is False and "is weak" in str(v["L-007"]["reason"])
    assert v["L-001"]["passed"] and v["L-014"]["passed"]
    assert result.outputs["passed"] is False
    assert "L-007" not in reviewer_prompts(clean, CLASS_SHEET)[1]


def test_a_quote_not_in_the_script_and_a_mark_not_given_are_rejected(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    notes[0] = notes[0].model_copy(
        update={"mark_with_quote": '5/5 on 1.1: "x = 3 or x = -2, checked by substitution"'}
    )
    quote = max(marked[2].marks, key=lambda m: (m.awarded, m.question_id)).evidence_quote
    notes[2] = notes[2].model_copy(update={"mark_with_quote": f'49/50 overall: "{quote}"'})
    result = run_review(recorder, notes, scripts, marked, [notes[1]])
    v = verdicts(result.outputs)
    assert "quote not found in the script" in str(v["L-001"]["reason"])
    assert "mark 49/50 is not in the marked script" in str(v["L-014"]["reason"])
    assert v["L-007"]["passed"] is True


def test_a_tone_rejection_from_the_model_is_kept(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    tone = passing_tone(notes)
    tone = tone.model_copy(
        update={
            "verdicts": (
                tone.verdicts[0].model_copy(
                    update={"passed": False, "reason": "implies the learner never tries"}
                ),
                *tone.verdicts[1:],
            )
        }
    )
    result = run_review(recorder, notes, scripts, marked, notes, tone)
    v = verdicts(result.outputs)
    assert v["L-001"]["passed"] is False and "never tries" in str(v["L-001"]["reason"])
    assert result.outputs["passed"] is False


def test_a_verdict_missing_for_a_note_is_sent_back(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    tone = passing_tone(notes)
    tone = tone.model_copy(update={"verdicts": tone.verdicts[1:]})
    result = run_review(recorder, notes, scripts, marked, notes, tone)
    assert result.status is ResultStatus.RETRY and "L-001" in result.reason


def test_the_rewrite_pointing_at_exponents_passes(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    """SPEC US4 acceptance: the rewrite of L-007's note quotes 2.1 with its mark, and passes."""
    scripts, marked = three
    l007_script, l007 = scripts[1], marked[1]
    answer_21 = next(a.text for a in l007_script.answers if a.question_id == "2.1")
    mark_21 = next(m for m in l007.marks if m.question_id == "2.1")
    rewrite = LearnerNote(
        pseudonym="L-007",
        outcome="The laws of exponents (questions 2.1 to 2.3)",
        mark_with_quote=f'{mark_21.awarded}/{mark_21.max_marks} on 2.1: you wrote "{answer_21}"',
        next_task="Square every factor inside the bracket in 2.1, then multiply by 3x^(-2)",
        self_assessment_prompt="When I square (2x^3 y), what happens to each exponent?",
    )
    notes = [grounded(scripts[0], marked[0]), rewrite, grounded(scripts[2], marked[2])]
    result = run_review(recorder, notes, scripts, marked, notes)
    assert result.status is ResultStatus.DONE, result.reason
    assert result.outputs["passed"] is True
    assert verdicts(result.outputs)["L-007"]["passed"] is True


def test_two_verdicts_for_one_note_are_sent_back(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    """Codex review P2: a rejection followed by a pass must not erase the rejection."""
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    tone = passing_tone(notes)
    reject = tone.verdicts[0].model_copy(update={"passed": False, "reason": "labels"})
    tone = tone.model_copy(update={"verdicts": (reject, *tone.verdicts)})
    result = run_review(recorder, notes, scripts, marked, notes, tone)
    assert result.status is ResultStatus.RETRY and "more than one tone verdict for L-001" in (
        result.reason
    )


PLAN = (
    CatchUpItem(
        week=11,
        topic="Re-teach 2.2",
        activity="Starter on prime bases",
        atp_source="RATP Mathematics Grade 10 2024, week 11 (03-05 April)",
    ),
)


def test_an_atp_week_not_in_the_plan_is_rejected_in_code(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    """Codex review P2: a note may name an ATP week only from the checked catch-up plan."""
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    notes[0] = notes[0].model_copy(update={"next_task": "Practise prime bases in week 11"})
    notes[1] = notes[1].model_copy(update={"next_task": "Practise prime bases in week 20"})
    result = run_review(recorder, notes, scripts, marked, [notes[0], notes[2]], plan=PLAN)
    v = verdicts(result.outputs)
    assert v["L-001"]["passed"] is True
    assert v["L-007"]["passed"] is False
    assert "week 20 is not in the checked catch-up plan" in str(v["L-007"]["reason"])


def test_with_no_plan_no_week_may_be_named(
    recorder: Recorder, three: tuple[list[Script], list[MarkedScript]]
) -> None:
    scripts, marked = three
    notes = [grounded(s, m) for s, m in zip(scripts, marked, strict=True)]
    notes[2] = notes[2].model_copy(update={"next_task": "Revise this in Week 11"})
    result = run_review(recorder, notes, scripts, marked, notes[:2])
    assert verdicts(result.outputs)["L-014"]["passed"] is False


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Practise in weeks 11 and 20", [20]),
        ("Practise in weeks 11, 12 or 20", [12, 20]),
        ("Practise in weeks 11\u201313", [12, 13]),
        ("Practise in weeks 11 to 12", [12]),
        ("Practise in week 11 & week 11", []),
        ("Practise in weeks 11-999999999", [*range(12, 53), 999999999]),
    ],
)
def test_every_week_in_a_list_or_range_is_checked(text: str, bad: list[int]) -> None:
    """Codex review P2: 'weeks 11 and 20' must not pass on week 11 alone."""
    from mothusi_core.agents.reviewer import check_note_weeks

    note = LearnerNote(
        pseudonym="L-001",
        outcome="o",
        mark_with_quote="m",
        next_task=text,
        self_assessment_prompt="p",
    )
    assert check_note_weeks(note, PLAN) == [
        f"week {w} is not in the checked catch-up plan" for w in bad
    ]
