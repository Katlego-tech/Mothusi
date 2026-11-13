"""Memo drafts (intake.md §12): a typed memo PDF or Word file read into rows the teacher confirms.

A draft is made in the background (the Setter's call can outlast CloudFront's 60 s) and polled by
the console. Drafts live in memory for two hours and hold no learner data: only the memo.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from mothusi_core.agents.setter import DraftItem, MemoDraftError, draft_memo
from mothusi_llm import Provider

log = logging.getLogger(__name__)
TTL_S = 2 * 3600


@dataclass
class Draft:
    draft_id: str
    source_name: str
    levels: tuple[str, ...]
    state: Literal["drafting", "ready", "failed"] = "drafting"
    total_marks: int | None = None
    items: tuple[DraftItem, ...] = ()
    problems: tuple[str, ...] = ()
    message: str = ""
    created: float = field(default_factory=time.monotonic)

    def view(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "state": self.state,
            "source_name": self.source_name,
            "total_marks": self.total_marks,
            "items": [i.model_dump(mode="json") for i in self.items],
            "problems": list(self.problems),
            "levels": list(self.levels),
            "message": self.message,
        }


class MemoDrafts:
    def __init__(self) -> None:
        self._drafts: dict[str, Draft] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def start(
        self, text: str, source_name: str, levels: tuple[str, ...], provider: Provider
    ) -> Draft:
        self._expire()
        draft = Draft(secrets.token_urlsafe(12), source_name, levels)
        self._drafts[draft.draft_id] = draft

        async def work() -> None:
            try:
                result = await draft_memo(text, provider, levels)
            except MemoDraftError as error:
                draft.state, draft.message = "failed", str(error)
                return
            except Exception:  # never leave a teacher polling a draft that stopped
                log.exception("memo draft %s failed", draft.draft_id)
                draft.state = "failed"
                draft.message = "the memo could not be read; try again, or upload the TOML"
                return
            draft.total_marks, draft.items, draft.problems = (
                result.total_marks,
                result.items,
                result.problems,
            )
            draft.state = "ready"

        task = asyncio.create_task(work())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return draft

    def get(self, draft_id: str) -> Draft | None:
        self._expire()
        return self._drafts.get(draft_id)

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, d in self._drafts.items() if now - d.created > TTL_S]:
            del self._drafts[key]
