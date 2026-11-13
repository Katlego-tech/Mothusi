"""The walking skeleton: the 6-script planted set through the whole graph, in replay mode.

`uv run --frozen python -m mothusi_core.smoke` (the smoke check). It replays
services/core/fixtures/gr10-maths-t1/replay-6.jsonl (scripted replies, marking-pipeline.md §6.2),
answers the teacher's questions the way the demo does (take the Setter's answer for memo 2.3,
approve), and exits non-zero unless the run is delivered along §5's states with every planted
trap caught and no learner name anywhere in the log.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from mothusi_contracts import Event, EventKind
from mothusi_core.graph.planner import Planner, new_run
from mothusi_core.graph.state import RunState
from mothusi_core.providers import ROOT, free
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import Provider, ReplayProvider
from mothusi_rules import load_atp_weeks

FIXTURE = ROOT / "services/core/fixtures/gr10-maths-t1"
REPLAY = FIXTURE / "replay-6.jsonl"
PACK = ROOT / "packs/dbe-caps/mathematics-gr10.toml"
PLANTED = ("L-001", "L-003", "L-007", "L-010", "L-014", "L-021")
ASSESSMENT = "gr10-maths-t1-planted"
GOAL = "Mark my Grade 10 Term 1 Maths test and get it ready for my HOD."

EXPECTED_STATES = [
    ("draft", "memo_check"),
    ("memo_check", "draft"),
    ("draft", "memo_check"),
    ("memo_check", "marking"),
    ("marking", "moderating"),
    ("moderating", "re_marking"),
    ("re_marking", "moderating"),
    ("moderating", "analysing"),
    ("analysing", "writing"),
    ("writing", "reviewing"),
    ("reviewing", "writing"),
    ("writing", "reviewing"),
    ("reviewing", "awaiting_approval"),
    ("awaiting_approval", "approved"),
    ("approved", "delivered"),
]


async def run_planted(
    primary: Provider, data_dir: Path, *, planner: Planner | None = None
) -> tuple[RunState, tuple[Event, ...], dict[str, str]]:
    """Run the planted set to the end; return the final state, the log and the names."""
    planner = planner or Planner(
        primary=primary, fallback=None, data_dir=data_dir, checkpointer=InMemorySaver()
    )
    memo = load_memo(FIXTURE / "memo.toml")
    scripts, by_name = load_scripts(FIXTURE / "scripts.csv")
    names = {pseudonym: name for name, pseudonym in by_name.items() if pseudonym in PLANTED}
    state = await planner.start(
        new_run(
            planner,
            assessment_id=ASSESSMENT,
            goal=GOAL,
            memo=memo,
            scripts=[x for x in scripts if x.script_id in PLANTED],
            names=names,
            pack_path=PACK,
            poa_task="Test",
            budget_zar=Decimal("40"),
            atp_weeks=load_atp_weeks(FIXTURE / "atp-weeks.toml"),
        )
    )
    for _ in range(30):
        question = state.get("question")
        if question and question["topic"] == "memo":
            answer = {"decision": "use_proposed", "question_id": question["question_id"]}
        elif question:
            raise RuntimeError(f"the planted set should not ask about {question['topic']}")
        elif state["state"] == "awaiting_approval":
            answer = {"decision": "approve", "by": "the teacher"}
        else:
            break
        state = await planner.resume(ASSESSMENT, answer)
    return state, planner.sink(ASSESSMENT).read(), names


def check_planted(state: RunState, events: tuple[Event, ...], names: dict[str, str]) -> list[str]:
    """Every failure of the planted run against marking-pipeline.md §5 and traps.md."""
    failures: list[str] = []
    moves = [
        (str(e.payload["from"]), str(e.payload["to"])) for e in events if e.kind is EventKind.STATE
    ]
    if moves != EXPECTED_STATES:
        failures.append(f"state sequence {moves} is not {EXPECTED_STATES}")
    if state["state"] != "delivered":
        failures.append(f"the run ended in {state['state']}, not delivered")
    first_marker = next(
        (e.seq for e in events if e.kind is EventKind.BRIEF and e.payload["agent"] == "marker"),
        None,
    )
    memo_flag = next(
        (
            e.seq
            for e in events
            if e.kind is EventKind.NEEDS_TEACHER and e.payload.get("question_id") == "2.3"
        ),
        None,
    )
    if memo_flag is None or first_marker is None or memo_flag > first_marker:
        failures.append("memo 2.3 was not flagged before marking began")
    evidence = [
        e
        for e in events
        if e.kind is EventKind.CHECK
        and e.payload["about"] == "L-014"
        and e.payload["check"] == "evidence"
        and not e.payload["passed"]
    ]
    retried = [
        e
        for e in events
        if e.kind is EventKind.RESULT
        and e.payload["script_id"] == "L-014"
        and str(e.payload["brief_id"]).endswith("/L-014/marker/2")
        and e.payload["status"] == "done"
    ]
    if not evidence or not retried:
        failures.append("L-014's tidied quote was not refused and re-marked on attempt 2")
    if not any(
        e.kind is EventKind.DISAGREEMENT
        and e.payload["script_id"] == "L-021"
        and e.payload["question_id"] == "4"
        for e in events
    ):
        failures.append("the L-021 Q4 over-mark was not caught by the blind moderator")
    final: dict[str, Any] = state.get("marked", {}).get("L-021", {})
    marks: list[dict[str, Any]] = final.get("marks", [])
    empty: dict[str, Any] = {}
    q4 = next((m for m in marks if m["question_id"] == "4"), empty)
    if q4.get("awarded") != 2:
        failures.append(f"L-021 Q4 ends at {q4.get('awarded')}, not the memo's 2")
    if not any(
        e.kind is EventKind.DECISION and str(e.payload["reason"]).startswith("L-007:")
        for e in events
    ):
        failures.append("the Reviewer did not reject the planted L-007 note")
    approved_at = next(
        (e.seq for e in events if e.kind is EventKind.STATE and e.payload["to"] == "approved"),
        None,
    )
    for e in events:
        executor = e.kind is EventKind.BRIEF and e.payload["agent"] == "executor"
        if executor and (approved_at is None or e.seq < approved_at):
            failures.append("an Executor brief exists before approved")
    raw = "\n".join(e.model_dump_json() for e in events)
    leaked = sorted(n for n in names.values() if n in raw)
    if leaked:
        failures.append(f"learner names in the event log: {leaked}")
    return failures


SMOKE_NOTE_KEY = "smoke-note-key-not-a-secret-0123456789abcdef"


def main() -> int:
    # The smoke delivers into a temporary folder; its note links are signed with a fixed,
    # throwaway key unless the environment gives one (./hack env does).
    os.environ.setdefault("MOTHUSI_NOTE_KEY", SMOKE_NOTE_KEY)
    os.environ.setdefault("MOTHUSI_PUBLIC_URL", "https://mothusi.local")
    provider = ReplayProvider(REPLAY, free)
    with tempfile.TemporaryDirectory() as tmp:
        state, events, names = asyncio.run(run_planted(provider, Path(tmp)))
        failures = check_planted(state, events, names)
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if not failures:
        print(f"smoke: planted set delivered in {len(events)} events; every trap caught")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
