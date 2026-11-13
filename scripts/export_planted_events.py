"""Write apps/web/src/run/__fixtures__/planted-events.jsonl: the planted run's event log.

    uv run --frozen python scripts/export_planted_events.py

Also writes planted-bundle.json, the run's Bundle. The console's reducer and screen tests fold
this real log (the smoke's run, in replay mode), so the reducer is
tested against what the core service actually emits, not a hand-written imitation of it. Re-run it
when an event payload changes.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from mothusi_core.graph.planner import Planner
from mothusi_core.providers import free
from mothusi_core.smoke import REPLAY, check_planted, run_planted
from mothusi_llm import ReplayProvider

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps/web/src/run/__fixtures__/planted-events.jsonl"
BUNDLE = ROOT / "apps/web/src/run/__fixtures__/planted-bundle.json"


def main() -> int:
    ticks = iter(range(10_000))
    start = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)

    def clock() -> datetime:  # two seconds an event: a deterministic, readable timeline
        return start + timedelta(seconds=2 * next(ticks))

    with tempfile.TemporaryDirectory() as tmp:
        planner = Planner(
            primary=ReplayProvider(REPLAY, free),
            fallback=None,
            data_dir=Path(tmp),
            checkpointer=InMemorySaver(),
            clock=clock,
        )
        state, events, names = asyncio.run(run_planted(planner.primary, Path(tmp), planner=planner))
        assert check_planted(state, events, names) == []
        OUT.write_text("".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8")
        # The bundle as GET /bundle returns it at awaiting_approval (synthetic names).
        bundle = {**state.get("bundle", {}), "state": "awaiting_approval"}
        BUNDLE.write_text(json.dumps(bundle, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(events)} events to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
