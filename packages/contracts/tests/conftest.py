"""Shared fixtures: a small Grade 10 Mathematics test, its memo and one marked script."""

from __future__ import annotations

import pytest

from mothusi_contracts import Answer, Mark, MarkedScript, Memo, MemoItem, Script


@pytest.fixture
def memo() -> Memo:
    return Memo(
        assessment_id="gr10-maths-t1-test",
        total_marks=10,
        items=(
            MemoItem(
                question_id="1.1",
                max_marks=4,
                answer="x = 3 or x = -2",
                marking_notes="1 mark factorising, 1 mark each root, 1 mark both stated",
                level="routine_procedures",
            ),
            MemoItem(question_id="1.2", max_marks=6, answer="y = 2x + 1", level="knowledge"),
        ),
    )


@pytest.fixture
def script() -> Script:
    return Script(
        script_id="s-014",
        learner_pseudonym="L-014",
        answers=(
            Answer(question_id="1.1", text="(x-3)(x+2)=0 so x=3 or x=-2"),
            Answer(question_id="1.2", text="gradient 2, intercept 1, y=2x+1"),
        ),
    )


@pytest.fixture
def marked(script: Script) -> MarkedScript:
    return MarkedScript(
        script_id=script.script_id,
        marker="marker-agent",
        marks=(
            Mark(
                question_id="1.1",
                awarded=4,
                max_marks=4,
                evidence_quote="(x-3)(x+2)=0 so x=3 or x=-2",
                memo_line="x = 3 or x = -2",
                principle="full marks: both roots with factorisation shown",
            ),
            Mark(
                question_id="1.2",
                awarded=6,
                max_marks=6,
                evidence_quote="y=2x+1",
                memo_line="y = 2x + 1",
                principle="answer matches memo",
            ),
        ),
        total=10,
    )
