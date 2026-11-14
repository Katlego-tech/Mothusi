"""Read-only conversation beside the marking graph; code composes every grounded reply."""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from mothusi_contracts import Contract, EventKind
from mothusi_core.graph.state import RunState
from mothusi_llm import Provider

if TYPE_CHECKING:
    from mothusi_core.graph.planner import Planner

Skill = Literal["run_status", "explain_mark", "send_feedback", "fallback"]


class Intent(Contract):
    skill: Skill
    script_id: str | None = None
    question_id: str | None = None


class ReplyBody(TypedDict):
    message_id: str | None
    skill: Skill
    say: str
    card: dict[str, Any] | None
    promise: str | None


def match_rules(text: str) -> Intent | None:
    text = text.lower()
    if re.search(r"\bstatus\b|\bhow far are you\b", text):
        return Intent(skill="run_status")
    sid = re.search(r"(?<![\w-])l-\d{3}(?![\w-])", text)
    if not sid:
        return None
    script_id = sid[0].upper()
    if re.search(r"\bsend\b", text) and re.search(r"\bfeedback\b|\bnote\b", text):
        return Intent(skill="send_feedback", script_id=script_id)
    if re.search(r"\bwhy\b|\bexplain\b", text):
        without_sid = text[: sid.start()] + " " + text[sid.end() :]
        question = re.search(r"\bquestion\s+(\d+(?:\.\d+)*)\b", without_sid)
        dotted = re.search(r"(?<![\w.])\d+(?:\.\d+)+(?![\w.]\d)", without_sid)
        qid = question[1] if question else dotted[0] if dotted else None
        if qid:
            return Intent(skill="explain_mark", script_id=script_id, question_id=qid)
    return None


async def understand(
    text: str,
    provider: Provider | None,
    *,
    script_ids: set[str] | None = None,
    question_ids: set[str] | None = None,
) -> Intent:
    """Rules first, otherwise validated model JSON; every failure is an honest fallback."""
    try:
        intent = match_rules(text)
        if intent is None:
            if provider is None:
                return Intent(skill="fallback")
            intent, _ = await provider.complete(
                "Choose only run_status, explain_mark, send_feedback or fallback. "
                "Return Intent JSON only. Chat never approves sending. "
                f"Valid script ids: {sorted(script_ids or [])}. "
                f"Valid question ids: {sorted(question_ids or [])}.",
                text,
                Intent,
            )
            intent = Intent.model_validate(intent)
        if intent.script_id is not None:
            sid = intent.script_id.upper()
            if not re.fullmatch(r"L-\d{3}", sid) or (
                script_ids is not None and sid not in script_ids
            ):
                return Intent(skill="fallback")
            intent = intent.model_copy(update={"script_id": sid})
        if intent.question_id is not None and (
            not re.fullmatch(r"\d+(?:\.\d+)*", intent.question_id)
            or (question_ids is not None and intent.question_id not in question_ids)
        ):
            return Intent(skill="fallback")
        if intent.skill in {"explain_mark", "send_feedback"} and not intent.script_id:
            return Intent(skill="fallback")
        if intent.skill == "explain_mark" and not intent.question_id:
            return Intent(skill="fallback")
        return intent
    except Exception:
        return Intent(skill="fallback")


def compose(intent: Intent, state: RunState, promises: set[str], early_sent: set[str]) -> ReplyBody:
    """Pure: no model, writes or delivery. All marks and wording come from this snapshot."""
    reply: ReplyBody = {
        "message_id": None,
        "skill": intent.skill,
        "say": "",
        "card": None,
        "promise": None,
    }
    sid = intent.script_id
    stage = state["state"]
    marked = state.get("marked", {})
    if intent.skill == "run_status":
        now = datetime.fromisoformat(state.get("observed_at", state["started_at"]))
        minutes = max(
            0, int((now - datetime.fromisoformat(state["started_at"])).total_seconds() // 60)
        )
        waiting = len(state.get("pending", {}))
        reply["say"] = (
            f"{stage.replace('_', ' ').capitalize()}: {len(marked)} of {len(state['scripts'])} "
            f"scripts marked, {waiting or 'none'} waiting for you. "
            f"R{Decimal(state.get('spent_zar', '0')):.2f} of R{Decimal(state['budget_zar']):.2f} "
            f"spent, {minutes} {'minute' if minutes == 1 else 'minutes'} in."
        )
    elif intent.skill == "fallback":
        reply["say"] = (
            "I can't do that while the run is working. I can tell you how far I am, explain a "
            'mark (try "why did L-014 get its mark on 2.1?"), or bring you one learner\'s note '
            'to approve (try "send L-001 their feedback"). A note is released only from its card.'
        )
    elif sid is None or sid not in {s["script_id"] for s in state["scripts"]}:
        reply["say"] = f"There's no learner {sid} in this class."
    elif intent.skill == "explain_mark":
        mark = next(
            (
                m
                for m in marked.get(sid, {}).get("marks", [])
                if m["question_id"] == intent.question_id
            ),
            None,
        )
        if mark is None:
            reply["say"] = f"{sid} isn't marked on {intent.question_id} yet. Ask again soon."
        else:
            reply["say"] = (
                f"{sid} got {mark['awarded']}/{mark['max_marks']} on {intent.question_id}. "
                f'The learner wrote "{mark["evidence_quote"]}", and the memo line applied was '
                f'"{mark["memo_line"]}".'
            )
            if sid not in state.get("moderated", {}) and not state.get("moderation_closed"):
                reply["say"] += " This is a first mark: provisional, not yet moderated."
    elif sid in early_sent:
        sent_at = state.get("early_sent_at", {}).get(sid)
        reply["say"] = (
            f"{sid}'s note was released at {datetime.fromisoformat(sent_at).strftime('%H:%M')}."
            if sent_at
            else f"{sid}'s note was already released."
        )
    elif stage == "delivered":
        reply["say"] = f"{sid}'s note went out with the set."
    elif stage != "awaiting_approval":
        if sid in marked:
            reply["say"] = (
                f"{sid} has a provisional {marked[sid]['total']}/{state['memo']['total_marks']}. "
                "Marks are final only after moderation, because the blind sample is drawn once "
                "every script is marked. I'll bring you the note to approve then."
            )
        else:
            reply["say"] = (
                f"{sid} isn't marked yet. "
                "I'll bring you the note to approve once the marks are final."
            )
        reply["promise"] = sid
    else:
        verdict = next((v for v in state.get("verdicts", []) if v["pseudonym"] == sid), {})
        note = next(
            (n for n in state.get("drafts", {}).get("learner_notes", []) if n["pseudonym"] == sid),
            None,
        )
        if not verdict.get("passed") or note is None:
            reason = (
                verdict.get("reason")
                or state.get("review_incomplete")
                or "not reviewed: no verdict was returned"
            )
            reply["say"] = (
                f"{sid}'s note didn't pass review: {reason}. "
                "It stays with the set for your approval."
            )
        else:
            reply["say"] = (
                f"{sid}'s note is ready. It's released only if you approve it on the card."
            )
            reply["card"] = {
                "script_id": sid,
                "learner": sid,
                **{
                    k: note[k]
                    for k in ("outcome", "mark_with_quote", "next_task", "self_assessment_prompt")
                },
            }
    return reply


async def entries(planner: Planner, namespace: str, aid: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    while page := await planner.store.asearch((namespace, aid), limit=100, offset=len(found)):
        found.update({item.key: item.value for item in page})
    return found


async def _reply(
    planner: Planner, aid: str, message_id: str, intent: Intent, state: RunState
) -> None:
    promises = await entries(planner, "promises", aid)
    sent = await entries(planner, "early_sent", aid)
    state = {
        **state,
        "observed_at": planner.clock().isoformat(),
        "early_sent_at": {sid: item["sent_at"] for sid, item in sent.items()},
    }
    body = compose(intent, state, set(promises), set(sent))
    body["message_id"] = message_id
    if body["promise"]:
        await planner.store.aput(("promises", aid), body["promise"], {"message_id": message_id})
    planner.sink(aid).emit(EventKind.REPLY, body["say"][:80], body)
    # Close the race where review finished while understanding this message.
    if body["promise"]:
        latest = await planner.snapshot(aid)
        if latest.get("state") == "awaiting_approval":
            await keep_promises(planner, aid, latest)


async def respond(planner: Planner, assessment_id: str, message_id: str, text: str) -> None:
    from mothusi_core.graph.planner import pseudonymise_text

    state = await planner.snapshot(assessment_id)
    text = pseudonymise_text(text, state["names"])
    provider = (
        planner.provider(assessment_id) if os.environ.get("MOTHUSI_PROVIDER") == "ollama" else None
    )
    intent = await understand(
        text,
        provider,
        script_ids={s["script_id"] for s in state["scripts"]},
        question_ids={q["question_id"] for q in state["memo"]["items"]},
    )
    await _reply(planner, assessment_id, message_id, intent, await planner.snapshot(assessment_id))


async def keep_promises(planner: Planner, assessment_id: str, state: RunState) -> None:
    promises = await entries(planner, "promises", assessment_id)
    sent = await entries(planner, "early_sent", assessment_id)
    state = {
        **state,
        "observed_at": planner.clock().isoformat(),
        "early_sent_at": {sid: item["sent_at"] for sid, item in sent.items()},
    }
    for sid, promise in promises.items():
        await planner.store.adelete(("promises", assessment_id), sid)
        sink = planner.sink(assessment_id)
        # A concurrent keeper may have read the same entry before deletion. The log is the
        # source of truth: check and emit without an intervening await.
        if any(
            e.kind is EventKind.REPLY
            and e.payload.get("message_id") == promise["message_id"]
            and e.payload.get("promise") is None
            for e in sink.read()
        ):
            continue
        body = compose(Intent(skill="send_feedback", script_id=sid), state, set(), set(sent))
        body["message_id"] = promise["message_id"]
        sink.emit(EventKind.REPLY, body["say"][:80], body)
