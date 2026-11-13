"""Every mark above zero cites a memo line that is really in the memo.

Design: docs/architecture/design/rules-engine.md §3 citations.py. The sibling of check_evidence:
that one grounds
the mark in the learner's words, this one grounds it in the teacher's memo. Pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mothusi_contracts import CatchUpItem, CheckResult, MarkedScript, Memo

MEMO_LINE = "memo_line"


def _normalise(text: str) -> str:
    return " ".join(text.split())


def check_memo_lines(marked: MarkedScript, memo: Memo) -> CheckResult:
    """For each mark above zero, memo_line is a substring of that question's answer or notes."""
    items = {item.question_id: item for item in memo.items}
    failures: list[str] = []
    for mark in marked.marks:
        if mark.awarded == 0:
            continue
        item = items.get(mark.question_id)
        if item is None:
            failures.append(f"{mark.question_id}: not in the memo")
            continue
        cited = _normalise(mark.memo_line)
        sources = (_normalise(item.answer), _normalise(item.marking_notes))
        if not cited or not any(cited in source for source in sources if source):
            failures.append(f"{mark.question_id}: memo line not found in the memo")
    return CheckResult(
        check=MEMO_LINE,
        about=marked.script_id,
        passed=not failures,
        detail="; ".join(sorted(failures)) or "every mark above zero cites a line of the memo",
    )


def check_citations(
    plan: Sequence[CatchUpItem],
    cited: Mapping[str, Sequence[str]],
    question_of: Sequence[str],
) -> CheckResult:
    """Require each non-empty citation to copy a passage given for that item's question."""
    failures: list[str] = []
    for index, item in enumerate(plan):
        if not item.citation:
            continue
        if index >= len(question_of) or item.citation not in cited.get(question_of[index], ()):
            failures.append(f"{item.topic}: citation not in passages given for this question")
    return CheckResult(
        check="citations",
        about="catch-up plan",
        passed=not failures,
        detail="; ".join(failures) or "every citation copies a passage given for its question",
    )
