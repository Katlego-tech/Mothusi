"""Scripted replies for the replay fixture. NOT a model, and never used in a live run.

`scripts/record_replay.py` runs the real Planner graph against this provider, wrapped in
`mothusi_core.providers.Recording`, to write `gr10-maths-t1/replay-6.jsonl`; the smoke and the graph
test then replay that file. It answers each agent from the fixture and the four planted traps in
`gr10-maths-t1/traps.md`, so a replay exercises the Planner, the rules and the retries, never a
model's judgement (marking-pipeline.md §6.2). Marks follow one simple rule: a final line
that `equivalent` reads as the memo answer earns full marks, anything else earns 0, except where a
trap says otherwise.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from mothusi_contracts import Contract
from mothusi_core.agents.marker import MODERATOR_PREAMBLE
from mothusi_llm import Usage
from mothusi_rules import equivalent

FIXTURE = Path(__file__).resolve().parent / "gr10-maths-t1"
SETTER_ANSWERS = {"2.3": ("x = -4", "1/8 = 2^(-3), so x + 1 = -3 and x = -4")}

# What the class misunderstood, per item, as a head of department would phrase it.
PATTERNS = {
    "1.1": "Factors are found but the roots are given with the wrong signs",
    "1.2": "The sign of -2(a^2 - 6a) is not carried through, and (a - 2)^2 is expanded as a^2 + 4",
    "1.3": "Terms are cancelled across a sum instead of cancelling common factors",
    "2.1": "The exponent outside the bracket is applied to only some factors",
    "2.2": "Bases are combined before each is written as a power of 3",
    "2.3": "1/8 is not written as a power of 2 before the exponents are equated",
    "3.1": "The substitution of y = 2x - 1 loses the sign of the -1",
    "3.2": "The inequality is solved as an equation, or the sign is not reversed",
    "4": "The perimeter is written as w + l = 48 instead of 2(w + l) = 48",
}
_FRACTION = re.compile(r"\d+\s*/\s*\d+|%")


def _usage(user: str) -> Usage:
    return Usage(
        model="scripted", input_tokens=len(user) // 4, output_tokens=200, cost_zar=Decimal(0)
    )


def _memo_answers() -> dict[str, str]:
    with (FIXTURE / "memo.toml").open("rb") as source:
        return {i["question_id"]: i["answer"] for i in tomllib.load(source)["item"]}


def _final(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _matches(memo_answer: str, final: str) -> bool:
    try:
        return equivalent(memo_answer, final.lstrip("= ").strip())
    except ValueError:
        return False


def _section(user: str, start: str, stops: tuple[str, ...]) -> list[str]:
    lines = user.splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start)) + 1
    out: list[str] = []
    for line in lines[begin:]:
        if any(line.startswith(s) for s in stops):
            break
        out.append(line)
    return out


def _answers(user: str, header: str) -> dict[str, str]:
    """The verbatim answers after `header`, keyed by question id ("[qid]" lines)."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    stops = (
        "A blind second marker",
        "Your previous attempt",
        "Reply as JSON",
        "What the class",
        "The Reviewer rejected",
        "The teacher sent",
        "The class's checked",
        "The last attempt",
    )
    for line in _section(user, header, stops):
        head = re.fullmatch(r"\[([^\]]+)\]( \(no answer\))?", line)
        if head:
            current = None if head.group(2) else head.group(1)
            if current:
                found[current] = []
        elif current is not None:
            found[current].append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}


def _memo_items(user: str) -> list[tuple[str, int, str]]:
    """(question_id, max_marks, memo answer) from the Marker's memo section."""
    lines = user.splitlines()
    items: list[tuple[str, int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"\[([^\]]+)\] \((\d+) marks\)", line)
        if m and i + 1 < len(lines) and lines[i + 1].startswith("  memo answer: "):
            items.append((m.group(1), int(m.group(2)), lines[i + 1][len("  memo answer: ") :]))
    return items


def _mark(
    qid: str, awarded: int, max_marks: int, quote: str, line: str, principle: str
) -> dict[str, Any]:
    return {
        "question_id": qid,
        "awarded": awarded,
        "max_marks": max_marks,
        "evidence_quote": quote if awarded else "",
        "memo_line": line,
        "principle": principle,
    }


def marker(system: str, user: str) -> dict[str, Any]:
    sid = re.search(r"script (L-\d+)", user)
    assert sid is not None
    learner = sid.group(1)
    moderator = system.startswith(MODERATOR_PREAMBLE)
    re_mark = "A blind second marker disagreed" in user
    retried = "Your previous attempt failed" in user
    answers = _answers(user, "The learner's answers")
    marks: list[dict[str, Any]] = []
    for qid, max_marks, memo_answer in _memo_items(user):
        text = answers.get(qid)
        if text is None:
            marks.append(_mark(qid, 0, max_marks, "", memo_answer, "no attempt"))
            continue
        final = _final(text)
        if learner == "L-014" and qid == "3.1":
            # Trap: the tidy line "x = 2 and y = 3" is not in this learner's working.
            quote = "x = 2 and y = 3" if not (moderator or retried) else "7x = 14 so x = 2"
            marks.append(
                _mark(qid, max_marks, max_marks, quote, memo_answer, "answer matches memo")
            )
        elif learner == "L-021" and qid == "4":
            # Trap: the perimeter is not doubled; only l = 2w + 3 and the units earn marks.
            if moderator or re_mark:
                principle = "perimeter not doubled: only l = 2w + 3 and the units earn marks"
                marks.append(_mark(qid, 2, max_marks, "l = 2w + 3", memo_answer, principle))
            else:
                principle = "CA marking: follows the learner's equation through"
                marks.append(_mark(qid, 5, max_marks, final, memo_answer, principle))
        elif _matches(memo_answer, final):
            marks.append(
                _mark(qid, max_marks, max_marks, final, memo_answer, "answer matches memo")
            )
        else:
            marks.append(_mark(qid, 0, max_marks, "", memo_answer, "answer does not match memo"))
    return {"marks": marks}


def setter(user: str) -> dict[str, Any]:
    memo = _memo_answers()
    out: list[dict[str, str]] = []
    for line in user.splitlines():
        m = re.match(r"\[([^\]]+)\] \(\d+ marks\)", line)
        if m:
            qid = m.group(1)
            answer, working = SETTER_ANSWERS.get(qid, (memo[qid], ""))
            out.append({"question_id": qid, "answer": answer, "working": working})
    return {"answers": out}


def analyst(user: str) -> dict[str, Any]:
    drafts: list[dict[str, Any]] = []
    lines = user.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\[([^\]]+)\] \(\d+ marks, facility", line)
        if not m:
            continue
        qid = m.group(1)
        quote = ""
        for nxt in lines[i + 1 :]:
            if re.match(r"\[[^\]]+\] \(", nxt) or nxt.startswith("Reply as JSON"):
                break
            if nxt.startswith("    ") and len(nxt.strip()) >= 3:
                quote = nxt.strip()
                break
        drafts.append(
            {
                "question_id": qid,
                "pattern": PATTERNS.get(qid, f"Errors in the working of {qid}"),
                "evidence_quotes": [quote],
            }
        )
    flags = ["4"] if any(d["question_id"] == "4" for d in drafts) else []
    return {"misconceptions": drafts, "language_load_flags": flags}


def support(user: str) -> dict[str, Any]:
    found = re.findall(r"^\[([^\]]+)\] (.+) \(\d+ scripts\)$", user, re.MULTILINE)
    weeks = re.findall(r"^(\d+) \| (.+?) \| (.+)$", user, re.MULTILINE)
    items: list[dict[str, Any]] = []
    for n, (qid, pattern) in enumerate(found):
        week, topic, source = weeks[n % len(weeks)]
        items.append(
            {
                "question_id": qid,
                "week": int(week),
                "topic": f"Re-teach question {qid} beside {topic.split(':')[0]}",
                "activity": (
                    f"Ten-minute starter on {pattern[0].lower() + pattern[1:]}, "
                    "then two worked examples"
                ),
                "atp_source": source,
            }
        )
    return {"items": items}


def _quote_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 3 and not _FRACTION.search(line):
            return line
    return ""


def note(user: str) -> dict[str, Any]:
    learner = re.search(r"^Learner: (L-\d+)", user, re.MULTILINE)
    assert learner is not None
    total = re.search(r"^Total: (\d+)/(\d+)", user, re.MULTILINE)
    assert total is not None
    marks = re.findall(r"^\[([^\]]+)\] (\d+)/(\d+), ", user, re.MULTILINE)
    answers = _answers(user, "The learner's answers")
    scored = sorted(
        ((int(a), int(m), q) for q, a, m in marks if _quote_line(answers.get(q, ""))),
        key=lambda x: (-x[0] / x[1], x[2]),
    )
    weakest = min(marks, key=lambda x: (int(x[1]) / int(x[2]), x[0]))[0]
    rejected = "The Reviewer rejected your last note" in user
    if learner.group(1) == "L-007" and not rejected:
        # Trap: L-007 has full marks on algebra; this label is what the Reviewer must refuse.
        outcome = "L-007 is weak at algebra and needs more practice with equations"
    else:
        outcome = "Solving equations and working with exponents (Term 1 Test)"
    if learner.group(1) == "L-007" and rejected and "2.1" in answers:
        a, m, q = next((x for x in scored if x[2] == "2.1"), (0, 6, "2.1"))
        weakest = "2.1"
    elif scored:
        a, m, q = scored[0]
    else:
        return {
            "outcome": outcome,
            "mark_with_quote": f"{total.group(1)}/{total.group(2)} overall",
            "next_task": f"Attempt question {weakest} step by step, using the memo method",
            "self_assessment_prompt": f"What is my first step for question {weakest}?",
        }
    quote = _quote_line(answers[q])
    return {
        "outcome": outcome,
        "mark_with_quote": f'{a}/{m} on question {q}: you wrote "{quote}"',
        "next_task": f"Redo question {weakest}, checking each line against the memo method",
        "self_assessment_prompt": f"Which step of question {weakest} would I check first, and why?",
    }


def corrections(user: str) -> dict[str, Any]:
    lines = user.splitlines()
    out: list[dict[str, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"\[([^\]]+)\] \(facility", line)
        if not m:
            continue
        qid = m.group(1)
        notes = next(
            (
                x[len("  marking notes: ") :]
                for x in lines[i + 1 : i + 4]
                if x.startswith("  marking notes: ")
            ),
            "the memo's steps",
        )
        out.append(
            {
                "question_id": qid,
                "common_error": PATTERNS.get(qid, f"Errors in the working of {qid}"),
                "worked_correction": f"Work it on the board in the memo's steps: {notes}",
            }
        )
    return {"corrections": out}


def tone(user: str) -> dict[str, Any]:
    names = re.findall(r"^<(L-\d+)>$", user, re.MULTILINE)
    return {
        "verdicts": [{"pseudonym": n, "passed": True, "reason": ""} for n in names],
        "class_sheet_passed": True,
        "class_sheet_reason": "",
    }


class ScriptedModel:
    """Answers every agent's output type from the fixture. Recorded once; replayed everywhere."""

    name = "scripted"

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        answer = {
            "MarkerReply": lambda: marker(system, user),
            "SetterReply": lambda: setter(user),
            "AnalystReply": lambda: analyst(user),
            "SupportReply": lambda: support(user),
            "NoteReply": lambda: note(user),
            "CorrectionsReply": lambda: corrections(user),
            "ToneReply": lambda: tone(user),
        }[output.__name__]()
        return output.model_validate(answer), _usage(user)
