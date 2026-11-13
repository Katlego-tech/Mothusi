"""Identify positive marks that use text still flagged as uncertain."""

import re

from mothusi_contracts import MarkedScript, Script


def flag_uncertain_evidence(marked: MarkedScript, script: Script) -> tuple[tuple[str, str], ...]:
    """Return (question_id, original quote) once per overlapping positive Mark, in mark order.

    Compare only the Answer with the Mark's question_id, and only lines indexed by its
    current uncertain_lines (zero-based into text.split("\n")). Collapse whitespace runs
    to a single space and strip both quote and line; preserve case and all symbols.
    Overlap means the normalised line is a substring of the normalised quote, or vice versa.
    Empty lines never overlap. For lines of 1-2 normalised characters, require the entire
    line to occur as a whole token in the quote: neither adjacent character may be a Unicode
    word character (letter, digit or underscore). Thus 'x' matches '(x)' but not 'extra',
    '2x' or 'x_value'; a shorter quote within such a line does not suffice. Partial shared
    runs without containment never overlap. No fuzzy or positional matching is applied.

    Zero marks, missing answers and answers without uncertain lines yield no flags. Evidence
    validity belongs to check_evidence; this function does not validate or change any mark.
    Raise ValueError for differing script_ids, as the scripts cannot be paired.
    """
    if marked.script_id != script.script_id:
        raise ValueError("marked and script must have the same script_id")

    uncertain_lines: dict[str, tuple[str, ...]] = {}
    for answer in script.answers:
        lines = answer.text.split("\n")
        uncertain_lines[answer.question_id] = tuple(
            " ".join(lines[index].split()) for index in answer.uncertain_lines
        )

    flags: list[tuple[str, str]] = []
    for mark in marked.marks:
        if mark.awarded == 0:
            continue
        quote = " ".join(mark.evidence_quote.split())
        if not quote:
            continue
        for line in uncertain_lines.get(mark.question_id, ()):
            if not line:
                continue
            if len(line) <= 2:
                overlaps = re.search(r"(?<!\w)" + re.escape(line) + r"(?!\w)", quote) is not None
            else:
                overlaps = line in quote or quote in line
            if overlaps:
                flags.append((mark.question_id, mark.evidence_quote))
                break
    return tuple(flags)
