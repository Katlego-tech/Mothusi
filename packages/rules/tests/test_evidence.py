"""Contract acceptance tests for the delegated rules task."""

import pytest

from mothusi_contracts import MarkedScript, Script
from mothusi_rules import EVIDENCE, check_evidence


def test_matching_evidence(marked: MarkedScript, script: Script) -> None:
    result = check_evidence(marked, script)
    assert result.passed and result.about == script.script_id
    assert result.check == EVIDENCE == "evidence"


def test_whitespace(marked: MarkedScript, script: Script) -> None:
    answer = script.answers[0].model_copy(update={"text": "  (x-3)(x+2)=0\n so\t x=3 or x=-2  "})
    assert check_evidence(
        marked, script.model_copy(update={"answers": (answer, script.answers[1])})
    ).passed


def test_case_and_symbol_changes(marked: MarkedScript, script: Script) -> None:
    for quote in ["Y=2x+1", "y=2x-1"]:
        mark = marked.marks[1].model_copy(update={"evidence_quote": quote})
        result = check_evidence(marked.model_copy(update={"marks": (mark,)}), script)
        assert not result.passed
        assert result.detail == "1.2: quote not found in the answer"


def test_collects_sorted_failures(marked: MarkedScript, script: Script) -> None:
    first = marked.marks[0].model_copy(update={"evidence_quote": "invented"})
    changed = marked.model_copy(update={"marks": (marked.marks[1], first)})
    result = check_evidence(changed, script.model_copy(update={"answers": (script.answers[0],)}))
    assert result.detail == "1.1: quote not found in the answer; 1.2: no answer for this question"


def test_zero_mark_is_not_checked(marked: MarkedScript, script: Script) -> None:
    zero = marked.marks[0].model_copy(update={"awarded": 0, "evidence_quote": ""})
    assert check_evidence(
        marked.model_copy(update={"marks": (zero,)}), script.model_copy(update={"answers": ()})
    ).passed


def test_wrong_pairing(marked: MarkedScript, script: Script) -> None:
    with pytest.raises(ValueError, match="script"):
        check_evidence(marked, script.model_copy(update={"script_id": "other"}))
