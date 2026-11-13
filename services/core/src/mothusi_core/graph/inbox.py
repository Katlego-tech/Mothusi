"""Teacher answers waiting beside the graph's checkpoints."""

from datetime import UTC, datetime

from langgraph.store.base import BaseStore

from mothusi_core.graph.state import Json


async def put(store: BaseStore, assessment_id: str, script_id: str, answer: Json) -> None:
    await store.aput(
        ("inbox", assessment_id),
        script_id,
        {"answer": answer, "received_at": datetime.now(UTC).isoformat()},
    )


async def exists(store: BaseStore, assessment_id: str, script_id: str) -> bool:
    return await store.aget(("inbox", assessment_id), script_id) is not None


async def take_all(store: BaseStore, assessment_id: str) -> dict[str, Json]:
    """Read all waiting answers; the caller deletes each entry only after applying it."""
    answers: dict[str, Json] = {}
    offset = 0
    while entries := await store.asearch(("inbox", assessment_id), limit=100, offset=offset):
        answers.update({entry.key: entry.value["answer"] for entry in entries})
        offset += len(entries)
    return answers
