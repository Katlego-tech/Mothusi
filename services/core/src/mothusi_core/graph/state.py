"""The Planner's checkpointed state (marking-pipeline.md §6.2 RunState).

Plain JSON values only (contracts as `model_dump(mode="json")`), so any checkpointer, in memory or
Postgres, stores it unchanged. `names` maps pseudonym to name: it is the teacher's, read only when
the bundle is built, and never copied into a Brief or an event.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

type Json = dict[str, Any]


class RunState(TypedDict):
    assessment_id: str
    state: str
    title: str
    goal: str
    pack_path: str
    poa_task: str
    budget_zar: str
    started_at: str
    memo: Json
    scripts: list[Json]
    names: dict[str, str]
    atp_weeks: list[Json]
    mode: NotRequired[
        str
    ]  # "csv" (typed scripts) or "pages" (Mothusi answer sheets, intake.md §11)
    spent_zar: NotRequired[str]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    teacher_steps: NotRequired[int]
    question: NotRequired[Json | None]
    memo_queue: NotRequired[list[Json]]
    memo_amended: NotRequired[bool]
    memo_unverified: NotRequired[list[Json]]
    marked: NotRequired[dict[str, Json]]
    pending: NotRequired[dict[str, list[Json]]]
    sample: NotRequired[list[str]]
    moderated: NotRequired[dict[str, Json]]
    disagreements: NotRequired[list[Json]]
    questions_compared: NotRequired[int]
    widened: NotRequired[bool]
    moderation_closed: NotRequired[bool]
    re_marked: NotRequired[list[str]]
    resolutions: NotRequired[dict[str, str]]
    converted: NotRequired[dict[str, Json]]
    item_stats: NotRequired[list[Json]]
    misconceptions: NotRequired[list[Json]]
    catch_up_plan: NotRequired[list[Json]]
    drafts: NotRequired[Json]
    verdicts: NotRequired[list[Json]]
    class_sheet_review: NotRequired[Json]
    class_sheet_reason: NotRequired[str | None]
    review_incomplete: NotRequired[str | None]
    rewrite: NotRequired[dict[str, str]]
    review_rounds: NotRequired[int]
    teacher_note: NotRequired[str | None]
    bundle: NotRequired[Json]
    approval: NotRequired[Json]
    delivered: NotRequired[Json]
    halted: NotRequired[str | None]
