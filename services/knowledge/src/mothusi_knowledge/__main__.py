"""python -m mothusi_knowledge ingest | load (knowledge.md §6.4)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from mothusi_knowledge import store
from mothusi_knowledge.embed import BgeM3Embedder
from mothusi_knowledge.extract import ExtractError
from mothusi_knowledge.ingest import load_sources, write_snapshot


async def _load(location: str, url: str) -> str:
    conn = await store.connect(url)
    try:
        await store.ensure_schema(conn)
        with store.local_snapshot(location) as path:
            sha, n, fresh = await store.load(conn, path)
    finally:
        await conn.close()
    return f"loaded {sha} ({n} passages)" if fresh else f"already loaded {sha}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mothusi_knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="the sources to a snapshot file")
    ingest.add_argument("--sources", type=Path, required=True)
    ingest.add_argument("--out", type=Path, required=True)
    ingest.add_argument("--max-chars", type=int, default=1200)
    ingest.add_argument(
        "--root", type=Path, default=Path("."), help="paths in --sources are relative to this"
    )
    load = sub.add_parser(
        "load", help="a snapshot into Postgres (what the service does at startup)"
    )
    load.add_argument("--snapshot", required=True, help="a path or s3://bucket/key")
    load.add_argument("--database-url", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            write_snapshot(
                load_sources(args.sources),
                args.root,
                args.out,
                BgeM3Embedder(),
                max_chars=args.max_chars,
            )
        else:
            print(asyncio.run(_load(args.snapshot, args.database_url)))
    except (ExtractError, store.SnapshotError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
