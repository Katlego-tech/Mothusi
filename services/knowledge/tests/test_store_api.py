"""The store and the HTTP API against a real Postgres with pgvector (knowledge.md §6.5-6.6).

Set KNOWLEDGE_TEST_DB to a throwaway database URL (its knowledge schema is dropped first)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from mothusi_contracts import PassageKind
from mothusi_knowledge import __main__ as cli
from mothusi_knowledge.api import create_app
from mothusi_knowledge.embed import HashingEmbedder
from mothusi_knowledge.ingest import Source, write_snapshot

URL = os.environ.get("KNOWLEDGE_TEST_DB")
pytestmark = pytest.mark.skipif(not URL, reason="KNOWLEDGE_TEST_DB is not set")

PAGES = {
    "atp.pdf": (
        "Measurement: revise the volume and surface areas of right prisms and cylinders, "
        "and study the effect of multiplying any dimension by a constant factor k on volume.",
        "Number patterns: investigate patterns with a constant difference between consecutive "
        "terms and find the general term, which is linear.",
    ),
    "acc.pdf": (
        "Accounting equation: indicate the effect of various cash transactions on the "
        "accounting equation of a sole trader, using the perpetual inventory system.",
    ),
}
SOURCES = [
    Source(
        path="atp.pdf",
        title="ATP Gr 10 Maths",
        kind=PassageKind.ATP,
        subject="Mathematics",
        grade=10,
    ),
    Source(
        path="acc.pdf",
        title="ATP Gr 10 Accounting",
        kind=PassageKind.ATP,
        subject="Accounting",
        grade=10,
    ),
]


def reset() -> None:
    assert URL
    with psycopg.connect(URL) as conn:
        conn.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    for name in PAGES:
        (tmp_path / name).write_bytes(name.encode())
    out = tmp_path / "passages.jsonl"
    write_snapshot(
        SOURCES,
        tmp_path,
        out,
        HashingEmbedder(),
        read_pdf=lambda p: PAGES[p.name],
        say=lambda _: None,
    )
    return out


@pytest.fixture
def client(snapshot: Path) -> Iterator[TestClient]:
    reset()
    assert URL
    with TestClient(create_app(URL, HashingEmbedder(), str(snapshot))) as c:
        yield c


def test_search_finds_the_measurement_passage_with_its_page(client: TestClient) -> None:
    reply = client.post(
        "/search",
        json={
            "query": "volume and surface area of right prisms and cylinders",
            "subject": "Mathematics",
            "grade": 10,
        },
    )
    assert reply.status_code == 200, reply.text
    first = reply.json()["passages"][0]
    assert first["locator"] == "PDF p 1" and first["source"] == "ATP Gr 10 Maths"
    assert 0 <= first["score"] <= 1 and reply.json()["model"] == "hashing"


def test_filters_keep_other_subjects_and_kinds_out(client: TestClient) -> None:
    maths = client.post("/search", json={"query": "accounting equation", "subject": "Mathematics"})
    assert {p["subject"] for p in maths.json()["passages"]} == {"Mathematics"}
    caps = client.post("/search", json={"query": "accounting equation", "kinds": ["caps"]})
    assert caps.json()["passages"] == []
    assert client.post("/search", json={"query": ""}).status_code == 422


def test_health_reports_the_snapshot_and_its_passages(client: TestClient, snapshot: Path) -> None:
    import hashlib

    health = client.get("/health").json()
    assert health == {
        "status": "ok",
        "passages": 3,
        "snapshot": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "stale": False,
    }


def test_a_second_load_is_already_loaded(
    snapshot: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reset()
    assert URL
    assert cli.main(["load", "--snapshot", str(snapshot), "--database-url", URL]) == 0
    assert capsys.readouterr().out.startswith("loaded ")
    assert cli.main(["load", "--snapshot", str(snapshot), "--database-url", URL]) == 0
    assert capsys.readouterr().out.startswith("already loaded ")


def test_without_a_snapshot_search_and_health_are_503(tmp_path: Path) -> None:
    reset()
    assert URL
    with TestClient(create_app(URL, HashingEmbedder(), None)) as c:
        assert c.get("/health").status_code == 503
        assert c.post("/search", json={"query": "x"}).status_code == 503


def test_an_unreadable_snapshot_keeps_the_loaded_one_and_reports_stale(
    client: TestClient, tmp_path: Path
) -> None:
    del client  # loads the good snapshot first
    assert URL
    with TestClient(create_app(URL, HashingEmbedder(), str(tmp_path / "missing.jsonl"))) as c:
        health = c.get("/health").json()
        assert health["stale"] is True and health["passages"] == 3


def test_load_is_one_transaction(snapshot: Path, tmp_path: Path) -> None:
    from mothusi_knowledge import store

    reset()
    assert URL
    bad = tmp_path / "bad.jsonl"
    lines = snapshot.read_text().splitlines()
    bad.write_text("\n".join([*lines, lines[1]]) + "\n")  # a duplicate passage_id fails mid-insert

    async def go() -> int:
        conn = await store.connect(URL)  # type: ignore[arg-type]
        try:
            await store.ensure_schema(conn)
            await store.load(conn, snapshot)
            with pytest.raises(psycopg.errors.UniqueViolation):
                await store.load(conn, bad)
            return await store.passage_count(conn)
        finally:
            await conn.close()

    assert asyncio.run(go()) == 3  # the good snapshot is still there, whole


def test_a_selective_filter_still_finds_its_passage_among_near_duplicates(tmp_path: Path) -> None:
    """400 near-identical passages of another subject must not crowd out the one that matches the
    filter. Under an HNSW index both failed: filters applied after its candidates were chosen, and
    pruning left the odd node unreachable; the search is now exact."""
    assert URL
    reset()
    words = ["debtors", "creditors", "stock", "drawings", "capital", "bank", "rent", "wages"]
    crowd = tuple(  # distinct words per page: digits alone would be stripped as a running line
        f"Accounting equation and cash transactions: {words[n % 8]} {words[n // 8 % 8]} "
        f"{words[n // 64 % 8]} affect assets and owner's equity."
        for n in range(400)
    )
    # One word in common with the query: hashed test vectors that share nothing are orthogonal
    # and can sit unreachable in the HNSW graph, which real bge-m3 vectors do not.
    maths = "Measurement: solve the equation for the volume and surface area of right prisms."
    pages = {"acc.pdf": crowd, "atp.pdf": (maths,)}
    for name in pages:
        (tmp_path / name).write_bytes(name.encode())
    out = tmp_path / "crowd.jsonl"
    write_snapshot(
        SOURCES, tmp_path, out, HashingEmbedder(), read_pdf=lambda p: pages[p.name], say=print
    )
    forced = f"{URL}{'&' if '?' in URL else '?'}options=-c%20enable_seqscan%3Doff"
    with TestClient(create_app(forced, HashingEmbedder(), str(out))) as c:
        found = c.post(
            "/search",
            json={
                "query": "accounting equation cash transactions",
                "subject": "Mathematics",
                "k": 3,
            },
        ).json()["passages"]
    assert [p["subject"] for p in found] == ["Mathematics"]
