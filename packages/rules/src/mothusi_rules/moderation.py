"""Reproducible blind moderation sampling and disagreement decisions."""

import random
from collections.abc import Sequence
from decimal import Decimal
from math import ceil

from mothusi_contracts import Disagreement, MarkedScript, ModerationRule


def moderation_sample(
    marked: Sequence[MarkedScript], rule: ModerationRule, seed: int = 0
) -> tuple[str, ...]:
    ordered = sorted(marked, key=lambda script: (-script.total, script.script_id))
    count = min(
        len(ordered), max(rule.min_scripts, ceil(Decimal(len(ordered)) * rule.sample_percent / 100))
    )
    ids = [script.script_id for script in ordered]
    if rule.spread == "random":
        return tuple(sorted(random.Random(seed).sample(ids, count)))
    size, extra = divmod(len(ids), 3)
    thirds: list[list[str]] = []
    offset = 0
    for index in range(3):
        end = offset + size + (index < extra)
        thirds.append(ids[offset:end])
        offset = end
    sampled: list[str] = []
    for index in range(size + bool(extra)):
        for third in thirds:
            if index < len(third):
                sampled.append(third[index])
    return tuple(sampled[:count])


def disagreements(first: MarkedScript, second: MarkedScript) -> tuple[Disagreement, ...]:
    if first.script_id != second.script_id:
        raise ValueError("marks must have the same script_id")
    first_marks = {mark.question_id: mark.awarded for mark in first.marks}
    second_marks = {mark.question_id: mark.awarded for mark in second.marks}
    if first_marks.keys() != second_marks.keys():
        raise ValueError("scripts must mark the same set of question_ids")
    return tuple(
        Disagreement(
            script_id=first.script_id,
            question_id=qid,
            first=awarded,
            second=second_marks[qid],
            reason="",
        )
        for qid, awarded in first_marks.items()
        if awarded != second_marks[qid]
    )


def should_widen(
    disagreements: Sequence[Disagreement], questions_compared: int, rule: ModerationRule
) -> bool:
    if questions_compared <= 0:
        raise ValueError("questions_compared must be positive")
    return Decimal(len(disagreements)) / questions_compared * 100 > rule.widen_above_gap_percent
