"""The passage store in Postgres with pgvector (knowledge.md §4.1, §6.5).

The service owns the `knowledge` schema and creates it idempotently. A snapshot loads in one
transaction and only when its sha256 differs from the one loaded, so a restart is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.rows import DictRow, dict_row

from mothusi_contracts import Passage, PassageKind
from mothusi_knowledge.embed import DIM

# vector(1024) is DIM; spelled out so the statement is a literal (psycopg's LiteralString rule).
assert DIM == 1024
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE TABLE IF NOT EXISTS knowledge.snapshot (
  sha256 text PRIMARY KEY, loaded_at timestamptz NOT NULL DEFAULT now(), passages int NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge.passage (
  passage_id text PRIMARY KEY, source_key text NOT NULL, source text NOT NULL, kind text NOT NULL,
  subject text, grade int, page int CHECK (page >= 1), chunk int NOT NULL, locator text NOT NULL,
  text text NOT NULL, embedding vector(1024) NOT NULL);
DROP INDEX IF EXISTS knowledge.passage_embedding_hnsw;
"""
# No vector index (knowledge.md §6.5): the corpus is a few thousand passages, so an
# exact scan costs milliseconds and is always right. HNSW applied the subject/grade/kind filters
# after choosing its candidates, and pruning left a node unreachable among near-duplicate pages;
# both made a matching passage vanish from a filtered search.

type Conn = psycopg.AsyncConnection[DictRow]

COLUMNS = (
    "passage_id",
    "source_key",
    "source",
    "kind",
    "subject",
    "grade",
    "page",
    "chunk",
    "locator",
    "text",
    "embedding",
)
INSERT = (
    "INSERT INTO knowledge.passage (passage_id, source_key, source, kind, subject, grade, page,"
    " chunk, locator, text, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


class SnapshotError(Exception):
    """The snapshot cannot be read or does not fit the schema; nothing was changed."""


async def connect(url: str) -> Conn:
    """Autocommit: a read never leaves an idle transaction holding locks between requests (which
    would block a schema change or a reload); the load opens its own transaction."""
    return await psycopg.AsyncConnection[DictRow].connect(
        url, row_factory=dict_row, autocommit=True
    )


async def ensure_schema(conn: Conn) -> None:
    async with conn.transaction():
        await conn.execute(SCHEMA)
    await register_vector_async(conn)  # pyright: ignore[reportUnknownVariableType]


@contextmanager
def local_snapshot(location: str) -> Generator[Path]:
    """A local path as is; an s3://bucket/key downloaded to a temporary file first."""
    if not location.startswith("s3://"):
        yield Path(location)
        return
    import boto3  # pyright: ignore[reportMissingTypeStubs]

    bucket, _, key = location.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise SnapshotError(f"{location}: give s3://bucket/key")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snapshot.jsonl"
        make_client = cast(Callable[[str], Any], boto3.client)  # pyright: ignore[reportUnknownMemberType]
        make_client("s3").download_file(bucket, key, str(path))
        yield path


def read_snapshot(path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """(sha256, header, rows) of a snapshot file, checked against the schema."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SnapshotError(f"{path}: cannot read the snapshot ({error.strerror})") from error
    lines = data.decode("utf-8").splitlines()
    try:
        header = cast(dict[str, Any], json.loads(lines[0]))
        rows = [cast(dict[str, Any], json.loads(line)) for line in lines[1:] if line.strip()]
    except (IndexError, ValueError) as error:
        raise SnapshotError(f"{path}: not a snapshot (JSON Lines with a header)") from error
    if header.get("snapshot") != 1 or header.get("dim") != DIM:
        raise SnapshotError(f"{path}: snapshot version or dimension is not 1 / {DIM}")
    for row in rows:
        if len(row.get("embedding", ())) != DIM or not row.get("passage_id"):
            raise SnapshotError(f"{path}: a row without a {DIM}-value embedding or an id")
    return hashlib.sha256(data).hexdigest(), header, rows


async def loaded(conn: Conn) -> tuple[str, int] | None:
    cur = await conn.execute(
        "SELECT sha256, passages FROM knowledge.snapshot ORDER BY loaded_at DESC LIMIT 1"
    )
    row = await cur.fetchone()
    return (str(row["sha256"]), int(row["passages"])) if row else None


async def load(conn: Conn, path: Path) -> tuple[str, int, bool]:
    """Load a snapshot unless it is the one already loaded: (sha256, passages, newly_loaded)."""
    sha, _header, rows = read_snapshot(path)
    current = await loaded(conn)
    if current and current[0] == sha:
        return sha, current[1], False
    async with conn.transaction():
        await conn.execute("TRUNCATE knowledge.passage")
        await conn.execute("DELETE FROM knowledge.snapshot")
        async with conn.cursor() as cur:
            await cur.executemany(
                INSERT,
                [
                    tuple(Vector(r["embedding"]) if c == "embedding" else r.get(c) for c in COLUMNS)
                    for r in rows
                ],
            )
        await conn.execute(
            "INSERT INTO knowledge.snapshot (sha256, passages) VALUES (%s, %s)", (sha, len(rows))
        )
    return sha, len(rows), True


async def search(
    conn: Conn,
    vector: Sequence[float],
    *,
    k: int,
    subject: str | None,
    grade: int | None,
    kinds: Sequence[PassageKind],
    min_score: float,
) -> tuple[Passage, ...]:
    """Nearest passages by cosine distance; score = 1 - distance, clamped to [0, 1]."""
    q = Vector(list(vector))
    # An exact scan (no vector index): see SCHEMA.
    cur = await conn.execute(
        """
    SELECT passage_id, source, source_key, kind, subject, grade, page, locator, text,
           1 - (embedding <=> %(q)s) AS score
    FROM knowledge.passage
    WHERE (%(subject)s::text IS NULL OR subject = %(subject)s OR subject IS NULL)
      AND (%(grade)s::int IS NULL OR grade = %(grade)s OR grade IS NULL)
      AND (cardinality(%(kinds)s::text[]) = 0 OR kind = ANY(%(kinds)s::text[]))
    ORDER BY embedding <=> %(q)s
    LIMIT %(k)s
    """,
        {
            "q": q,
            "subject": subject,
            "grade": grade,
            "kinds": [kind.value for kind in kinds],
            "k": k,
        },
    )
    rows = await cur.fetchall()
    out: list[Passage] = []
    for row in rows:
        score = min(1.0, max(0.0, float(row["score"])))
        if score >= min_score:
            out.append(Passage.model_validate({**row, "score": score}))
    return tuple(out)


async def passage_count(conn: Conn) -> int:
    cur = await conn.execute("SELECT count(*) AS n FROM knowledge.passage")
    row = await cur.fetchone()
    return int(row["n"]) if row else 0
