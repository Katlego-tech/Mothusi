"""Read-only conversation beside the marking graph; code composes every grounded reply."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict, cast, get_args

from pydantic import Field

from mothusi_contracts import Contract, Event, EventKind
from mothusi_core.graph.state import RunState
from mothusi_core.knowledge import Knowledge, NoKnowledge, knowledge_from_environment
from mothusi_llm import Provider

if TYPE_CHECKING:
    from mothusi_core.graph.planner import Planner
    from mothusi_core.main import Runs

Skill = Literal[
    "open",
    "run_status",
    "explain_mark",
    "send_feedback",
    "fallback",
    "deferred",
    "run_report",
    "run_walls",
    "memo_item",
    "poa",
    "atp",
    "curriculum",
    "cannot",
    "lesson_plan",
]
PACKS = Path(__file__).resolve().parents[4] / "packs"


class Lesson(Contract):
    subject: str | None = None
    weeks: list[Annotated[int, Field(ge=1, le=43)]] | None = None
    topic: str | None = None
    lessons: int | None = Field(default=None, ge=1, le=100)
    minutes: int | None = Field(default=None, ge=1, le=1440)
    notes: str | None = None


class Intent(Contract):
    skill: Skill
    script_id: str | None = None
    question_id: str | None = None
    view: str | None = None
    subject: str | None = None
    term: int | None = Field(default=None, ge=1, le=4)
    weeks: list[Annotated[int, Field(ge=1, le=43)]] | None = None
    topic: str | None = None
    refusal: Literal["sources", "contact", "export", "marks"] | None = None


OpenTarget = Literal["script", "note", "stats", "approval", "desk", "new"]


class UiAction(TypedDict):
    open: OpenTarget
    script_id: str | None


class ReplyBody(TypedDict):
    message_id: str | None
    skill: Skill
    say: str
    card: dict[str, Any] | None
    promise: str | None
    action: UiAction | None


def match_rules(text: str) -> Intent | None:
    text = text.lower()
    if re.search(r"\bgrade\s+(?!10\b)\d+|\b(geography|physics|biology|history|english)\b", text):
        return Intent(skill="cannot", refusal="sources")
    if re.search(r"\b(email|contact|message|call)\b.*\b(parent|learner|student)s?\b", text):
        return Intent(skill="cannot", refusal="contact")
    if re.search(r"report cards?|sa-sams", text):
        return Intent(skill="cannot", refusal="export")
    if re.search(r"\b(change|alter|override)\b.*\bmarks?\b|\bapprove\b.*\b(set|test|run)\b", text):
        return Intent(skill="cannot", refusal="marks")
    if re.search(r"lesson\s+plan", text):
        return Intent(skill="lesson_plan")
    if re.search(r"stuck|went wrong|hit a wall|failed checks|re-brief|budget spent", text):
        return Intent(skill="run_walls")
    sid_match = re.search(r"(?<![\w-])l-\d{3}(?![\w-])", text)
    if re.search(r"\b(?:start|mark)\s+(?:a\s+)?(?:new\s+)?set\b", text):
        return Intent(skill="open", view="new")
    if re.search(r"\b(?:open|show|take me to|go to|go home)\b", text):
        targets: tuple[tuple[OpenTarget, str], ...] = (
            ("new", r"\bnew set\b"),
            ("desk", r"\b(?:desk|home)\b"),
            ("approval", r"\bapproval\b"),
            ("note", r"\b(?:feedback|note)\b"),
            ("script", r"\b(?:script|sheet|paper)\b"),
            ("stats", r"\b(?:stats|statistics|item analysis|results)\b"),
        )
        for target, pattern in targets:
            if re.search(pattern, text):
                return Intent(
                    skill="open",
                    view=target,
                    script_id=sid_match[0].upper() if sid_match else None,
                )
    if re.search(
        r"stats|statistics|average|highest|lowest|spread|struggle|weakest|catch.up"
        r"|towards the year",
        text,
    ):
        view = (
            "catch_up"
            if re.search(r"catch.up", text)
            else "items"
            if re.search(r"struggle|weakest", text)
            else "year"
            if "towards the year" in text
            else None
        )
        return Intent(
            skill="run_report", view=view, script_id=sid_match[0].upper() if sid_match else None
        )
    if re.search(r"\bpoa\b|how much.*count|scripts.*moderat|moderation rule|weighting", text):
        term = re.search(r"term\s+([1-4])\b", text)
        return Intent(skill="poa", term=int(term[1]) if term else None)
    if re.search(r"\bcaps\b|curriculum", text):
        return Intent(skill="curriculum", topic=text)
    if re.search(r"\batp\b|\bteach\b|\btaught\b|\bweek\s+\d+", text):
        slots = fill_lesson(text, Lesson())
        topic_match = re.search(r"when is (.+?) taught", text)
        return Intent(
            skill="atp", weeks=slots.weeks, topic=topic_match[1] if topic_match else slots.topic
        )
    if re.search(r"\bmemo\b|how many marks", text):
        question = re.search(r"(?:question\s+|for\s+)(\d+(?:\.\d+)*)", text)
        view = (
            "check"
            if re.search(r"wrong|check|setter|flag", text)
            else "total"
            if "how many marks" in text
            else None
        )
        return Intent(skill="memo_item", view=view, question_id=question[1] if question else None)
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


def route_rules(text: str, lesson: Lesson | None = None) -> Intent | None:
    intent = match_rules(text)
    if (
        lesson is not None
        and not lesson_complete(lesson)
        and (intent is None or intent.skill in {"fallback", "atp"})
    ):
        return Intent(skill="lesson_plan")
    return intent


def colleague_provider(planner: Planner, scope: str) -> Provider | None:
    return planner.provider(scope) if os.environ.get("MOTHUSI_PROVIDER") == "ollama" else None


def deferred_reply(title: str, message_id: str | None = None) -> ReplyBody:
    return {
        "message_id": message_id,
        "skill": "deferred",
        "say": f"I'm at capacity: I'm marking {title} on my GPU right now. "
        "I'll attend to this as soon as that's done, and answer here.",
        "card": None,
        "promise": None,
        "action": None,
    }


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
            skills = tuple(s for s in get_args(Skill) if s != "deferred")
            intent, _ = await provider.complete(
                f"Choose only one skill from {skills}. "
                "Return Intent JSON only. Chat never approves sending. "
                f"Valid script ids: {sorted(script_ids or [])}. "
                f"Valid question ids: {sorted(question_ids or [])}.",
                text,
                Intent,
            )
            intent = Intent.model_validate(intent)
            if intent.skill == "deferred":
                return Intent(skill="fallback")
        if intent.script_id is not None:
            sid = intent.script_id.upper()
            if not re.fullmatch(r"L-\d{3}", sid) or (
                script_ids is not None and sid not in script_ids
            ):
                return Intent(skill="fallback")
            intent = intent.model_copy(update={"script_id": sid})
        if intent.question_id is not None and (
            not re.fullmatch(r"\d+(?:\.\d+)*", intent.question_id)
            or (
                intent.skill == "explain_mark"
                and question_ids is not None
                and intent.question_id not in question_ids
            )
        ):
            return Intent(skill="fallback")
        if intent.skill in {"explain_mark", "send_feedback"} and not intent.script_id:
            return Intent(skill="fallback")
        if intent.skill == "explain_mark" and not intent.question_id:
            return Intent(skill="fallback")
        return intent
    except Exception:
        return Intent(skill="fallback")


PAUSED_RUN = "The run is taking longer than planned; your decision is waiting on the run."


def counted(value: object, noun: str) -> str:
    return f"{value} {noun}{'' if Decimal(str(value)) == 1 else 's'}"


def teacher_say(text: str) -> str:
    """Event details can contain internal metering; keep it out of teacher replies."""
    lines: list[str] = []
    for line in text.splitlines():
        safe = (
            PAUSED_RUN
            if re.search(r"\b(?:budget|cost|spent|rand|zar)\b|\bR\s*\d", line, re.I)
            else line
        )
        if safe != PAUSED_RUN or safe not in lines:
            lines.append(safe)
    return "\n".join(lines)


def compose(
    intent: Intent,
    state: RunState | None,
    promises: set[str],
    early_sent: set[str],
    *,
    events: Sequence[Event] = (),
    pack_path: Path | None = None,
    packs_dir: Path = PACKS,
) -> ReplyBody:
    reply = _compose(
        intent, state, promises, early_sent, events=events, pack_path=pack_path, packs_dir=packs_dir
    )
    reply["say"] = teacher_say(reply["say"])
    return reply


def _compose(
    intent: Intent,
    state: RunState | None,
    promises: set[str],
    early_sent: set[str],
    *,
    events: Sequence[Event] = (),
    pack_path: Path | None = None,
    packs_dir: Path = PACKS,
) -> ReplyBody:
    """Pure: no model, writes or delivery. All marks and wording come from this snapshot."""
    reply: ReplyBody = {
        "message_id": None,
        "skill": intent.skill,
        "say": "",
        "card": None,
        "promise": None,
        "action": None,
    }
    if intent.skill == "open":
        return open_reply(intent, state, reply)
    if intent.skill in {"run_report", "run_walls", "memo_item", "poa", "atp", "cannot"}:
        reply["say"] = grounded(intent, state, events, pack_path, packs_dir)
        if intent.skill == "run_report" and state and state["state"] == "awaiting_approval":
            reply["action"] = {"open": "stats", "script_id": None}
        return reply
    if state is None:
        reply["say"] = (
            "Choose a run to ask about its status, marks or feedback."
            if intent.skill != "fallback"
            else "I can report on a run, explain marks, show the memo, PoA or ATP, "
            "quote curriculum "
            "documents, and research a lesson plan."
        )
        return reply
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
            f"{'script' if len(state['scripts']) == 1 else 'scripts'} marked, "
            f"{waiting or 'none'} waiting for you. {counted(minutes, 'minute')} in."
        )
        if (state.get("question") or {}).get("topic") == "budget" or state.get("halted"):
            reply["say"] += " " + teacher_say(str(state.get("halted") or "budget"))
    elif intent.skill == "fallback":
        reply["say"] = (
            "I can show run stats and problems, the memo, PoA and ATP, quote curriculum documents, "
            "and research a lesson plan. I can also tell you how far I am, explain a "
            'mark (try "why did L-014 get its mark on 2.1?"), or bring you one learner\'s note '
            'to approve (try "send L-001 their feedback"). A note is released only from its card.'
        )
    elif sid is None or sid not in {s["script_id"] for s in state["scripts"]}:
        reply["say"] = f"There's no learner {sid} in this class."
    elif intent.skill == "explain_mark":
        reply["action"] = {"open": "script", "script_id": sid}
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
        empty: dict[str, Any] = {}
        verdict = next((v for v in state.get("verdicts", []) if v["pseudonym"] == sid), empty)
        drafts = state.get("drafts") or {}
        notes = cast(list[dict[str, Any]], drafts.get("learner_notes", []))
        note = next(
            (n for n in notes if n["pseudonym"] == sid),
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
            reply["action"] = {"open": "note", "script_id": sid}
            reply["card"] = {
                "script_id": sid,
                "learner": sid,
                **{
                    k: note[k]
                    for k in ("outcome", "mark_with_quote", "next_task", "self_assessment_prompt")
                },
            }
    return reply


def open_reply(intent: Intent, state: RunState | None, reply: ReplyBody) -> ReplyBody:
    """Only open targets that exist at this point in the run."""
    target = intent.view
    sid = intent.script_id
    if target in {"desk", "new"}:
        reply["say"] = "Opening your desk." if target == "desk" else "Starting a new set."
        reply["action"] = {"open": "desk" if target == "desk" else "new", "script_id": None}
    elif state is None:
        reply["say"] = "Choose a run to open its scripts, notes, statistics or approval."
    elif target == "stats":
        reply["say"] = report(Intent(skill="run_report"), state)
        if state["state"] == "awaiting_approval":
            reply["action"] = {"open": "stats", "script_id": None}
    elif target == "approval":
        if state["state"] == "awaiting_approval":
            reply["say"] = "Opening the approval."
            reply["action"] = {"open": "approval", "script_id": None}
        else:
            reply["say"] = "Approval will be ready after marking, moderation and review."
    elif target in {"script", "note"}:
        if sid is None:
            reply["say"] = "Which learner's script or note should I open? Use their L- number."
        elif sid not in {s["script_id"] for s in state["scripts"]}:
            reply["say"] = f"There's no learner {sid} in this class."
        elif target == "note" and state["state"] == "awaiting_approval":
            notes = cast(list[dict[str, Any]], (state.get("drafts") or {}).get("learner_notes", []))
            if any(note["pseudonym"] == sid for note in notes):
                reply["say"] = f"Opening {sid}'s note."
                reply["action"] = {"open": "note", "script_id": sid}
            else:
                reply["say"] = f"{sid}'s note isn't written yet. I'll open the script instead."
                reply["action"] = {"open": "script", "script_id": sid}
        else:
            reply["say"] = (
                f"{sid}'s note isn't written yet. I'll open the script instead."
                if target == "note"
                else f"Opening {sid}'s script."
            )
            reply["action"] = {"open": "script", "script_id": sid}
    else:
        reply["say"] = (
            "I can open a learner's script or note, statistics, approval, your desk or a new set."
        )
    return reply


async def entries(planner: Planner, namespace: str, aid: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    while page := await planner.store.asearch((namespace, aid), limit=100, offset=len(found)):
        found.update({item.key: item.value for item in page})
    return found


async def _reply(
    planner: Planner, aid: str, message_id: str, intent: Intent, state: RunState, text: str = ""
) -> None:
    promises = await entries(planner, "promises", aid)
    sent = await entries(planner, "early_sent", aid)
    state = {
        **state,
        "observed_at": planner.clock().isoformat(),
        "early_sent_at": {sid: item["sent_at"] for sid, item in sent.items()},
    }
    saved = await planner.store.aget(("lesson", aid), "slots")
    lesson = Lesson.model_validate(saved.value) if saved else None
    result = await answer(
        text,
        state=state,
        intent=intent,
        lesson=lesson,
        knowledge=planner.knowledge,
        events=planner.sink(aid).read(),
        promises=set(promises),
        early_sent=set(sent),
    )
    body = result["reply"]
    if result["lesson"] is not None:
        await planner.store.aput(("lesson", aid), "slots", result["lesson"])
    body["message_id"] = message_id
    if body["promise"]:
        await planner.store.aput(("promises", aid), body["promise"], {"message_id": message_id})
    planner.sink(aid).emit(EventKind.REPLY, body["say"][:80], body)
    # Close the race where review finished while understanding this message.
    if body["promise"]:
        latest = await planner.snapshot(aid)
        if latest.get("state") == "awaiting_approval":
            await keep_promises(planner, aid, latest)


async def respond(
    planner: Planner,
    assessment_id: str,
    message_id: str,
    text: str,
    *,
    capacity: Runs | None = None,
) -> None:
    from mothusi_core.graph.planner import pseudonymise_text

    state = await planner.snapshot(assessment_id)
    text = pseudonymise_text(text, state["names"])
    provider = colleague_provider(planner, assessment_id)
    saved = await planner.store.aget(("lesson", assessment_id), "slots")
    lesson = Lesson.model_validate(saved.value) if saved else None
    intent = route_rules(text, lesson)
    if intent is None and capacity is not None and (busy := await capacity.busy_run()):
        await capacity.defer(assessment_id, message_id, text)
        body = deferred_reply(busy[1], message_id)
        planner.sink(assessment_id).emit(EventKind.REPLY, body["say"][:80], body)
        return
    if intent is None or intent.skill != "lesson_plan":
        intent = await understand(
            text,
            provider,
            script_ids={s["script_id"] for s in state["scripts"]},
            question_ids={q["question_id"] for q in state["memo"]["items"]},
        )
    if lesson is not None and not lesson_complete(lesson) and intent.skill in {"fallback", "atp"}:
        intent = Intent(skill="lesson_plan")
    await _reply(
        planner, assessment_id, message_id, intent, await planner.snapshot(assessment_id), text
    )


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


def pack_data(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        return tomllib.load(source)


def held_packs(packs_dir: Path) -> dict[str, Path]:
    return {
        str(pack_data(p)["subject"]): p for p in sorted((packs_dir / "dbe-caps").glob("*.toml"))
    }


def subject_in(text: str, packs_dir: Path = PACKS) -> str | None:
    for subject in held_packs(packs_dir):
        if re.search(rf"\b{re.escape(subject)}\b", text, re.I):
            return subject
    return "Mathematics" if re.search(r"\bmaths\b", text, re.I) else None


def atp_rows(subject: str, packs_dir: Path) -> list[dict[str, Any]] | None:
    path = packs_dir / "dbe-caps" / "atp" / f"{subject.lower()}-gr10.toml"
    # Only known subjects are used to construct paths.
    if subject not in held_packs(packs_dir) or not path.is_file():
        return None
    return cast(list[dict[str, Any]], pack_data(path).get("week", []))


def select_weeks(
    rows: list[dict[str, Any]], weeks: list[int] | None, topic: str | None
) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if (r["week"] in weeks if weeks else topic.lower() in r["topic"].lower() if topic else True)
    ]


def quote_weeks(rows: list[dict[str, Any]]) -> str:
    return (
        "\n".join(f'Week {r["week"]}: "{r["topic"]}" — {r["source"]}' for r in rows)
        or "No matching ATP weeks in this pack."
    )


def refusal(reason: str | None, packs_dir: Path) -> str:
    if reason == "contact":
        return (
            "Mothusi never contacts learners or parents. Release a reviewed learner note "
            "from its approval card."
        )
    if reason == "export":
        return "Report cards and SA-SAMS export are not built."
    if reason == "marks":
        return (
            "Marks change only through Needs-you or approval. Approve the set on its "
            "approval screen."
        )
    return f"I only hold the Grade 10 {' and '.join(held_packs(packs_dir))} ATPs."


def grounded(
    intent: Intent,
    state: RunState | None,
    events: Sequence[Event],
    pack_path: Path | None,
    packs_dir: Path,
) -> str:
    if intent.skill == "cannot":
        return refusal(intent.refusal, packs_dir)
    if state is not None:
        pack_path = Path(state["pack_path"])
    pack = pack_data(pack_path) if pack_path else None
    subject = str(pack["subject"]) if pack else intent.subject
    if intent.skill in {"poa", "atp"}:
        if subject is None:
            return "Which subject: " + " or ".join(held_packs(packs_dir)) + "?"
        if not pack:
            path = held_packs(packs_dir).get(subject)
            if path is None:
                return refusal("sources", packs_dir)
            pack = pack_data(path)
        if intent.skill == "atp":
            rows = atp_rows(subject, packs_dir)
            return (
                refusal("sources", packs_dir)
                if rows is None
                else quote_weeks(select_weeks(rows, intent.weeks, intent.topic))
            )
        rows = cast(list[dict[str, Any]], pack["poa"])
        if intent.term is not None:
            rows = [r for r in rows if r["term"] == intent.term]
        elif state is not None:
            rows = [r for r in rows if r["name"] == state["poa_task"]]
        lines = [
            f"Term {r['term']}: {r['name']} ({r['kind']}), {counted(r['max_marks'], 'mark')}, "
            f"{r['weight_percent']}% of SBA."
            for r in rows
        ]
        lines.extend(
            str(source)
            for source in pack["sources"]
            if "programme of assessment" in str(source).lower() or "poa" in str(source).lower()
        )
        lines.append(f"SBA: {pack['sba_percent']}% of the year; exam: {pack['exam_percent']}%.")
        rule = cast(dict[str, Any], pack["moderation"])
        lines.append(
            f"Moderation working rule: {rule['sample_percent']}%, at least "
            f"{counted(rule['min_scripts'], 'script')}, {rule['spread']}; "
            f"widen when disagreements exceed "
            f"{rule['widen_above_gap_percent']}%."
        )
        return "\n".join(lines)
    if state is None:
        return "Choose a run first so I can read its memo, marks and event log."
    if intent.skill == "memo_item":
        if intent.view == "check":
            lines: list[str] = []
            for e in events:
                payload = e.payload
                if e.kind == EventKind.NEEDS_TEACHER and payload.get("topic") == "memo":
                    lines.append(
                        f"Flagged {payload.get('question_id')}: {payload.get('title', e.summary)}"
                    )
                elif (
                    (e.kind == EventKind.TEACHER and payload.get("topic") == "memo")
                    or "memo confirmed" in e.summary
                    or "memo not verified" in e.summary
                    or (
                        e.kind == EventKind.DECISION
                        and payload.get("action") == "memo item unverified"
                    )
                ):
                    lines.append(e.summary)
            return (
                "\n".join(lines)
                if lines
                else "The memo-check outcome will be ready after memo check."
            )
        items = cast(list[dict[str, Any]], state["memo"]["items"])
        qid = intent.question_id
        if intent.view == "total" or qid is None:
            selected = [
                i
                for i in items
                if qid is None or i["question_id"] == qid or i["question_id"].startswith(qid + ".")
            ]
            return (
                f"{'Question ' + qid if qid else 'Whole test'}: "
                f"{counted(sum(i['max_marks'] for i in selected), 'mark')}."
                if selected
                else f"No memo question {qid}."
            )
        item = next((i for i in items if i["question_id"] == qid), None)
        return (
            f"{qid}: {item['answer']}. Maximum {counted(item['max_marks'], 'mark')}. "
            f"Marking notes: "
            f"{item['marking_notes'] or 'none recorded'}."
            if item
            else f"No memo item {qid}."
        )
    if intent.skill == "run_walls":
        failed = [
            e for e in events if e.kind == EventKind.CHECK and e.payload.get("passed") is False
        ]
        briefs = [e for e in events if e.kind == EventKind.DECISION and "re-brief" in e.summary]
        disputes = len(state.get("disagreements", []))
        lines = []
        halted = state.get("halted")
        if halted:
            lines.append(f"Halted: {halted}.")
        for e in events:
            if e.kind == EventKind.STATE and e.payload.get("to") == "halted":
                lines.append(e.summary)
        pending = state.get("pending", {})
        for sid in pending:
            flag = next(
                (
                    e
                    for e in reversed(events)
                    if e.kind == EventKind.NEEDS_TEACHER and e.payload.get("script_id") == sid
                ),
                None,
            )
            reason = (
                flag.payload.get("title", flag.summary)
                if flag
                else "marking checks need a teacher decision"
            )
            lines.append(f"{sid} waiting for the teacher: {reason}.")
        question = state.get("question")
        if question:
            lines.append(f"Needs teacher: {question['title']}.")
        lines.append(f"{counted(len(failed), 'failed check')}.")
        lines.extend(str(e.payload.get("detail", e.summary)) for e in failed[-3:])
        lines.append(
            f"{counted(len(briefs), 're-brief')}; {counted(disputes, 'disagreement')}; "
            f"moderation sample "
            f"{'widened' if state.get('widened') else 'not widened'}."
        )
        if not (
            failed
            or briefs
            or disputes
            or state.get("widened")
            or pending
            or question
            or state.get("halted")
        ):
            lines.insert(0, "Nothing went wrong in the recorded run.")
        return "\n".join(lines)
    return report(intent, state)


def report(intent: Intent, state: RunState) -> str:
    if intent.view == "catch_up":
        if not state.get("drafts") or state["state"] in {
            "draft",
            "memo_check",
            "reading_check",
            "marking",
            "moderating",
            "re_marking",
            "analysing",
        }:
            return "Catch-up groups will be ready after writing."
        plan = state.get("catch_up_plan", [])
        misconceptions = state.get("misconceptions", [])
        lines: list[str] = []
        scripts = {r["script_id"]: r for r in state["scripts"]}
        for row in misconceptions:
            qid = row["question_id"]
            members: list[str] = []
            for sid, marked in state.get("marked", {}).items():
                losing = any(
                    m["question_id"] == qid and m["awarded"] < m["max_marks"]
                    for m in marked["marks"]
                )
                script = scripts.get(sid)
                if (
                    losing
                    and script is not None
                    and any(
                        a["question_id"] == qid
                        and any(q in a["text"] for q in row["evidence_quotes"])
                        for a in script["answers"]
                    )
                ):
                    members.append(sid)
            lines.append(
                f"{qid}: {row['pattern']}; {counted(row['scripts'], 'script')}; "
                + ", ".join(members)
            )
        lines.extend(
            f"Week {r['week']}: {r['topic']} — {r['activity']} ({r['atp_source']})" for r in plan
        )
        return "\n".join(lines) or "No catch-up groups recorded after writing."
    converted = state.get("converted", {}) if state.get("moderation_closed") else {}
    if intent.view == "year":
        row = converted.get(intent.script_id or "")
        return (
            f"{intent.script_id}: {counted(row['sba_points'], 'SBA point')}; "
            f"{counted(row['year_points'], 'point')} "
            f"towards the year."
            if row
            else "Year contributions will be ready after analysing; choose a learner in this run."
        )
    stats = state.get("item_stats", []) if state.get("moderation_closed") else []
    if intent.view == "items" and stats:
        weakest = sorted(stats, key=lambda r: Decimal(str(r["facility_percent"])))[:3]
        return "Weakest questions: " + "; ".join(
            f"{r['question_id']}: {Decimal(str(r['mean_awarded'])):.2f}/{r['max_marks']} "
            f"({r['facility_percent']}%)"
            for r in weakest
        )
    marked = state.get("marked", {})
    totals = (
        [Decimal(str(r["raw"])) for r in converted.values()]
        if converted
        else [Decimal(str(r["total"])) for r in marked.values()]
    )
    if not totals:
        return "Statistics will be ready during marking, once a script has been marked."
    final = bool(converted)
    label = "Final" if final else "Provisional"
    lines = [
        f"{label}: {counted(len(totals), 'script')} marked; class average "
        f"{sum(totals) / len(totals):.2f}/{state['memo']['total_marks']}, highest "
        f"{max(totals)}, lowest {min(totals)}, spread {max(totals) - min(totals)}."
    ]
    if stats:
        lines.extend(
            f"{r['question_id']}: {Decimal(str(r['mean_awarded'])):.2f}/{r['max_marks']}"
            for r in stats
        )
    else:
        for item in state["memo"]["items"]:
            awards = [
                Decimal(str(m["awarded"]))
                for row in marked.values()
                for m in row["marks"]
                if m["question_id"] == item["question_id"]
            ]
            if awards:
                lines.append(
                    f"Provisional {item['question_id']}: "
                    f"{sum(awards) / len(awards):.2f}/{item['max_marks']}"
                )
        lines.append("Final statistics and weakest questions will be ready after analysing.")
    return "\n".join(lines)


def fill_lesson(text: str, lesson: Lesson, packs_dir: Path = PACKS) -> Lesson:
    update: dict[str, Any] = {}
    if subject := subject_in(text, packs_dir):
        update["subject"] = subject
    weeks = re.findall(r"\bweek\s+(\d+)\b", text, re.I)
    term = re.search(r"\bterm\s+([1-4])\s+week\s+(\d+)\b", text, re.I)
    if term:
        local = int(term[2])
        if 1 <= local <= (10 if term[1] == "4" else 11):
            update["weeks"] = [(int(term[1]) - 1) * 11 + local]
    elif weeks:
        update["weeks"] = [int(w) for w in weeks if 1 <= int(w) <= 43]
    for field, pattern in [
        ("lessons", r"\b(\d+)\s+lessons?\b"),
        ("minutes", r"\b(\d+)\s+minutes?\b"),
    ]:
        match = re.search(pattern, text, re.I)
        if match:
            value = int(match[1])
            if 1 <= value <= (100 if field == "lessons" else 1440):
                update[field] = value
    topic = re.search(
        r"\b(?:about|topic\s*:?|on|lesson plan for)\s+(.+?)(?=\s+for\b|[,;]|$)", text, re.I
    )
    if (
        topic
        and not re.match(r"(?:week|term)\b", topic[1], re.I)
        and subject_in(topic[1], packs_dir) is None
    ):
        update["topic"] = topic[1].strip()
    if not update.get("topic") and lesson.subject and not lesson.weeks and not update.get("weeks"):
        rows = atp_rows(lesson.subject, packs_dir) or []
        candidate = text.strip().rstrip(".?")
        if candidate and any(candidate.lower() in r["topic"].lower() for r in rows):
            update["topic"] = candidate
    notes = re.search(r"\bnotes?\s*:\s*(.+)", text, re.I)
    if notes:
        update["notes"] = notes[1]
    return Lesson.model_validate({**lesson.model_dump(), **update})


def lesson_complete(lesson: Lesson) -> bool:
    return bool(
        lesson.subject and (lesson.weeks or lesson.topic) and lesson.lessons and lesson.minutes
    )


class AnswerBody(TypedDict):
    reply: ReplyBody
    ask: str | None
    lesson: dict[str, Any] | None
    deferred_id: str | None


async def passages(query: str, subject: str | None, knowledge: Knowledge) -> str:
    if isinstance(knowledge, NoKnowledge):
        return "I can't reach the curriculum documents right now."
    try:
        found = await knowledge.search(query, k=4, subject=subject, grade=10)
        return (
            "\n".join(
                f"{p.source}, p {p.page}: {p.text[:200]}" for p in found[:4] if p.page is not None
            )
            or "No page-cited curriculum passages found."
        )
    except Exception:
        return "I can't reach the curriculum documents right now."


async def answer(
    text: str,
    *,
    state: RunState | None = None,
    intent: Intent | None = None,
    subject: str | None = None,
    pack_path: Path | None = None,
    lesson: Lesson | None = None,
    knowledge: Knowledge | None = None,
    events: Sequence[Event] = (),
    packs_dir: Path = PACKS,
    promises: set[str] | None = None,
    early_sent: set[str] | None = None,
    provider: Provider | None = None,
) -> AnswerBody:
    if intent is None:
        intent = route_rules(text, lesson)
        if intent is None or intent.skill != "lesson_plan":
            intent = await understand(
                text,
                provider,
                script_ids={s["script_id"] for s in state["scripts"]} if state else set(),
                question_ids={q["question_id"] for q in state["memo"]["items"]} if state else set(),
            )
        if (
            lesson is not None
            and not lesson_complete(lesson)
            and intent.skill in {"fallback", "atp"}
        ):
            intent = Intent(skill="lesson_plan")
    subject = subject_in(text, packs_dir) or subject
    if state:
        pack_path = Path(state["pack_path"])
    if pack_path:
        subject = str(pack_data(pack_path)["subject"])
    if subject:
        subject = subject_in(subject, packs_dir) or subject
    intent = intent.model_copy(update={"subject": subject or intent.subject})
    if (
        subject
        and subject not in held_packs(packs_dir)
        and intent.skill in {"poa", "atp", "curriculum", "lesson_plan"}
    ):
        intent = Intent(skill="cannot", refusal="sources")
    reply = compose(
        intent,
        state,
        promises or set(),
        early_sent or set(),
        events=events,
        pack_path=pack_path,
        packs_dir=packs_dir,
    )
    result: AnswerBody = {"reply": reply, "ask": None, "lesson": None, "deferred_id": None}
    if intent.skill == "curriculum":
        reply["say"] = await passages(
            text or intent.topic or "",
            subject,
            knowledge if knowledge is not None else knowledge_from_environment(),
        )
    elif intent.skill == "lesson_plan":
        lesson = fill_lesson(text, lesson or Lesson(subject=subject), packs_dir)
        result["lesson"] = lesson.model_dump(mode="json")
        if lesson.subject and atp_rows(lesson.subject, packs_dir) is None:
            reply.update(skill="cannot", say=refusal("sources", packs_dir))
            reply["say"] = teacher_say(reply["say"])
            return result
        missing = next(
            (
                question
                for present, question in [
                    (lesson.subject, "Which subject: " + " or ".join(held_packs(packs_dir)) + "?"),
                    (lesson.weeks or lesson.topic, "Which week or topic should the lessons cover?"),
                    (lesson.lessons, "How many lessons should I prepare?"),
                    (lesson.minutes, "How long is each lesson in minutes?"),
                ]
                if not present
            ),
            None,
        )
        if missing:
            result["ask"] = missing
            reply["say"] = missing
            reply["say"] = teacher_say(reply["say"])
            return result
        assert lesson.subject is not None
        rows = select_weeks(atp_rows(lesson.subject, packs_dir) or [], lesson.weeks, lesson.topic)
        if not rows:
            result["lesson"] = {**lesson.model_dump(mode="json"), "weeks": None, "topic": None}
            result["ask"] = "Which week or topic from the ATP should I use?"
            reply["say"] = result["ask"]
            reply["say"] = teacher_say(reply["say"])
            return result
        query = lesson.topic or "; ".join(dict.fromkeys(str(r["topic"]) for r in rows))
        research = await passages(
            query,
            lesson.subject,
            knowledge if knowledge is not None else knowledge_from_environment(),
        )
        reply["say"] = (
            f"{lesson.subject}: {counted(lesson.lessons, 'lesson')} of "
            f"{counted(lesson.minutes, 'minute')}.\n"
            + quote_weeks(rows)
            + "\n"
            + research
            + (f"\nClass notes: {lesson.notes}" if lesson.notes else "")
            + "\nDrafting the full plan from this is the next step I'll be able to take."
        )
    reply["say"] = teacher_say(reply["say"])
    return result
