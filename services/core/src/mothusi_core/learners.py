"""Classes, learner codes and consent (learner-section.md §3, §6.2, §7).

A learner is a random code; the name lives only in the class's roster, which only the class's own
teacher reads. Two stores share one protocol: Postgres when POSTGRES_URL is set (AWS: RDS under the
mothusi-data key), JSON files under the data directory otherwise (the laptop, tests).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import DictRow, dict_row

from mothusi_contracts import ClassGroup, ConsentRecord, LearnerAccount, new_learner_code

BASIS = "signed parental consent form held by the school"


class ClassStore(Protocol):
    async def add_class(
        self, group: ClassGroup, accounts: Sequence[LearnerAccount], names: Mapping[str, str]
    ) -> None: ...
    async def get_class(self, class_id: str) -> ClassGroup | None: ...
    async def classes_of(self, teacher_sub: str) -> list[ClassGroup]: ...
    async def accounts(self, class_id: str) -> list[LearnerAccount]: ...
    async def names(self, class_id: str) -> dict[str, str]: ...
    async def save_accounts(self, accounts: Sequence[LearnerAccount]) -> None: ...
    async def delete_class(self, class_id: str) -> None: ...
    async def account(self, code: str) -> LearnerAccount | None: ...


def may_replace(current: str, new: str) -> bool:
    """Whether a save may move an account from `current` to `new`. Nothing goes back to
    "invited" (consent once recorded stays recorded), and a "consented" write never demotes an
    active or disabled account: a stale snapshot can never undo a later change."""
    if new == "invited":
        return current == "invited"
    if new == "consented":
        return current in ("invited", "consented")
    return True


def make_accounts(
    class_id: str,
    names: Sequence[str],
    taken: set[str] | None = None,
    rng: Callable[[int], bytes] = secrets.token_bytes,
) -> tuple[list[LearnerAccount], dict[str, str]]:
    """One invited account per name, each with a fresh code (never one already `taken`)."""
    used = set(taken or ())
    accounts: list[LearnerAccount] = []
    roster: dict[str, str] = {}
    for name in names:
        code = new_learner_code(rng)
        while code in used:
            code = new_learner_code(rng)
        used.add(code)
        accounts.append(LearnerAccount(code=code, class_id=class_id, state="invited"))
        roster[code] = name
    return accounts, roster


def record_consent(
    accounts: Sequence[LearnerAccount], codes: Sequence[str], teacher_sub: str, now: datetime
) -> list[LearnerAccount]:
    """Invited accounts named in `codes` become consented; the others are unchanged. A code that
    is not in the class is refused, naming it, and nothing is recorded."""
    by_code = {a.code: a for a in accounts}
    unknown = [c for c in codes if c not in by_code]
    if unknown:
        raise ValueError(f"not in this class: {', '.join(unknown)}")
    wanted = set(codes)
    out: list[LearnerAccount] = []
    for account in accounts:
        if account.code in wanted and account.state == "invited":
            consent = ConsentRecord(recorded_by=teacher_sub, recorded_at=now, basis=BASIS)
            account = account.model_copy(update={"state": "consented", "consent": consent})
        out.append(account)
    return out


class FileClassStore:
    """data_dir/classes/<class_id>/{class,accounts,roster}.json, each replaced atomically."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "classes"
        self._lock = asyncio.Lock()

    def _dir(self, class_id: str) -> Path:
        if not class_id.replace("-", "").isalnum():
            raise ValueError("not a class id")
        return self.root / class_id

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        partial = path.with_name(f".{path.name}.partial")
        partial.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(path)

    async def add_class(
        self, group: ClassGroup, accounts: Sequence[LearnerAccount], names: Mapping[str, str]
    ) -> None:
        async with self._lock:
            folder = self._dir(group.class_id)
            folder.mkdir(parents=True, exist_ok=False)
            self._write(folder / "class.json", group.model_dump(mode="json"))
            self._write(folder / "accounts.json", [a.model_dump(mode="json") for a in accounts])
            self._write(folder / "roster.json", dict(names))

    async def get_class(self, class_id: str) -> ClassGroup | None:
        try:
            path = self._dir(class_id) / "class.json"
        except ValueError:
            return None
        return ClassGroup.model_validate_json(path.read_text()) if path.is_file() else None

    async def classes_of(self, teacher_sub: str) -> list[ClassGroup]:
        if not self.root.is_dir():
            return []
        groups = [
            ClassGroup.model_validate_json(p.read_text())
            for p in sorted(self.root.glob("*/class.json"))
        ]
        return [g for g in groups if g.teacher_sub == teacher_sub]

    async def accounts(self, class_id: str) -> list[LearnerAccount]:
        path = self._dir(class_id) / "accounts.json"
        if not path.is_file():
            return []
        return [LearnerAccount.model_validate(a) for a in json.loads(path.read_text())]

    async def names(self, class_id: str) -> dict[str, str]:
        path = self._dir(class_id) / "roster.json"
        return dict(json.loads(path.read_text())) if path.is_file() else {}

    async def save_accounts(self, accounts: Sequence[LearnerAccount]) -> None:
        async with self._lock:
            for class_id in {a.class_id for a in accounts}:
                path = self._dir(class_id) / "accounts.json"
                current = (
                    {a["code"]: a for a in json.loads(path.read_text())} if path.is_file() else {}
                )
                for account in accounts:
                    held = current.get(account.code)
                    if account.class_id == class_id and (
                        held is None or may_replace(str(held["state"]), account.state)
                    ):
                        current[account.code] = account.model_dump(mode="json")
                self._write(path, list(current.values()))

    async def account(self, code: str) -> LearnerAccount | None:
        if not self.root.is_dir():
            return None
        for path in sorted(self.root.glob("*/accounts.json")):
            for row in json.loads(path.read_text()):
                if row.get("code") == code:
                    return LearnerAccount.model_validate(row)
        return None

    async def delete_class(self, class_id: str) -> None:
        """The class, its codes, consent records and roster names, gone (§7)."""
        async with self._lock:
            folder = self._dir(class_id)
            if folder.is_dir():
                shutil.rmtree(folder)


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS learners;
CREATE TABLE IF NOT EXISTS learners.class_group (
  class_id text PRIMARY KEY, teacher_sub text NOT NULL, pack_id text NOT NULL,
  label text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS learners.account (
  code text PRIMARY KEY, class_id text NOT NULL REFERENCES learners.class_group ON DELETE CASCADE,
  state text NOT NULL, consent jsonb);
CREATE TABLE IF NOT EXISTS learners.roster (
  code text PRIMARY KEY REFERENCES learners.account ON DELETE CASCADE, name text NOT NULL);
"""

type Conn = psycopg.AsyncConnection[DictRow]


class PostgresClassStore:
    """The learners schema in the core's database (RDS on AWS). Autocommit connections, one per
    call: classes are read a few times per lesson, and no read holds a transaction open."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._ready = False

    async def _conn(self) -> Conn:
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self.url, row_factory=dict_row, autocommit=True
        )
        if not self._ready:
            await conn.execute(SCHEMA)
            self._ready = True
        return conn

    async def add_class(
        self, group: ClassGroup, accounts: Sequence[LearnerAccount], names: Mapping[str, str]
    ) -> None:
        async with await self._conn() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO learners.class_group (class_id, teacher_sub, pack_id, label)"
                " VALUES (%s, %s, %s, %s)",
                (group.class_id, group.teacher_sub, group.pack_id, group.label),
            )
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO learners.account (code, class_id, state, consent)"
                    " VALUES (%s, %s, %s, %s)",
                    [(a.code, a.class_id, a.state, _consent(a)) for a in accounts],
                )
                await cur.executemany(
                    "INSERT INTO learners.roster (code, name) VALUES (%s, %s)",
                    list(names.items()),
                )

    async def get_class(self, class_id: str) -> ClassGroup | None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT class_id, teacher_sub, pack_id, label FROM learners.class_group"
                " WHERE class_id = %s",
                (class_id,),
            )
            row = await cur.fetchone()
        return ClassGroup.model_validate(row) if row else None

    async def classes_of(self, teacher_sub: str) -> list[ClassGroup]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT class_id, teacher_sub, pack_id, label FROM learners.class_group"
                " WHERE teacher_sub = %s ORDER BY created_at",
                (teacher_sub,),
            )
            rows = await cur.fetchall()
        return [ClassGroup.model_validate(r) for r in rows]

    async def accounts(self, class_id: str) -> list[LearnerAccount]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT code, class_id, state, consent FROM learners.account"
                " WHERE class_id = %s ORDER BY code",
                (class_id,),
            )
            rows = await cur.fetchall()
        return [LearnerAccount.model_validate(r) for r in rows]

    async def names(self, class_id: str) -> dict[str, str]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT r.code, r.name FROM learners.roster r JOIN learners.account a"
                " USING (code) WHERE a.class_id = %s",
                (class_id,),
            )
            rows = await cur.fetchall()
        return {str(r["code"]): str(r["name"]) for r in rows}

    async def account(self, code: str) -> LearnerAccount | None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "SELECT code, class_id, state, consent FROM learners.account WHERE code = %s",
                (code,),
            )
            row = await cur.fetchone()
        return LearnerAccount.model_validate(row) if row else None

    async def delete_class(self, class_id: str) -> None:
        """The class row; its accounts and roster names go with it (ON DELETE CASCADE)."""
        async with await self._conn() as conn:
            await conn.execute("DELETE FROM learners.class_group WHERE class_id = %s", (class_id,))

    async def save_accounts(self, accounts: Sequence[LearnerAccount]) -> None:
        async with await self._conn() as conn, conn.transaction(), conn.cursor() as cur:
            # may_replace, in SQL: a stale snapshot never moves an account backwards.
            await cur.executemany(
                "UPDATE learners.account SET state = %(new)s, consent = %(consent)s"
                " WHERE code = %(code)s"
                " AND NOT (%(new)s = 'invited' AND state <> 'invited')"
                " AND NOT (%(new)s = 'consented' AND state NOT IN ('invited', 'consented'))",
                [{"new": a.state, "consent": _consent(a), "code": a.code} for a in accounts],
            )


def _consent(account: LearnerAccount) -> str | None:
    return account.consent.model_dump_json() if account.consent else None


def class_store_from_environment(data_dir: Path) -> ClassStore:
    url = os.environ.get("POSTGRES_URL")
    return PostgresClassStore(url) if url else FileClassStore(data_dir)
