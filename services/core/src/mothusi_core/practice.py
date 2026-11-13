"""Practice tests: set from an ATP week, checked by code, sat blind, approved once.

learner-section.md §4.1, §5, §8. The Setter sets the test and memo from the class pack's ATP
week and the retrieved CAPS/ATP passages; code checks the memo, its citations and the cognitive
grid; the Setter then sits it blind like any memo. A test with a named problem cannot be approved.
Records live beside their class (classes/<id>/tests/ on files, learners.practice_test in
Postgres), so closing a class deletes them too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.rows import DictRow, dict_row
from pydantic import Field

from mothusi_contracts import (
    AgentRole,
    Answer,
    AtpWeek,
    Attempt,
    Brief,
    Contract,
    MarkedScript,
    Memo,
    Passage,
    PassageKind,
    PracticeQuestionResult,
    PracticeResult,
    PracticeTest,
    Script,
    SubjectPack,
)
from mothusi_core.agents.loop import run_with_retries
from mothusi_core.agents.marker import mark_script
from mothusi_core.agents.setter import blocking, set_practice_test, sit_test
from mothusi_core.knowledge import Knowledge, KnowledgeError
from mothusi_llm import Provider
from mothusi_rules import check_cognitive_grid, load_atp_weeks

log = logging.getLogger(__name__)
PACKS = Path(__file__).resolve().parents[4] / "packs"


class PracticeRecord(Contract):
    """A practice test as the teacher sees it: the test once set, and what blocks approval."""

    test_id: str = Field(min_length=1)
    class_id: str = Field(min_length=1)
    atp_week: AtpWeek
    marks: int = Field(ge=10, le=30)
    state: Literal["drafting", "checked", "approved", "open", "closed", "refused"]
    test: PracticeTest | None = None
    problems: tuple[str, ...] = ()
    """Block the teacher's approval."""
    unverified: tuple[str, ...] = ()
    """The blind sitting differed in words code cannot compare: shown for the teacher to read
    before approving, never treated as agreement."""
    message: str = ""


def atp_weeks_for(pack_id: str, packs_dir: Path = PACKS) -> tuple[AtpWeek, ...]:
    """The class pack's full-year ATP weeks: packs/<group>/atp/<pack>.toml."""
    group, _, name = pack_id.partition("/")
    path = packs_dir / group / "atp" / f"{name}.toml"
    if not name or not path.is_file():
        raise ValueError(f"no ATP weeks are held for {pack_id}")
    return load_atp_weeks(path)


async def set_and_check(
    record: PracticeRecord,
    pack: SubjectPack,
    provider: Provider,
    knowledge: Knowledge,
) -> PracticeRecord:
    """Set the test, then check it; returns the record in `checked` (with any problems) or
    `refused` (the Setter could not produce a valid test)."""
    week = record.atp_week
    try:
        passages: Sequence[Passage] = await knowledge.search(
            week.topic,
            k=4,
            subject=pack.subject,
            grade=pack.grade,
            kinds=(PassageKind.ATP, PassageKind.CAPS),
        )
    except KnowledgeError as error:
        log.info(
            "practice %s: knowledge search failed (%s); set from the ATP row", record.test_id, error
        )
        passages = ()
    targets = dict(pack.cognitive.levels) if pack.cognitive else {}

    def brief(attempt: int, previous: str | None) -> Brief:
        return Brief(
            brief_id=f"{record.test_id}/practice/setter/{attempt}",
            assessment_id=record.test_id,
            agent=AgentRole.SETTER,
            task="Set a practice test and memo for one ATP week",
            inputs={
                "atp_week": week.model_dump(mode="json"),
                "passages": [p.model_dump(mode="json") for p in passages],
                "targets": {k: str(v) for k, v in targets.items()},
                "marks": record.marks,
                "subject": pack.subject,
                "grade": pack.grade,
                **({"previous_failure": previous} if previous else {}),
            },
            attempt=attempt,
        )

    outcome = await run_with_retries(brief, lambda b: set_practice_test(b, provider))
    if outcome.status != "done":
        return record.model_copy(
            update={"state": "refused", "message": f"the Setter could not set it: {outcome.reason}"}
        )
    memo = Memo.model_validate(outcome.outputs["memo"])
    problems: list[str] = []
    unverified: tuple[str, ...] = ()
    if pack.cognitive is not None:
        grid = check_cognitive_grid(memo, pack)
        if not grid.passed:
            problems.append(f"cognitive grid: {grid.detail}")
    sat = await run_with_retries(
        lambda n, _p: Brief(
            brief_id=f"{record.test_id}/practice/sit/{n}",
            assessment_id=record.test_id,
            agent=AgentRole.SETTER,
            task="Sit the test blind to the memo; code compares your answers with it",
            inputs={
                "memo": memo.model_dump(mode="json"),
                "pack_conventions": list(pack.conventions),
            },
            attempt=n,
        ),
        lambda b: sit_test(b, provider),
    )
    if sat.status != "done":
        problems.append(f"the Setter could not sit it: {sat.reason}")
    else:
        from mothusi_core.agents.setter import MemoIssue

        issues = blocking(tuple(MemoIssue.model_validate(i) for i in sat.outputs.get("issues", [])))
        problems.extend(
            f"{i.question_id}: the Setter's answer {i.setter_answer!r} {i.kind} the memo's"
            for i in issues
        )
        unverified = tuple(
            f"{i.question_id}: the Setter wrote {i.setter_answer!r}; "
            f"the memo says {i.memo_answer!r}"
            for i in (MemoIssue.model_validate(x) for x in sat.outputs.get("unverified", []))
        )
    test = PracticeTest(
        test_id=record.test_id,
        class_id=record.class_id,
        atp_week=week,
        memo=memo,
        citations=tuple(outcome.outputs.get("citations", ())),
        state="checked",
    )
    return record.model_copy(
        update={
            "state": "checked",
            "test": test,
            "problems": tuple(problems),
            "unverified": unverified,
        }
    )


def approve(record: PracticeRecord, teacher_sub: str, now: datetime) -> PracticeRecord:
    if record.state != "checked" or record.test is None:
        raise ValueError(f"a {record.state} test cannot be approved")
    if record.problems:
        raise ValueError("fix or reset the test first: " + "; ".join(record.problems))
    test = record.test.model_copy(
        update={"state": "approved", "approved_by": teacher_sub, "approved_at": now}
    )
    return record.model_copy(update={"state": "approved", "test": test})


def move(record: PracticeRecord, to: Literal["open", "closed"]) -> PracticeRecord:
    allowed = {"open": ("approved", "closed"), "closed": ("open",)}
    if record.state not in allowed[to] or record.test is None:
        raise ValueError(
            f"a {record.state} test cannot be {'opened' if to == 'open' else 'closed'}"
        )
    test = record.test.model_copy(update={"state": to})
    return record.model_copy(update={"state": to, "test": test})


class ClassGoneError(Exception):
    """The class was deleted while its test was being set; the test is dropped."""


class FilePracticeStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "classes"
        self._lock = asyncio.Lock()

    def _path(self, class_id: str, test_id: str) -> Path:
        for part in (class_id, test_id):
            if not part.replace("-", "").isalnum():
                raise ValueError("not an id")
        return self.root / class_id / "tests" / f"{test_id}.json"

    async def save(self, record: PracticeRecord) -> None:
        async with self._lock:
            path = self._path(record.class_id, record.test_id)
            if not (self.root / record.class_id / "class.json").is_file():
                raise ClassGoneError(record.class_id)  # never recreate a deleted class's folder
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(f".{path.name}.partial")
            partial.write_text(record.model_dump_json(indent=1), encoding="utf-8")
            partial.replace(path)

    async def get(self, test_id: str) -> PracticeRecord | None:
        if not test_id.replace("-", "").isalnum():
            return None
        found = sorted(self.root.glob(f"*/tests/{test_id}.json"))
        return PracticeRecord.model_validate_json(found[0].read_text()) if found else None

    async def of_class(self, class_id: str) -> list[PracticeRecord]:
        folder = self.root / class_id / "tests"
        if not class_id.replace("-", "").isalnum() or not folder.is_dir():
            return []
        return [
            PracticeRecord.model_validate_json(p.read_text()) for p in sorted(folder.glob("*.json"))
        ]


SCHEMA = """
CREATE TABLE IF NOT EXISTS learners.practice_test (
  test_id text PRIMARY KEY,
  class_id text NOT NULL REFERENCES learners.class_group ON DELETE CASCADE,
  record jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
"""


class PostgresPracticeStore:
    """learners.practice_test, one JSON record per test; the class's delete cascades to it."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._ready = False

    async def _conn(self) -> psycopg.AsyncConnection[DictRow]:
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self.url, row_factory=dict_row, autocommit=True
        )
        if not self._ready:
            await conn.execute(SCHEMA)
            self._ready = True
        return conn

    async def save(self, record: PracticeRecord) -> None:
        async with await self._conn() as conn:
            exists = await conn.execute(
                "SELECT 1 FROM learners.class_group WHERE class_id = %s", (record.class_id,)
            )
            if await exists.fetchone() is None:
                raise ClassGoneError(record.class_id)
            await conn.execute(
                "INSERT INTO learners.practice_test (test_id, class_id, record) VALUES (%s, %s, %s)"
                " ON CONFLICT (test_id) DO UPDATE SET record = EXCLUDED.record, updated_at = now()",
                (record.test_id, record.class_id, record.model_dump_json()),
            )

    async def get(self, test_id: str) -> PracticeRecord | None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT record FROM learners.practice_test WHERE test_id = %s", (test_id,)
            )
            row = await cur.fetchone()
        return PracticeRecord.model_validate(row["record"]) if row else None

    async def of_class(self, class_id: str) -> list[PracticeRecord]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT record FROM learners.practice_test WHERE class_id = %s ORDER BY updated_at",
                (class_id,),
            )
            rows = await cur.fetchall()
        return [PracticeRecord.model_validate(r["record"]) for r in rows]


type PracticeStore = FilePracticeStore | PostgresPracticeStore


def practice_store_from_environment(data_dir: Path) -> PracticeStore:
    url = os.environ.get("POSTGRES_URL")
    return PostgresPracticeStore(url) if url else FilePracticeStore(data_dir)


def record_json(record: PracticeRecord) -> dict[str, object]:
    """The teacher's view of a practice test: everything, including the memo (learners never get
    this: their routes serve the questions only)."""
    return json.loads(record.model_dump_json())


# ------------------------------------------------------------------ attempts
# learner-section.md §4.2, §6.3: the same Marker and rules as a class set; the learner sees the
# code-checked mark, the quote that earned it and the memo's worked answer, nothing else.


def practice_result(memo: Memo, marked: MarkedScript, title: str) -> PracticeResult:
    """§6.3 from a checked MarkedScript: per memo question, the marks, the Marker's quotes for
    them and the memo's worked answer. The Marker's rationale is never copied."""
    questions: list[PracticeQuestionResult] = []
    for item in memo.items:
        marks = [m for m in marked.marks if m.question_id == item.question_id]
        awarded = min(item.max_marks, sum(m.awarded for m in marks))
        quotes = tuple(m.evidence_quote for m in marks if m.awarded > 0 and m.evidence_quote)
        questions.append(
            PracticeQuestionResult(
                question_id=item.question_id,
                question=item.question,
                awarded=awarded,
                max_marks=item.max_marks,
                evidence=quotes if awarded else (),
                worked_answer=item.answer,
            )
        )
    return PracticeResult(
        test_title=title,
        total=sum(q.awarded for q in questions),
        max_total=sum(q.max_marks for q in questions),
        questions=tuple(questions),
    )


def test_title(test: PracticeTest) -> str:
    return f"Practice: {test.atp_week.topic}"


def new_attempt_id() -> str:
    """Time first, so a sorted listing is submission order on every store."""
    return f"attempt-{time.time_ns() // 1_000_000:013d}-{secrets.token_hex(3)}"


class MarkingReview(Contract):
    """Teacher-only: what the Marker proposed and which checks failed, for an attempt left
    needs_teacher (the teacher sees every failed attempt). Never on a learner route."""

    attempt_id: str = Field(min_length=1)
    reason: str
    passes: tuple[dict[str, object], ...]


async def mark_attempt(
    test: PracticeTest,
    attempt_id: str,
    code: str,
    answers: Sequence[Answer],
    pack: SubjectPack,
    provider: Provider,
) -> tuple[Attempt, MarkingReview | None]:
    """Mark one attempt; a Marker that cannot produce checked marks leaves it for the teacher,
    with every pass kept for the teacher's review."""
    given = {a.question_id: a for a in answers}
    full = tuple(
        given.get(i.question_id) or Answer(question_id=i.question_id, text="")
        for i in test.memo.items
    )
    script = Script(script_id=code, learner_pseudonym=code, answers=full)
    outcome = await mark_script(test.memo, script, pack, provider, assessment_id=test.test_id)
    if outcome.status == "done" and outcome.marked is not None:
        result = practice_result(test.memo, outcome.marked, test_title(test))
        attempt = Attempt(
            attempt_id=attempt_id,
            test_id=test.test_id,
            code=code,
            answers=full,
            state="marked",
            result=result,
        )
        return attempt, None
    log.info("attempt %s left for the teacher: %s", attempt_id, outcome.reason)
    review = MarkingReview(
        attempt_id=attempt_id,
        reason=outcome.reason,
        passes=tuple(
            {
                "status": str(a.result.status),
                "reason": a.result.reason,
                "proposed": a.result.outputs.get("marked"),
                "checks": [c.model_dump(mode="json") for c in a.checks],
            }
            for a in outcome.attempts
        ),
    )
    attempt = Attempt(
        attempt_id=attempt_id, test_id=test.test_id, code=code, answers=full, state="needs_teacher"
    )
    return attempt, review


class FileAttemptStore:
    """classes/<class_id>/attempts/<attempt_id>.json beside the class (deleted with it)."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "classes"
        self._lock = asyncio.Lock()

    async def save_review(self, class_id: str, review: MarkingReview) -> None:
        folder = self.root / class_id / "reviews"  # beside, not among, the attempts
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{review.attempt_id}.json").write_text(review.model_dump_json(indent=1))

    async def review(self, class_id: str, attempt_id: str) -> MarkingReview | None:
        if not attempt_id.replace("-", "").isalnum():
            return None
        path = self.root / class_id / "reviews" / f"{attempt_id}.json"
        return MarkingReview.model_validate_json(path.read_text()) if path.is_file() else None

    async def save(self, class_id: str, attempt: Attempt) -> None:
        async with self._lock:
            if not (self.root / class_id / "class.json").is_file():
                raise ClassGoneError(class_id)
            folder = self.root / class_id / "attempts"
            folder.mkdir(exist_ok=True)
            path = folder / f"{attempt.attempt_id}.json"
            partial = path.with_name(f".{path.name}.partial")
            partial.write_text(attempt.model_dump_json(indent=1), encoding="utf-8")
            partial.replace(path)

    async def of_test(self, class_id: str, test_id: str) -> list[Attempt]:
        folder = self.root / class_id / "attempts"
        if not class_id.replace("-", "").isalnum() or not folder.is_dir():
            return []
        found = [Attempt.model_validate_json(p.read_text()) for p in sorted(folder.glob("*.json"))]
        return [a for a in found if a.test_id == test_id]

    async def of_learner(self, class_id: str, code: str) -> list[Attempt]:
        folder = self.root / class_id / "attempts"
        if not class_id.replace("-", "").isalnum() or not folder.is_dir():
            return []
        found = [Attempt.model_validate_json(p.read_text()) for p in sorted(folder.glob("*.json"))]
        return [a for a in found if a.code == code]


ATTEMPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS learners.attempt (
  attempt_id text PRIMARY KEY,
  class_id text NOT NULL REFERENCES learners.class_group ON DELETE CASCADE,
  test_id text NOT NULL, code text NOT NULL, record jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE learners.attempt ADD COLUMN IF NOT EXISTS review jsonb;
"""


class PostgresAttemptStore:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ready = False

    async def _conn(self) -> psycopg.AsyncConnection[DictRow]:
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self.url, row_factory=dict_row, autocommit=True
        )
        if not self._ready:
            await conn.execute(ATTEMPT_SCHEMA)
            self._ready = True
        return conn

    async def save(self, class_id: str, attempt: Attempt) -> None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM learners.class_group WHERE class_id = %s", (class_id,)
            )
            if await cur.fetchone() is None:
                raise ClassGoneError(class_id)
            await conn.execute(
                "INSERT INTO learners.attempt (attempt_id, class_id, test_id, code, record)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    attempt.attempt_id,
                    class_id,
                    attempt.test_id,
                    attempt.code,
                    attempt.model_dump_json(),
                ),
            )

    async def save_review(self, class_id: str, review: MarkingReview) -> None:
        async with await self._conn() as conn:
            await conn.execute(
                "UPDATE learners.attempt SET review = %s WHERE attempt_id = %s AND class_id = %s",
                (review.model_dump_json(), review.attempt_id, class_id),
            )

    async def review(self, class_id: str, attempt_id: str) -> MarkingReview | None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT review FROM learners.attempt WHERE attempt_id = %s AND class_id = %s",
                (attempt_id, class_id),
            )
            row = await cur.fetchone()
        return MarkingReview.model_validate(row["review"]) if row and row["review"] else None

    async def of_test(self, class_id: str, test_id: str) -> list[Attempt]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT record FROM learners.attempt WHERE class_id = %s AND test_id = %s"
                " ORDER BY created_at",
                (class_id, test_id),
            )
            rows = await cur.fetchall()
        return [Attempt.model_validate(r["record"]) for r in rows]

    async def of_learner(self, class_id: str, code: str) -> list[Attempt]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT record FROM learners.attempt WHERE class_id = %s AND code = %s"
                " ORDER BY created_at",
                (class_id, code),
            )
            rows = await cur.fetchall()
        return [Attempt.model_validate(r["record"]) for r in rows]


type AttemptStore = FileAttemptStore | PostgresAttemptStore


def attempt_store_from_environment(data_dir: Path) -> AttemptStore:
    url = os.environ.get("POSTGRES_URL")
    return PostgresAttemptStore(url) if url else FileAttemptStore(data_dir)


def progress(
    test: PracticeTest, attempts: Sequence[Attempt], names: Mapping[str, str]
) -> dict[str, object]:
    """The teacher's progress view (§6.2): per-question means over marked attempts, and per
    learner the best and last marks, tries, and attempts waiting for the teacher. Names appear
    here only."""
    marked = [a for a in attempts if a.result is not None]
    questions: list[dict[str, object]] = []
    for item in test.memo.items:
        scores = [
            q.awarded
            for a in marked
            if a.result
            for q in a.result.questions
            if q.question_id == item.question_id
        ]
        mean = (sum(scores) / len(scores)) if scores else None
        questions.append(
            {
                "question_id": item.question_id,
                "max_marks": item.max_marks,
                "mean": None if mean is None else round(mean, 1),
            }
        )
    rows: list[dict[str, object]] = []
    for code in sorted({a.code for a in attempts}):
        mine = [a for a in attempts if a.code == code]
        totals = [a.result.total for a in mine if a.result]
        rows.append(
            {
                "code": code,
                "name": names.get(code, ""),
                "best": max(totals) if totals else None,
                "last": totals[-1] if totals else None,
                "tries": len(mine),
                "waiting": sum(1 for a in mine if a.state == "needs_teacher"),
            }
        )
    return {
        "test_id": test.test_id,
        "learners": len(names),
        "attempted": len(rows),
        "questions": questions,
        "rows": rows,
    }
