"""Write services/core/fixtures/gr10-maths-t1/replay-6.jsonl: the planted run's scripted replies.

    uv run --frozen python scripts/record_replay.py

Runs the real Planner graph on the 6-script planted set against the scripted provider in
services/core/fixtures/scripted_model.py (NOT a model), wrapped in Recording, so every prompt the
graph builds is keyed to its scripted reply. Re-run it whenever a prompt changes; the smoke then
replays the file without the scripted provider (marking-pipeline.md §6.2).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/core/fixtures"))

from scripted_model import ScriptedModel  # noqa: E402

from mothusi_core.providers import Recording  # noqa: E402
from mothusi_core.smoke import REPLAY, check_planted, run_planted  # noqa: E402


def main() -> int:
    REPLAY.unlink(missing_ok=True)
    REPLAY.touch()
    with tempfile.TemporaryDirectory() as tmp:
        state, events, names = asyncio.run(
            run_planted(Recording(ScriptedModel(), REPLAY), Path(tmp))
        )
    failures = check_planted(state, events, names)
    lines = sum(1 for _ in REPLAY.open(encoding="utf-8"))
    print(f"recorded {lines} replies to {REPLAY.relative_to(ROOT)}; {len(events)} events")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
