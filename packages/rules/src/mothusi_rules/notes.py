"""The Reviewer's deterministic grounding and non-labelling checks for learner notes."""

import re

from mothusi_contracts import CheckResult, LearnerNote, MarkedScript, Script

NOTE_GROUNDING = "note_grounding"
_QUOTES = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d|\u00ab([^\u00bb]+)\u00bb|`([^`]+)`')
_LABELS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(is|are|was|were)\s+(weak|poor|slow|lazy|bad|hopeless|careless)\b",
        r"\b(better|worse|slower|faster)\s+than\b",
        r"\b(top|bottom)\s+of\s+the\s+class\b",
        r"\bnot\s+(clever|smart|bright|good\s+at)\b",
        r"\b(can['\u2019]t|cannot|can\s+not)\s+do\b",
    )
)
_MARK = re.compile(r"\b(\d+)\s*(?:/|out\s+of)\s*(\d+)\b", re.IGNORECASE)
_PERCENT = re.compile(r"\b(\d+)\s*%")
_PRAISE = re.compile(r"\b(well done|excellent|brilliant)\b", re.IGNORECASE)


def check_note_grounding(note: LearnerNote, script: Script, marked: MarkedScript) -> CheckResult:
    """Report all unsupported quotes, marks, labels and evidence-free praise."""
    if script.script_id != marked.script_id or note.pseudonym != script.learner_pseudonym:
        raise ValueError("note, script and marked script must refer to the same learner")
    failures: list[str] = []
    quotes = [
        span
        for match in _QUOTES.finditer(note.mark_with_quote)
        if (span := next(g for g in match.groups() if g)).strip()
    ]
    answers = [" ".join(answer.text.split()) for answer in script.answers]
    if not quotes:
        failures.append("no quote from the learner's work")
    for span in quotes:
        if not any(" ".join(span.split()) in answer for answer in answers):
            failures.append(f'quote not found in the script: "{span}"')
    valid_marks = {(mark.awarded, mark.max_marks) for mark in marked.marks}
    valid_marks.add((marked.total, sum(mark.max_marks for mark in marked.marks)))
    for match in _MARK.finditer(note.mark_with_quote):
        awarded, maximum = map(int, match.groups())
        if (awarded, maximum) not in valid_marks:
            failures.append(f"mark {awarded}/{maximum} is not in the marked script")
    # A percentage is a mark too: it must be one the marked script actually gives.
    valid_percents = {round(a * 100 / m) for a, m in valid_marks if m}
    for match in _PERCENT.finditer(note.mark_with_quote):
        percent = int(match.group(1))
        if percent not in valid_percents:
            failures.append(f"mark {percent}% is not in the marked script")
    for text in (note.outcome, note.mark_with_quote, note.next_task):
        for pattern in _LABELS:
            failures.extend(
                f"labels the learner: '{match.group()}'" for match in pattern.finditer(text)
            )
    if not quotes and _PRAISE.search(note.mark_with_quote):
        failures.append("praise without a quote")
    return CheckResult(
        check=NOTE_GROUNDING,
        about=note.pseudonym,
        passed=not failures,
        detail="; ".join(sorted(failures)) or "note is grounded",
    )
