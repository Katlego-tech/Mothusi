"""The contracts reject what the design says is impossible, and accept what it allows."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from mothusi_contracts import (
    Assessment,
    AssessmentState,
    CognitiveSpread,
    Disagreement,
    ExamPapers,
    Mark,
    MarkedScript,
    Memo,
    MemoItem,
    ModerationRule,
    PoATask,
    Script,
    SubjectPack,
)


def test_memo_items_must_add_up_to_total() -> None:
    with pytest.raises(ValidationError, match="add up to 4, not total_marks 10"):
        Memo(
            assessment_id="a",
            total_marks=10,
            items=(MemoItem(question_id="1", max_marks=4, answer="x", level="knowledge"),),
        )


def test_memo_question_ids_are_unique() -> None:
    item = MemoItem(question_id="1", max_marks=2, answer="x", level="knowledge")
    with pytest.raises(ValidationError, match="unique"):
        Memo(assessment_id="a", total_marks=4, items=(item, item))


def test_a_mark_above_zero_needs_an_evidence_quote() -> None:
    with pytest.raises(ValidationError, match="evidence_quote"):
        Mark(question_id="1", awarded=1, max_marks=2, memo_line="x", principle="p")


def test_a_zero_mark_needs_no_quote() -> None:
    mark = Mark(question_id="1", awarded=0, max_marks=2, memo_line="x", principle="no attempt")
    assert mark.evidence_quote == ""


def test_awarded_cannot_exceed_max() -> None:
    with pytest.raises(ValidationError, match="exceeds max_marks"):
        Mark(
            question_id="1",
            awarded=3,
            max_marks=2,
            evidence_quote="q",
            memo_line="x",
            principle="p",
        )


def test_models_are_frozen_and_reject_unknown_fields(marked: MarkedScript) -> None:
    with pytest.raises(ValidationError):
        marked.total = 3  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        MemoItem(question_id="1", max_marks=1, answer="a", level="l", bonus=1)  # type: ignore[call-arg]


def test_disagreement_gap_is_absolute() -> None:
    d = Disagreement(script_id="s", question_id="1", first=2, second=5)
    assert d.gap == 3
    assert Disagreement(script_id="s", question_id="1", first=5, second=2).gap == 3


def test_cognitive_levels_must_sum_to_100() -> None:
    with pytest.raises(ValidationError, match="sum to 100"):
        CognitiveSpread(levels={"knowledge": Decimal(20)}, tolerance_points=Decimal(5))


def _pack(sba: int, exam: int) -> SubjectPack:
    return SubjectPack(
        pack_id="dbe-caps",
        subject="Mathematics",
        grade=10,
        sba_percent=sba,
        exam_percent=exam,
        poa=(PoATask(term=1, name="Test", kind="test", max_marks=50, weight_percent=Decimal(100)),),
        exam=ExamPapers(papers=2, marks_each=100, hours_each=Decimal(2)),
        cognitive=CognitiveSpread(
            levels={"knowledge": Decimal(50), "problem_solving": Decimal(50)},
            tolerance_points=Decimal(5),
        ),
        moderation=ModerationRule(
            sample_percent=Decimal(10),
            min_scripts=6,
            spread="top-middle-bottom",
            widen_above_gap_percent=Decimal(10),
        ),
        sources=("Circular S8 of 2023 Annexure §26",),
    )


def test_pack_components_sum_to_100() -> None:
    assert _pack(40, 60).sba_percent == 40
    with pytest.raises(ValidationError, match="sum to 100"):
        _pack(40, 50)


def _assessment(assessment_id: str, memo: Memo, script: Script) -> Assessment:
    return Assessment(
        assessment_id=assessment_id,
        subject="Mathematics",
        grade=10,
        term=1,
        title="Term 1 test",
        poa_task_name="Test",
        memo=memo,
        scripts=(script,),
    )


def test_assessment_memo_must_belong_to_it(memo: Memo, script: Script) -> None:
    ok = _assessment("gr10-maths-t1-test", memo, script)
    assert ok.state is AssessmentState.DRAFT
    with pytest.raises(ValidationError, match=r"memo\.assessment_id"):
        _assessment("other", memo, script)


def test_a_script_holds_one_answer_per_question() -> None:
    from mothusi_contracts import Answer

    with pytest.raises(ValidationError, match="one answer per question_id"):
        Script(
            script_id="s",
            learner_pseudonym="L",
            answers=(Answer(question_id="1", text="a"), Answer(question_id="1", text="b")),
        )


def test_a_pack_may_have_no_cognitive_grid() -> None:
    pack = _pack(40, 60).model_copy(update={"cognitive": None})
    assert pack.cognitive is None


def test_catch_up_and_atp_rows_need_every_field() -> None:
    from mothusi_contracts import AtpWeek, CatchUpItem, LedgerLine

    assert (
        AtpWeek(week=38, topic="Functions", source="ATP 2023/24 Gr 10 Maths, Term 4 row 2").week
        == 38
    )
    with pytest.raises(ValidationError):
        CatchUpItem(week=0, topic="t", activity="a", atp_source="s")
    line = LedgerLine(
        line_id="1",
        description="Bank charges",
        amount=Decimal("120.00"),
        foreign=False,
        marks_scored=1,
    )
    assert line.marks_scored == 1


def test_memo_item_question_is_optional_and_kept() -> None:
    bare = MemoItem(question_id="2.3", max_marks=4, answer="x = -4", level="routine_procedures")
    assert bare.question == ""
    asked = MemoItem(
        question_id="2.3",
        question="Solve for x: 2^(x+1) = 1/8",
        max_marks=4,
        answer="x = -4",
        level="routine_procedures",
    )
    assert asked.question == "Solve for x: 2^(x+1) = 1/8"
