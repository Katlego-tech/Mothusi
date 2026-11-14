"""Mothusi's HTTP API: the eight endpoints of docs/architecture/design/console-ui.md §6, over the
Planner graph.

    uv run --frozen uvicorn mothusi_core.main:app --port 8000

Design: marking-pipeline.md §6.2. Runs are background tasks on one Planner; the event log is the
JSONL file the SSE endpoint follows; each teacher endpoint answers the pending question (409 when
there is none of that kind) and resumes the graph in the background.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import jwt
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field, ValidationError, field_validator

from mothusi_contracts import (
    Answer,
    AssessmentState,
    AtpWeek,
    ClassGroup,
    Event,
    EventKind,
    LearnerAccount,
    Memo,
    PracticeTest,
    ReadingAnswer,
    ResultStatus,
    Script,
)
from mothusi_core import health
from mothusi_core.agents.executor import Approval, execute, executor_brief
from mothusi_core.agents.setter import MEMO_DRAFT_LIMIT, memo_too_long
from mothusi_core.auth import (
    AuthUnavailableError,
    TokenVerifier,
    needs_token,
    verifier_from_environment,
)
from mothusi_core.colleague import AnswerBody, Intent, Lesson, compose, entries, respond
from mothusi_core.colleague import answer as colleague_answer
from mothusi_core.delivery import delivery_from_environment, store_from_environment
from mothusi_core.events import follow, frame, with_pings
from mothusi_core.graph import inbox
from mothusi_core.graph.planner import (
    Planner,
    apply_readings,
    attempt_is_complete,
    check_memo_fits,
    executor_bundle,
    new_run,
    pack_ref,
    pseudonymise_text,
    teacher_email,
    teacher_messages,
)
from mothusi_core.graph.state import RunState
from mothusi_core.knowledge import knowledge_from_environment
from mothusi_core.learners import (
    ClassStore,
    class_store_from_environment,
    make_accounts,
    record_consent,
)
from mothusi_core.memo_drafts import MemoDrafts
from mothusi_core.pages import (
    PageIntake,
    check_crop_link,
    crop_link,
    load_manifests,
    missing_pages,
    read_uploads,
    safe_name,
)
from mothusi_core.practice import (
    AttemptStore,
    ClassGoneError,
    PracticeRecord,
    PracticeStore,
    approve,
    atp_weeks_for,
    attempt_store_from_environment,
    mark_attempt,
    move,
    new_attempt_id,
    practice_store_from_environment,
    progress,
    record_json,
    set_and_check,
    test_title,
)
from mothusi_core.providers import ROOT, Fallback, Limited, from_environment
from mothusi_delivery import LinkExpired, LinkInvalid, NotePage, note_page_html, read_note_link
from mothusi_intake import (
    MAX_MEMO_BYTES,
    load_class_list,
    load_memo,
    load_scripts,
    memo_text,
    pseudonymise,
)
from mothusi_intake.pages import sweep_names
from mothusi_llm import Provider
from mothusi_outputs.sheets import answer_sheets_pdf
from mothusi_rules import find_task, load_atp_weeks, load_pack

log = logging.getLogger("mothusi")
PACKS = ROOT / "packs"
BUNDLE_STATES = {"awaiting_approval", "approved", "delivered"}
PARSE_ERRORS = (ValueError, ValidationError, KeyError, UnicodeDecodeError, OSError, csv.Error)


class DeskAssessment(BaseModel):
    assessment_id: str
    title: str
    state: AssessmentState
    waiting: bool
    halted: str | None
    started_at: str
    scripts: int
    subject: str
    grade: int


class DeskAtpWeek(BaseModel):
    week: int
    term: int
    term_week: int
    topic: str
    source: str


class Runs:
    """The background task per assessment, so a teacher answer never races a running node."""

    def __init__(self, planner: Planner) -> None:
        self.planner = planner
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._idle: set[str] = set()
        self.replies: set[asyncio.Task[Any]] = set()

    def lock(self, assessment_id: str) -> asyncio.Lock:
        """Held from reading the pending question until the resume is launched, so two answers
        to one question can never both be accepted: the second finds the run busy (409)."""
        return self._locks.setdefault(assessment_id, asyncio.Lock())

    def busy(self, assessment_id: str) -> bool:
        task = self.tasks.get(assessment_id)
        return task is not None and not task.done() and assessment_id not in self._idle

    def launch(self, assessment_id: str, work: Any) -> None:
        self._idle.discard(assessment_id)

        async def guarded() -> None:
            try:
                await work
                while True:
                    # Serialize the final inbox check with POSTs: an answer arriving as the
                    # interrupt opens either resumes here or sees an idle run in the endpoint.
                    async with self.lock(assessment_id):
                        state = await self.planner.snapshot(assessment_id)
                        question = state.get("question") or {}
                        entry = None
                        if state.get("state") == "marking" and question.get("topic") == "script":
                            entry = await self.planner.store.aget(
                                ("inbox", assessment_id), question["script_id"]
                            )
                        if entry is None:
                            self._idle.add(assessment_id)
                            return
                    await self.planner.resume(assessment_id, entry.value["answer"])
            except Exception as error:  # a crashed node is logged for the teacher, never hidden
                log.exception("run %s stopped", assessment_id)
                self.planner.sink(assessment_id).decision("halt", f"the run stopped: {error}")

        self.tasks[assessment_id] = asyncio.create_task(guarded())

    def known(self, assessment_id: str) -> bool:
        return (self.planner.data_dir / assessment_id / "events.jsonl").exists()


def _planner_from_environment(checkpointer: Any, store: BaseStore) -> Planner:
    providers = from_environment()
    return Planner(
        primary=providers.text,
        fallback=providers.fallback,
        roles={"reader": providers.reader, "moderator": providers.moderator},
        fallback_roles=dict(providers.fallbacks),
        priced=providers.priced,
        data_dir=Path(os.environ.get("MOTHUSI_DATA") or ROOT / ".mothusi"),
        checkpointer=checkpointer,
        store=store,
        teacher_email=teacher_email(),
        knowledge=knowledge_from_environment(),
    )


def create_app(
    planner: Planner | None = None,
    packs_dir: Path = PACKS,
    verifier: TokenVerifier | None = None,
    *,
    auth_from_environment: bool = True,
) -> FastAPI:
    """The core API. With no `verifier`, Cognito auth comes from the environment (off when
    COGNITO_USER_POOL_ID is unset); pass `auth_from_environment=False` to force it off."""
    if verifier is None and auth_from_environment:
        verifier = verifier_from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if planner is not None:
            app.state.runs = Runs(planner)
            yield
            return
        url = os.environ.get("POSTGRES_URL")
        if url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres.aio import AsyncPostgresStore

            async with (
                AsyncPostgresSaver.from_conn_string(url) as saver,
                AsyncPostgresStore.from_conn_string(url) as store,
            ):
                await saver.setup()
                await store.setup()
                app.state.runs = Runs(_planner_from_environment(saver, store))
                yield
        else:
            log.warning("POSTGRES_URL not set: checkpoints are in memory and end with the process")
            app.state.runs = Runs(_planner_from_environment(InMemorySaver(), InMemoryStore()))
            yield

    app = FastAPI(title="Mothusi", lifespan=lifespan)

    if verifier is not None:
        teacher_auth = verifier

        @app.middleware("http")
        async def require_teacher(  # pyright: ignore[reportUnusedFunction]
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            if needs_token(request.method, request.url.path):
                header = request.headers.get("authorization", "")
                scheme, _, token = header.partition(" ")
                if scheme.lower() != "bearer" or not token:
                    return JSONResponse({"message": "sign in first"}, status_code=401)
                try:
                    # In a thread: fetching Cognito's keys is blocking I/O, and the event loop
                    # also serves /health/live, the SSE pings and the running assessments.
                    claims = await asyncio.to_thread(teacher_auth.verify, token)
                    request.state.teacher_sub = str(claims.get("sub") or "")
                except jwt.InvalidTokenError:
                    return JSONResponse({"message": "sign in again"}, status_code=401)
                except AuthUnavailableError:
                    return JSONResponse(
                        {"message": "sign-in cannot be checked right now; try again"},
                        status_code=503,
                    )
            return await call_next(request)

    # Added after the auth middleware so it wraps it: a 401 or 503 from sign-in then carries the
    # same CORS headers as any other response, and the console can show the sign-in error.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("MOTHUSI_WEB_ORIGINS", "http://localhost:5173").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/n/{token}")
    async def note(request: Request, token: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        """A learner's note page (outputs-and-delivery.md §6.2). The token is the credential:
        one fixed 404 for anything not valid, 410 once it has expired. Never cached."""
        r = runs(request)
        key = os.environ.get("MOTHUSI_NOTE_KEY", "").encode()
        if len(key) < 32:
            return _note_response(NOT_FOUND_PAGE, 404)
        try:
            claims = read_note_link(key, token, datetime.now(UTC))
        except LinkExpired:
            return _note_response(EXPIRED_PAGE, 410)
        except LinkInvalid:
            return _note_response(NOT_FOUND_PAGE, 404)
        key_path = f"{claims.assessment_id}/bundle/note-pages/{claims.learner_pseudonym}.json"

        def fetch() -> bytes:  # S3 is blocking I/O: off the event loop
            store = store_from_environment(r.planner.data_dir)
            try:
                return store.get(key_path)
            except KeyError:
                return store.get(
                    f"{claims.assessment_id}/early/{claims.learner_pseudonym}/"
                    f"note-pages/{claims.learner_pseudonym}.json"
                )

        try:
            page = NotePage.model_validate_json(await asyncio.to_thread(fetch))
        except KeyError, ValueError:
            return _note_response(NOT_FOUND_PAGE, 404)
        return _note_response(note_page_html(page), 200)

    @app.get("/health/live")
    async def live() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "live"}

    @app.get("/health")
    async def deep_health() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        status, body = await health.report()
        return JSONResponse(body, status_code=status)

    def runs(request: Request) -> Runs:
        return request.app.state.runs

    def pack_files() -> dict[str, Path]:
        return {pack_ref(p): p for p in sorted(packs_dir.glob("*/*.toml"))}

    @asynccontextmanager
    async def pending(
        request: Request, assessment_id: str
    ) -> AsyncGenerator[tuple[Runs, RunState]]:
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        async with r.lock(assessment_id):
            if r.busy(assessment_id):
                raise HTTPException(409, {"message": "the run is working; wait for its question"})
            yield r, await r.planner.snapshot(assessment_id)

    def conflict(message: str) -> HTTPException:
        return HTTPException(409, {"message": message})

    def unprocessable(field: str, message: str) -> HTTPException:
        return HTTPException(422, {"errors": [{"field": field, "message": message}]})

    @app.exception_handler(HTTPException)
    async def _detail_as_body(request: Request, error: HTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(error.detail, status_code=error.status_code)

    @app.post("/colleague/messages")
    async def colleague_message(request: Request, body: ColleagueMessageBody) -> AnswerBody:
        r = runs(request)
        context = body.context
        state: RunState | None = None
        events: tuple[Event, ...] = ()
        early_sent: set[str] = set()
        text = body.text
        if context.assessment_id is not None:
            if not r.known(context.assessment_id):
                raise HTTPException(404, {"message": f"no assessment {context.assessment_id}"})
            state = await r.planner.snapshot(context.assessment_id)
            sent = await entries(r.planner, "early_sent", context.assessment_id)
            early_sent = set(sent)
            state = state.copy()
            state["observed_at"] = r.planner.clock().isoformat()
            state["early_sent_at"] = {sid: item["sent_at"] for sid, item in sent.items()}
            text = pseudonymise_text(text, state["names"])
            events = tuple(r.planner.sink(context.assessment_id).read())
        path = None
        if context.pack_id is not None and state is None:
            path = pack_files().get(context.pack_id)
            if path is None:
                raise HTTPException(404, {"message": f"no pack {context.pack_id}"})
        return await colleague_answer(
            text,
            state=state,
            events=events,
            subject=context.subject,
            pack_path=path,
            lesson=context.lesson,
            early_sent=early_sent,
            knowledge=r.planner.knowledge,
            packs_dir=packs_dir,
        )

    @app.post("/assessments/{assessment_id}/messages", status_code=202)
    async def message(request: Request, assessment_id: str, body: MessageBody) -> dict[str, str]:
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        state = await r.planner.snapshot(assessment_id)
        if state.get("state") in {"delivered", "halted"} or state.get("halted"):
            raise conflict("the run is delivered; start a new run to ask about it")
        sink = r.planner.sink(assessment_id)
        n = sum(e.kind is EventKind.MESSAGE for e in sink.read()) + 1
        message_id = f"m-{n}"
        text = pseudonymise_text(body.text, state["names"])
        sink.emit(EventKind.MESSAGE, text[:80], {"message_id": message_id, "text": text})
        task = asyncio.create_task(respond(r.planner, assessment_id, message_id, body.text))
        r.replies.add(task)
        task.add_done_callback(r.replies.discard)
        return {"message_id": message_id}

    @app.post("/assessments/{assessment_id}/notes/{script_id}/send", status_code=202)
    async def send_note(request: Request, assessment_id: str, script_id: str) -> dict[str, str]:
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        async with r.lock(assessment_id):
            p = r.planner
            state = await p.snapshot(assessment_id)
            if script_id not in {s["script_id"] for s in state["scripts"]}:
                raise HTTPException(404, {"message": f"no learner {script_id} in this class"})
            if state["state"] != "awaiting_approval":
                raise conflict("the note is not settled; wait for awaiting_approval")
            if await p.store.aget(("early_sent", assessment_id), script_id):
                raise conflict("the note was already sent")

            def offers(card: object) -> bool:
                return (
                    isinstance(card, dict)
                    and cast(dict[str, Any], card).get("script_id") == script_id
                )

            offered = any(
                e.kind is EventKind.REPLY and offers(e.payload.get("card"))
                for e in p.sink(assessment_id).read()
            )
            if not offered:
                raise conflict("no reply has offered this card")
            reply = compose(Intent(skill="send_feedback", script_id=script_id), state, set(), set())
            if reply["card"] is None:
                raise conflict(reply["say"])
            bundle = executor_bundle(p, state)
            bundle = bundle.model_copy(
                update={
                    "root": str(p.data_dir / assessment_id / "early" / script_id),
                    "learner_notes": tuple(
                        n for n in bundle.learner_notes if n.note.pseudonym == script_id
                    ),
                }
            )
            approval = Approval(by="the teacher", at=p.clock())
            brief = executor_brief(
                assessment_id,
                AssessmentState.APPROVED,
                approval,
                bundle,
                teacher_messages(p, state, bundle),
            )
            result = await asyncio.to_thread(
                execute,
                brief,
                p.mailer(assessment_id),
                delivery_from_environment(p.data_dir),
                publish_prefix=f"{assessment_id}/early/{script_id}",
            )
            if result.status is not ResultStatus.DONE:
                raise HTTPException(503, {"message": result.reason})
            await p.store.aput(
                ("early_sent", assessment_id), script_id, {"sent_at": p.clock().isoformat()}
            )
            payload = {
                "script_id": script_id,
                "sent": result.outputs["sent"],
                "written": result.outputs["written"],
            }
            p.sink(assessment_id).emit(EventKind.NOTE_SENT, f"{script_id}'s note sent", payload)
            return {"state": state["state"]}

    # ------------------------------------------------------------------ packs and creation

    @app.get("/packs")
    async def packs() -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        out: list[dict[str, Any]] = []
        for ref, path in pack_files().items():
            pack = load_pack(path)
            out.append(
                {
                    "pack_id": ref,
                    "subject": pack.subject,
                    "grade": pack.grade,
                    "moderation": pack.moderation.model_dump(mode="json"),
                    "tasks": [
                        {
                            "term": t.term,
                            "name": t.name,
                            "max_marks": t.max_marks,
                            "weight_percent": str(t.weight_percent),
                        }
                        for t in pack.poa
                    ],
                }
            )
        return out

    async def assessments(request: Request) -> list[DeskAssessment]:
        r = runs(request)

        def read() -> list[DeskAssessment]:
            rows: list[tuple[datetime, DeskAssessment]] = []
            for path in r.planner.data_dir.glob("*/events.jsonl"):
                events = r.planner.sink(path.parent.name).read()
                created = next((e for e in events if e.kind is EventKind.CREATED), None)
                if created is None:
                    continue
                payload = created.payload
                subject, grade = payload.get("subject"), payload.get("grade")
                if not isinstance(subject, str) or not isinstance(grade, int):
                    pack = load_pack(pack_files()[str(payload["pack_id"])])
                    subject = subject if isinstance(subject, str) else pack.subject
                    grade = grade if isinstance(grade, int) else pack.grade
                state = AssessmentState.DRAFT
                questions: list[Event] = []
                halted: str | None = None
                for event in events:
                    data = event.payload
                    destination = data.get("to")
                    if event.kind is EventKind.STATE and isinstance(destination, str):
                        next_state = AssessmentState(destination)
                        if next_state != state:
                            # Script flags survive stage progression until their own answer.
                            questions = [q for q in questions if q.payload.get("script_id")]
                        state = next_state
                    elif event.kind is EventKind.NEEDS_TEACHER:
                        questions.append(event)
                    elif event.kind is EventKind.TEACHER:
                        questions = [
                            q
                            for q in questions
                            if not all(
                                q.payload.get(key) == data.get(key)
                                for key in (
                                    ("topic", "script_id")
                                    if q.payload.get("script_id")
                                    else ("topic", "question_id")
                                )
                                if q.payload.get(key) is not None
                            )
                        ]
                    elif event.kind is EventKind.DECISION and data.get("action") == "halt":
                        halted = str(data.get("reason") or event.summary)
                ids = payload.get("script_ids")
                rows.append(
                    (
                        events[0].at,
                        DeskAssessment(
                            assessment_id=created.assessment_id,
                            title=str(payload["title"]),
                            state=state,
                            waiting=bool(questions),
                            halted=halted,
                            started_at=events[0].at.isoformat(),
                            scripts=len(ids) if isinstance(ids, list) else 0,
                            subject=subject,
                            grade=grade,
                        ),
                    )
                )
            return [row for _, row in sorted(rows, key=lambda pair: pair[0], reverse=True)]

        return await asyncio.to_thread(read)

    app.add_api_route("/assessments", assessments, methods=["GET"])

    async def pack_atp(pack_id: str, subject: str) -> list[DeskAtpWeek]:
        # Accept both a pack directory and the subject pack id returned by /packs.
        pack_path = pack_files().get(pack_id)
        directory = pack_path.parent if pack_path else packs_dir / pack_id
        path = directory / "atp" / f"{subject.lower()}-gr10.toml"
        if not path.resolve().is_relative_to(packs_dir.resolve()) or not path.is_file():
            raise HTTPException(404, {"message": f"no ATP for {subject} in {pack_id}"})
        weeks = await asyncio.to_thread(load_atp_weeks, path)
        return [
            DeskAtpWeek(
                week=w.week,
                term=(w.week - 1) // 11 + 1,
                term_week=(w.week - 1) % 11 + 1,
                topic=w.topic,
                source=w.source,
            )
            for w in weeks
        ]

    app.add_api_route("/packs/{pack_id:path}/atp", pack_atp, methods=["GET"])

    drafts = MemoDrafts()

    # ------------------------------------------------------------------ classes

    def teacher(request: Request) -> str:
        """The signed-in teacher's Cognito subject; "local" when sign-in is off (the laptop)."""
        return getattr(request.state, "teacher_sub", "") or "local"

    def class_store(request: Request) -> ClassStore:
        store = getattr(request.app.state, "classes", None)
        if store is None:
            store = class_store_from_environment(runs(request).planner.data_dir)
            request.app.state.classes = store
        return store

    async def owned_class(request: Request, class_id: str) -> tuple[ClassStore, ClassGroup]:
        store = class_store(request)
        group = await store.get_class(class_id)
        if group is None or group.teacher_sub != teacher(request):
            raise HTTPException(404, {"message": f"no class {class_id}"})  # never "not yours"
        return store, group

    async def class_view(store: ClassStore, group: ClassGroup) -> dict[str, Any]:
        """The teacher's view: the one place a roster name leaves the store."""
        names = await store.names(group.class_id)
        return {
            **group.model_dump(mode="json", exclude={"teacher_sub"}),
            "learners": [
                {"code": a.code, "name": names.get(a.code, ""), "state": a.state}
                for a in await store.accounts(group.class_id)
            ],
        }

    @app.post("/classes", status_code=201)
    async def create_class(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        label: Annotated[str, Form()],
        pack_id: Annotated[str, Form()],
        roster: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        """learner-section.md §6.2: a class from a roster CSV (one column, learner)."""
        if pack_id not in pack_files():
            raise unprocessable("pack_id", f"no pack {pack_id!r}")
        if not label.strip() or len(label.strip()) > 60:
            raise unprocessable("label", "give the class a name of up to 60 characters")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roster.csv"
            path.write_bytes(await roster.read())
            try:
                names = load_class_list(path)
            except PARSE_ERRORS as error:
                raise unprocessable("roster", _sentence(error)) from error
        store = class_store(request)
        class_id = f"class-{uuid.uuid4().hex[:10]}"
        group = ClassGroup(
            class_id=class_id, teacher_sub=teacher(request), pack_id=pack_id, label=label.strip()
        )
        accounts, roster_names = make_accounts(class_id, names)
        await store.add_class(group, accounts, roster_names)
        return await class_view(store, group)

    @app.get("/classes")
    async def list_classes(request: Request) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        groups = await class_store(request).classes_of(teacher(request))
        return [g.model_dump(mode="json", exclude={"teacher_sub"}) for g in groups]

    @app.get("/classes/{class_id}")
    async def get_class(request: Request, class_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        store, group = await owned_class(request, class_id)
        return await class_view(store, group)

    @app.post("/classes/{class_id}/consent")
    async def consent(request: Request, class_id: str, body: ConsentBody) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Record that the school holds each listed learner's signed parental consent form
        (POPIA s35(1)(a)); only then can the learner's code be activated."""
        store, group = await owned_class(request, class_id)
        before = await store.accounts(class_id)
        try:
            updated = record_consent(before, body.codes, teacher(request), datetime.now(UTC))
        except ValueError as error:
            raise unprocessable("codes", str(error)) from error
        # Only the accounts this request moved: a whole-class save from this snapshot would undo
        # a consent another request recorded meanwhile.
        await store.save_accounts([a for a, b in zip(updated, before, strict=True) if a != b])
        return await class_view(store, group)

    # ------------------------------------------------------------------ practice tests

    practice_tasks: set[asyncio.Task[None]] = set()

    def practice_store(request: Request) -> PracticeStore:
        store = getattr(request.app.state, "practice", None)
        if store is None:
            store = practice_store_from_environment(runs(request).planner.data_dir)
            request.app.state.practice = store
        return store

    async def owned_test(request: Request, test_id: str) -> PracticeRecord:
        record = await practice_store(request).get(test_id)
        if record is None:
            raise HTTPException(404, {"message": f"no practice test {test_id}"})
        await owned_class(request, record.class_id)  # 404 unless it is this teacher's class
        return record

    @app.post("/classes/{class_id}/practice-tests", status_code=202)
    async def set_practice(  # pyright: ignore[reportUnusedFunction]
        request: Request, class_id: str, body: PracticeRequest
    ) -> dict[str, str]:
        """learner-section.md §4.1: the Setter sets a test for one ATP week; code checks it and the
        Setter sits it blind, in the background. Poll GET /practice-tests/{test_id}."""
        _store, group = await owned_class(request, class_id)
        try:
            weeks = {w.week: w for w in atp_weeks_for(group.pack_id, packs_dir)}
        except ValueError as error:
            raise unprocessable("atp_week", str(error)) from error
        week = weeks.get(body.atp_week)
        if week is None:
            raise unprocessable(
                "atp_week", f"week {body.atp_week} is not in the ATP (1-{max(weeks)})"
            )
        pack = load_pack(pack_files()[group.pack_id])
        record = PracticeRecord(
            test_id=f"practice-{uuid.uuid4().hex[:10]}",
            class_id=class_id,
            atp_week=week,
            marks=body.marks,
            state="drafting",
        )
        store = practice_store(request)
        await store.save(record)
        planner = runs(request).planner
        provider = _text_provider(planner)

        async def work() -> None:
            try:
                done = await set_and_check(record, pack, provider, planner.knowledge)
            except Exception as error:  # never leave a teacher polling a test that stopped
                log.exception("practice test %s failed", record.test_id)
                done = record.model_copy(
                    update={"state": "refused", "message": f"setting stopped: {error}"}
                )
            try:
                await store.save(done)
            except ClassGoneError:
                log.info("practice test %s dropped: its class was closed", record.test_id)

        task = asyncio.create_task(work())
        practice_tasks.add(task)
        task.add_done_callback(practice_tasks.discard)
        return {"test_id": record.test_id, "state": record.state}

    @app.get("/classes/{class_id}/practice-tests")
    async def class_practice(request: Request, class_id: str) -> list[dict[str, object]]:  # pyright: ignore[reportUnusedFunction]
        await owned_class(request, class_id)
        return [record_json(r) for r in await practice_store(request).of_class(class_id)]

    @app.get("/practice-tests/{test_id}")
    async def practice_test(request: Request, test_id: str) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        return record_json(await owned_test(request, test_id))

    @app.post("/practice-tests/{test_id}/{action}")
    async def practice_action(request: Request, test_id: str, action: str) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        """approve (the teacher's one approval, §1), open, close."""
        record = await owned_test(request, test_id)
        try:
            if action == "approve":
                record = approve(record, teacher(request), datetime.now(UTC))
            elif action == "open":
                record = move(record, "open")
            elif action == "close":
                record = move(record, "closed")
            else:
                raise HTTPException(404, {"message": f"no action {action}"})
        except ValueError as error:
            raise conflict(str(error)) from error
        await practice_store(request).save(record)
        return record_json(record)

    # ------------------------------------------------------------------ learners

    sign_in_on = verifier is not None

    def attempt_store(request: Request) -> AttemptStore:
        store = getattr(request.app.state, "attempts", None)
        if store is None:
            store = attempt_store_from_environment(runs(request).planner.data_dir)
            request.app.state.attempts = store
        return store

    async def learner(request: Request) -> LearnerAccount:
        """learner-section.md §6.1. On the laptop (sign-in off) the code comes from
        X-Learner-Code (Katlego, 2026-11-11); with sign-in on, the header is ignored and learners
        need the learner pool."""
        if sign_in_on:
            raise HTTPException(401, {"message": "learner sign-in is not set up here yet"})
        code = request.headers.get("x-learner-code", "").strip().upper()
        if not code:
            raise HTTPException(401, {"message": "give your learner code"})
        account = await class_store(request).account(code)
        if account is None:
            raise HTTPException(
                401, {"message": "that code is not known; check it with your teacher"}
            )
        if account.state not in ("consented", "active"):
            raise HTTPException(
                403, {"message": "this code is not active yet; your teacher activates it"}
            )
        return account

    async def open_test(request: Request, account: LearnerAccount, test_id: str) -> PracticeTest:
        record = await practice_store(request).get(test_id)
        if record is None or record.class_id != account.class_id or record.test is None:
            raise HTTPException(404, {"message": f"no practice test {test_id}"})
        if record.state != "open":
            raise conflict("this practice test is not open")
        return record.test

    @app.get("/learn/tests")
    async def learner_tests(request: Request) -> list[dict[str, object]]:  # pyright: ignore[reportUnusedFunction]
        account = await learner(request)
        out: list[dict[str, object]] = []
        for record in await practice_store(request).of_class(account.class_id):
            if record.state == "open" and record.test is not None:
                memo = record.test.memo
                out.append(
                    {
                        "test_id": record.test_id,
                        "title": test_title(record.test),
                        "questions": len(memo.items),
                        "max_total": memo.total_marks,
                    }
                )
        return out

    @app.get("/learn/tests/{test_id}")
    async def learner_test(request: Request, test_id: str) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        """The questions only: never the memo's answers before (or after) an attempt."""
        test = await open_test(request, await learner(request), test_id)
        return {
            "test_id": test.test_id,
            "title": test_title(test),
            "questions": [
                {"question_id": i.question_id, "question": i.question, "max_marks": i.max_marks}
                for i in test.memo.items
            ],
        }

    @app.post("/learn/attempts", response_model=None)
    async def learner_attempt(  # pyright: ignore[reportUnusedFunction]
        request: Request, body: AttemptBody
    ) -> dict[str, object] | JSONResponse:
        """§4.2: marked by the same Marker and rules; 200 with exactly §6.3, or 202 when the
        Marker could not produce checked marks (the teacher sees it)."""
        account = await learner(request)
        test = await open_test(request, account, body.test_id)
        known = {i.question_id for i in test.memo.items}
        unknown = [a.question_id for a in body.answers if a.question_id not in known]
        if unknown:
            raise unprocessable("answers", f"not questions on this test: {', '.join(unknown)}")
        # A learner may write a classmate's name: the Marker never sees one (grounding).
        by_name = {
            name: code
            for code, name in (await class_store(request).names(account.class_id)).items()
        }
        answers = [
            sweep_names(Answer(question_id=a.question_id, text=a.text), by_name)[0]
            for a in body.answers
        ]
        group = await class_store(request).get_class(account.class_id)
        if group is None:
            raise HTTPException(404, {"message": "this class is closed"})
        pack = load_pack(pack_files()[group.pack_id])
        planner = runs(request).planner
        provider = _text_provider(planner)
        attempt, review = await mark_attempt(
            test, new_attempt_id(), account.code, answers, pack, provider
        )
        try:
            await attempt_store(request).save(account.class_id, attempt)
            if review is not None:
                await attempt_store(request).save_review(account.class_id, review)
        except ClassGoneError as error:
            raise HTTPException(404, {"message": "this class is closed"}) from error
        if attempt.result is None:
            return JSONResponse(
                {
                    "attempt_id": attempt.attempt_id,
                    "state": attempt.state,
                    "message": "This attempt could not be marked yet; your teacher will see it.",
                },
                status_code=202,
            )
        return attempt.result.model_dump(mode="json")

    @app.get("/learn/attempts")
    async def learner_attempts(request: Request) -> list[dict[str, object]]:  # pyright: ignore[reportUnusedFunction]
        account = await learner(request)
        mine = await attempt_store(request).of_learner(account.class_id, account.code)
        return [
            {
                "attempt_id": a.attempt_id,
                "test_id": a.test_id,
                "state": a.state,
                "result": a.result.model_dump(mode="json") if a.result else None,
            }
            for a in mine
        ]

    @app.get("/practice-tests/{test_id}/attempts/{attempt_id}/review")
    async def attempt_review(request: Request, test_id: str, attempt_id: str) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        """Teacher-only: the Marker's passes and failed checks for an attempt it could not mark."""
        record = await owned_test(request, test_id)
        review = await attempt_store(request).review(record.class_id, attempt_id)
        if review is None:
            raise HTTPException(404, {"message": f"no review for attempt {attempt_id}"})
        return review.model_dump(mode="json")

    @app.get("/practice-tests/{test_id}/progress")
    async def practice_progress(request: Request, test_id: str) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        record = await owned_test(request, test_id)
        if record.test is None:
            raise conflict("this practice test was never set")
        store = class_store(request)
        attempts = await attempt_store(request).of_test(record.class_id, test_id)
        return progress(record.test, attempts, await store.names(record.class_id))

    @app.delete("/classes/{class_id}", status_code=204)
    async def close_class(request: Request, class_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        """learner-section.md §7: closing a class deletes its learner codes, consent records
        and roster names at once. Cognito accounts go too once the learner pool exists."""
        store, _group = await owned_class(request, class_id)
        await store.delete_class(class_id)
        return Response(status_code=204)

    @app.post("/memo-drafts", status_code=202)
    async def start_memo_draft(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        memo: Annotated[UploadFile, File()],
        pack_id: Annotated[str, Form()],
    ) -> dict[str, str]:
        """intake.md §12.3: read a typed memo PDF or .docx, then draft its rows in the background.

        The draft is polled at GET /memo-drafts/{draft_id}."""
        pack_path = pack_files().get(pack_id)
        if pack_path is None:
            raise unprocessable("pack_id", f"no pack {pack_id!r}")
        pack = load_pack(pack_path)
        data = await memo.read(MAX_MEMO_BYTES + 1)
        name = memo.filename or "memo"
        try:
            text = await asyncio.to_thread(memo_text, data, name)
        except ValueError as error:
            raise unprocessable("memo", str(error)) from error
        if len(text) > MEMO_DRAFT_LIMIT:
            raise unprocessable("memo", f"{name}: {memo_too_long(len(text))}")
        planner = runs(request).planner
        provider = _text_provider(planner)
        levels = tuple(pack.cognitive.levels) if pack.cognitive else ()
        draft = drafts.start(text, name, levels, provider)
        return {"draft_id": draft.draft_id, "state": draft.state}

    @app.get("/memo-drafts/{draft_id}")
    async def memo_draft(draft_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        draft = drafts.get(draft_id)
        if draft is None:
            raise HTTPException(404, {"message": "no such memo draft (drafts last two hours)"})
        return draft.view()

    @app.post("/assessments", status_code=201)
    async def create(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        goal: Annotated[str, Form()],
        memo: Annotated[UploadFile, File()],
        pack_id: Annotated[str, Form()],
        poa_task: Annotated[str, Form()],
        budget_zar: Annotated[str, Form()],
        scripts: Annotated[UploadFile | None, File()] = None,
        class_list: Annotated[UploadFile | None, File()] = None,
        atp: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, str]:
        """Typed scripts (scripts.csv) start the run now; a class list starts pages mode
        (console-ui.md §11.5): sheets are printed first and the run starts at /pages/ready."""
        if (scripts is None) == (class_list is None):
            raise HTTPException(
                422,
                {
                    "errors": [
                        {
                            "field": "scripts",
                            "message": "give either the typed scripts or a class list, not both",
                        }
                    ]
                },
            )
        r = runs(request)
        errors: list[dict[str, str]] = []
        files = pack_files()
        pack_path = files.get(pack_id)
        if pack_path is None:
            errors.append({"field": "pack_id", "message": f"no pack {pack_id!r}"})
        else:
            try:
                find_task(load_pack(pack_path), poa_task)
            except ValueError as error:
                errors.append({"field": "poa_task", "message": str(error)})
        budget = rand(budget_zar)
        if budget is None:
            budget = Decimal(0)
            errors.append(
                {"field": "budget_zar", "message": "a positive amount in rand, e.g. 40.00"}
            )
        slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:32] or "assessment"
        assessment_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        inputs = r.planner.data_dir / assessment_id / "inputs"
        inputs.mkdir(parents=True)
        saved: dict[str, Path] = {}
        # A memo confirmed from a draft arrives as JSON (intake.md §12.4); a typed one as TOML.
        memo_json = (memo.filename or "").lower().endswith(".json")
        for field, upload, name in (
            ("memo", memo, "memo.json" if memo_json else "memo.toml"),
            ("scripts", scripts, "scripts.csv"),
            ("class_list", class_list, "class-list.csv"),
            ("atp", atp, "atp-weeks.toml"),
        ):
            if upload is not None:
                saved[field] = inputs / name
                saved[field].write_bytes(await upload.read())
        parsed_memo: Memo | None = None
        parsed: tuple[tuple[Script, ...], dict[str, str]] | None = None
        weeks: tuple[AtpWeek, ...] = ()
        try:
            parsed_memo = (
                Memo.model_validate_json(saved["memo"].read_bytes())
                if memo_json
                else load_memo(saved["memo"])
            )
        except PARSE_ERRORS as error:
            errors.append({"field": "memo", "message": _sentence(error)})
        roster: tuple[str, ...] = ()
        if "scripts" in saved:
            try:
                parsed = load_scripts(saved["scripts"])
                if not parsed[0]:
                    parsed = None
                    errors.append(
                        {"field": "scripts", "message": "the file holds no learners' answers"}
                    )
            except PARSE_ERRORS as error:
                errors.append({"field": "scripts", "message": _sentence(error)})
        else:
            try:
                roster = load_class_list(saved["class_list"])
                parsed = ((), pseudonymise(roster))
            except PARSE_ERRORS as error:
                errors.append({"field": "class_list", "message": _sentence(error)})
        if "atp" in saved:
            try:
                weeks = load_atp_weeks(saved["atp"])
            except PARSE_ERRORS as error:
                errors.append({"field": "atp", "message": _sentence(error)})
        if (
            parsed_memo is not None
            and pack_path is not None
            and not any(e["field"] == "poa_task" for e in errors)
        ):
            try:
                check_memo_fits(parsed_memo, find_task(load_pack(pack_path), poa_task))
            except ValueError as error:
                errors.append({"field": "poa_task", "message": str(error)})
        if errors or parsed_memo is None or parsed is None or pack_path is None:
            shutil.rmtree(inputs.parent, ignore_errors=True)
            raise HTTPException(422, {"errors": errors})
        loaded, by_name = parsed
        if roster:
            return pages_mode(
                r,
                assessment_id,
                goal=goal,
                memo=parsed_memo,
                by_name=by_name,
                roster=roster,
                pack_path=pack_path,
                poa_task=poa_task,
                budget=budget,
                weeks=weeks,
            )
        initial = new_run(
            r.planner,
            assessment_id=assessment_id,
            goal=goal,
            memo=parsed_memo,
            scripts=loaded,
            names={pseudonym: name for name, pseudonym in by_name.items()},
            pack_path=pack_path,
            poa_task=poa_task,
            budget_zar=budget,
            atp_weeks=weeks,
        )
        r.launch(assessment_id, r.planner.start(initial))
        return {"assessment_id": assessment_id, "state": "draft"}

    def pages_mode(
        r: Runs,
        assessment_id: str,
        *,
        goal: str,
        memo: Memo,
        by_name: dict[str, str],
        roster: tuple[str, ...],
        pack_path: Path,
        poa_task: str,
        budget: Decimal,
        weeks: tuple[AtpWeek, ...],
    ) -> dict[str, str]:
        """Print the sheets now; keep the run's first state until the pages are uploaded."""
        inputs = r.planner.data_dir / assessment_id / "inputs"
        pdf, manifests = answer_sheets_pdf(
            assessment_id, memo, [(name, by_name[name]) for name in roster]
        )
        (inputs / "answer-sheets.pdf").write_bytes(pdf)
        (inputs / "manifests.json").write_text(
            json.dumps([m.model_dump(mode="json") for m in manifests])
        )
        (inputs / "pages").mkdir()
        initial = new_run(
            r.planner,
            assessment_id=assessment_id,
            goal=goal,
            memo=memo,
            scripts=(),
            names={pseudonym: name for name, pseudonym in by_name.items()},
            pack_path=pack_path,
            poa_task=poa_task,
            budget_zar=budget,
            atp_weeks=weeks,
            mode="pages",
        )
        (inputs / WAITING).write_text(json.dumps(initial))
        return {"assessment_id": assessment_id, "state": "draft", "mode": "pages"}

    def waiting(request: Request, assessment_id: str) -> tuple[Runs, Path]:
        """A pages-mode assessment whose run has not started: its inputs directory."""
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        inputs = r.planner.data_dir / assessment_id / "inputs"
        if not (inputs / WAITING).exists():
            raise conflict("this assessment is not waiting for pages")
        return r, inputs

    @app.get("/assessments/{assessment_id}/answer-sheets.pdf")
    async def answer_sheets(request: Request, assessment_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        path = r.planner.data_dir / assessment_id / "inputs" / "answer-sheets.pdf"
        if not path.exists():
            raise conflict("this assessment has typed scripts, not answer sheets")
        return Response(
            path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{assessment_id}-sheets.pdf"'},
        )

    @app.post("/assessments/{assessment_id}/uploads")
    async def upload_urls(
        request: Request, assessment_id: str, body: UploadRequest
    ) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        waiting(request, assessment_id)
        uploads: list[dict[str, Any]] = []
        for f in body.files:
            try:
                name = safe_name(f.name)
            except ValueError as error:
                raise unprocessable("files", str(error)) from error
            if f.content_type not in UPLOAD_TYPES:
                raise unprocessable("files", f"{f.name}: send a PNG or JPEG photo, or a PDF")
            if f.size > MAX_UPLOAD:
                raise unprocessable("files", f"{f.name}: over 25 MB")
            uploads.append(
                {
                    "name": name,
                    "method": "PUT",
                    "url": f"/assessments/{assessment_id}/uploads/{name}",
                    "headers": {"Content-Type": f.content_type},
                }
            )
        return {"uploads": uploads}

    @app.put("/assessments/{assessment_id}/uploads/{name}", status_code=204)
    async def upload_page(request: Request, assessment_id: str, name: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        waiting(request, assessment_id)  # refuse before reading the body; checked again below
        try:
            safe = safe_name(name)
        except ValueError as error:
            raise unprocessable("files", str(error)) from error
        if request.headers.get("content-type", "").split(";")[0] not in UPLOAD_TYPES:
            raise unprocessable("files", f"{name}: send a PNG or JPEG photo, or a PDF")
        data = await request.body()
        if len(data) > MAX_UPLOAD:
            raise unprocessable("files", f"{name}: over 25 MB")
        async with runs(request).lock(assessment_id):  # never while /pages/ready checks or starts
            _r, inputs = waiting(request, assessment_id)
            (inputs / "pages" / safe).write_bytes(data)
        return Response(status_code=204)

    @app.delete("/assessments/{assessment_id}/uploads/{name}", status_code=204)
    async def remove_upload(request: Request, assessment_id: str, name: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        """Drop one uploaded file (a refused photo, or a page uploaded twice) before reading."""
        try:
            safe = safe_name(name)
        except ValueError as error:
            raise unprocessable("files", str(error)) from error
        async with runs(request).lock(assessment_id):
            _r, inputs = waiting(request, assessment_id)
            path = inputs / "pages" / safe
            if not path.is_file():
                raise HTTPException(404, {"message": f"no uploaded file {name}"})
            path.unlink()
        return Response(status_code=204)

    @app.post("/assessments/{assessment_id}/pages/ready", status_code=202)
    async def pages_ready(  # pyright: ignore[reportUnusedFunction]
        request: Request, assessment_id: str, body: PagesReady | None = None
    ) -> dict[str, str]:
        """Start reading. Every page is first checked on its geometry alone (no model): a refused
        file or a printed page that did not arrive is a 409 that names them, and the assessment
        keeps waiting so the teacher can retake, remove or add files (intake.md §11.4). The
        teacher may then start anyway with accept_gaps; a missing page goes to the reading check.

        The assessment's lock is held from the check to the launch, and uploads and removals take
        it too: the run reads exactly the files that were checked, and a second request finds the
        assessment no longer waiting (409)."""
        waiting(request, assessment_id)
        async with runs(request).lock(assessment_id):
            return await _check_and_start(request, assessment_id, body)

    async def _check_and_start(
        request: Request, assessment_id: str, body: PagesReady | None
    ) -> dict[str, str]:
        r, inputs = waiting(request, assessment_id)
        if not any((inputs / "pages").iterdir()):
            raise conflict("upload the photographed or scanned pages first")
        manifests = load_manifests(inputs)

        def check() -> PageIntake:
            with tempfile.TemporaryDirectory() as crops:  # the run cuts its own crops
                return read_uploads(assessment_id, inputs / "pages", manifests, Path(crops))

        intake = await asyncio.to_thread(check)
        missing = missing_pages(intake, manifests)
        if not intake.crops or ((intake.refused or missing) and not (body and body.accept_gaps)):
            raise HTTPException(
                409,
                {
                    "message": (
                        "no page could be read: upload Mothusi answer sheets"
                        if not intake.crops
                        else "some pages were refused or have not arrived"
                    ),
                    "refused": intake.refused,
                    "missing": [{"script_id": k, "pages": v} for k, v in missing.items()],
                    "can_accept": bool(intake.crops),
                },
            )
        initial = cast(RunState, json.loads((inputs / WAITING).read_text()))
        (inputs / WAITING).unlink()
        r.launch(assessment_id, r.planner.start(initial))
        return {"state": "draft"}

    @app.get("/c/{assessment_id}/{pseudonym}/{key}")
    async def crop(  # pyright: ignore[reportUnusedFunction]
        request: Request, assessment_id: str, pseudonym: str, key: str, exp: int, sig: str
    ) -> Response:
        r = runs(request)
        invalid = HTTPException(404, {"message": "this link is not valid"})
        if not re.fullmatch(r"[0-9a-f]{24}", key):
            raise invalid
        if not check_crop_link(assessment_id, pseudonym, key, exp, sig):
            raise invalid
        try:
            path = r.planner.data_dir / assessment_id / "crops" / safe_name(pseudonym)
        except ValueError as error:
            raise invalid from error
        path = path / f"{key}.png"
        if not path.exists():
            raise invalid
        return Response(
            path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"}
        )

    @app.get("/assessments/{assessment_id}/reading")
    async def reading_items_now(request: Request, assessment_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """The pending reading check with freshly signed crop links (they last 15 minutes)."""
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        state = await r.planner.snapshot(assessment_id)
        question = state.get("question") or {}
        if state.get("state") != "reading_check" or question.get("topic") != "reading":
            raise conflict("no reading check is pending")
        items = [
            {
                **i,
                "crop_url": (
                    crop_link(assessment_id, i["script_id"], i["question_id"])
                    if i.get("crop_url")
                    else None
                ),
            }
            for i in question["items"]
        ]
        return {"items": items}

    # ------------------------------------------------------------------ the live log

    @app.get("/assessments/{assessment_id}/events")
    async def events(request: Request, assessment_id: str) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        try:
            after = int(request.headers.get("last-event-id", "-1"))
        except ValueError:
            after = -1
        path = r.planner.data_dir / assessment_id / "events.jsonl"

        async def stream() -> AsyncIterator[str]:
            async for event in follow(path, after, stop=request.is_disconnected):
                yield frame(event)
                if event.kind is EventKind.DELIVERED:
                    return  # nothing follows delivery; the console closes its EventSource here

        return StreamingResponse(
            with_pings(stream()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ teacher answers

    def answer(
        r: Runs, assessment_id: str, state: RunState, body: dict[str, Any]
    ) -> dict[str, str]:
        r.launch(assessment_id, r.planner.resume(assessment_id, body))
        return {"state": state["state"]}

    @app.post("/assessments/{assessment_id}/reading", status_code=202)
    async def reading_answer(
        request: Request, assessment_id: str, body: ReadingAnswers
    ) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        async with pending(request, assessment_id) as (r, state):
            question = state.get("question") or {}
            if state.get("state") != "reading_check" or question.get("topic") != "reading":
                raise conflict("no reading check is pending")
            answers = [a.model_dump(mode="json") for a in body.answers]
            try:
                scripts = tuple(Script.model_validate(x) for x in state["scripts"])
                apply_readings(scripts, question["items"], answers, {})
            except (ValueError, ValidationError) as error:
                raise unprocessable("answers", _sentence(error)) from error
            return answer(r, assessment_id, state, {"answers": answers})

    @app.post("/assessments/{assessment_id}/memo", status_code=202)
    async def memo_answer(request: Request, assessment_id: str, body: MemoAnswer) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        async with pending(request, assessment_id) as (r, state):
            question = state.get("question") or {}
            if state["state"] != "memo_check" or question.get("topic") != "memo":
                raise conflict("no memo question is pending")
            if body.question_id != question["question_id"]:
                raise conflict(f"the pending memo question is {question['question_id']}")
            if body.decision == "use_proposed" and not question.get("proposed_answer"):
                raise unprocessable(
                    "decision", "the Setter proposed no answer here: amend it or keep the memo"
                )
            if body.decision == "amend" and not (body.answer or "").strip():
                raise unprocessable("answer", "an amended answer is needed for amend")
            return answer(r, assessment_id, state, body.model_dump(exclude_none=True))

    @app.post("/assessments/{assessment_id}/budget", status_code=202)
    async def budget_answer(
        request: Request, assessment_id: str, body: BudgetAnswer
    ) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        async with pending(request, assessment_id) as (r, state):
            question = state.get("question") or {}
            if question.get("topic") != "budget":
                raise conflict("no budget question is pending")
            raised = rand(body.raise_to_zar)
            if raised is None:
                raise unprocessable("raise_to_zar", "a positive amount in rand, e.g. 60.00")
            if raised <= Decimal(state.get("spent_zar", "0")):
                raise unprocessable(
                    "raise_to_zar", f"must exceed the R{state.get('spent_zar', '0')} spent"
                )
            return answer(r, assessment_id, state, {"raise_to_zar": str(raised)})

    @app.post("/assessments/{assessment_id}/scripts/{script_id}", status_code=202)
    async def script_answer(  # pyright: ignore[reportUnusedFunction]
        request: Request, assessment_id: str, script_id: str, body: ScriptAnswer
    ) -> dict[str, str]:
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        async with r.lock(assessment_id):
            state = await r.planner.snapshot(assessment_id)
            question = state.get("question") or {}
            marking = state["state"] == "marking"
            open_script = (
                question.get("topic") == "script" and question.get("script_id") == script_id
            )
            if marking:
                if script_id not in state.get("pending", {}):
                    raise conflict(f"{script_id} is not pending a teacher decision")
                if await inbox.exists(r.planner.store, assessment_id, script_id):
                    raise conflict(f"{script_id} already has an answer waiting to be applied")
            else:
                if r.busy(assessment_id):
                    raise conflict("the run is working; wait for its question")
                if not open_script:
                    raise conflict(f"{script_id} is not pending a teacher decision")
            if body.decision == "use_attempt":
                attempts = state.get("pending", {})[script_id] if marking else question["attempts"]
                if body.attempt is None or not 1 <= body.attempt <= len(attempts):
                    raise unprocessable("attempt", f"choose an attempt from 1 to {len(attempts)}")
                memo = Memo.model_validate(state["memo"])
                if not attempt_is_complete(memo, attempts[body.attempt - 1]["marks"]):
                    raise unprocessable(
                        "attempt",
                        f"attempt {body.attempt} does not mark every memo question: set the marks",
                    )
            else:
                memo = Memo.model_validate(state["memo"])
                script = next(x for x in state["scripts"] if x["script_id"] == script_id)
                answered = {a["question_id"] for a in script["answers"] if a["text"].strip()}
                given = [m.question_id for m in body.marks or []]
                if sorted(given) != sorted(i.question_id for i in memo.items):
                    raise unprocessable("marks", "give every memo question exactly once")
                items = {i.question_id: i for i in memo.items}
                for m in body.marks or []:
                    if m.awarded > items[m.question_id].max_marks:
                        raise unprocessable(
                            "marks", f"{m.question_id}: over {items[m.question_id].max_marks}"
                        )
                    if m.awarded and m.question_id not in answered:
                        raise unprocessable(
                            "marks", f"{m.question_id}: no answer to give marks for"
                        )
            posted = body.model_dump(exclude_none=True)
            if marking:
                await inbox.put(r.planner.store, assessment_id, script_id, posted)
                if open_script and not r.busy(assessment_id):
                    return answer(r, assessment_id, state, posted)
                return {"state": "marking"}
            return answer(r, assessment_id, state, posted)

    @app.get("/assessments/{assessment_id}/bundle")
    async def bundle(request: Request, assessment_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        r = runs(request)
        if not r.known(assessment_id):
            raise HTTPException(404, {"message": f"no assessment {assessment_id}"})
        state = await r.planner.snapshot(assessment_id)
        if state.get("state") not in BUNDLE_STATES or "bundle" not in state:
            raise conflict("the bundle is ready once the run reaches awaiting_approval")
        return {**state["bundle"], "state": state["state"]}

    @app.post("/assessments/{assessment_id}/approval", status_code=202)
    async def approval(
        request: Request, assessment_id: str, body: ApprovalAnswer
    ) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        async with pending(request, assessment_id) as (r, state):
            if state["state"] != "awaiting_approval":
                raise conflict(f"approval is asked at awaiting_approval, not {state['state']}")
            if body.decision == "reject" and not (body.note or "").strip():
                raise unprocessable("note", "say what to change: a rejection needs a note")
            return answer(r, assessment_id, state, body.model_dump(exclude_none=True))

    return app


MAX_RAND = Decimal(1_000_000)


def rand(text: str) -> Decimal | None:
    """A finite, positive amount in rand (2 dp at most worth keeping), else None."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value > MAX_RAND:
        return None
    return value


def _sentence(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        return f"{where}: {first['msg']}" if where else str(first["msg"])
    return str(error).strip("'\"") or type(error).__name__


WAITING = "waiting-for-pages.json"
UPLOAD_TYPES = ("image/png", "image/jpeg", "application/pdf")
MAX_UPLOAD = 25 * 1024 * 1024


class UploadFileInfo(BaseModel):
    name: str
    content_type: str
    size: int = Field(ge=0)


def _no_fallback_event(primary: str, fallback: str, error: str) -> None:
    """A memo draft has no assessment and so no event log: a fallback is only logged here."""
    log.info("memo draft: %s failed (%s); asked %s", primary, error, fallback)


def _text_provider(planner: Planner) -> Provider:
    """The text role outside a run, holding the Planner's slots like every call in a run."""
    return Limited(
        Fallback(
            planner.roles.get("text", planner.primary),
            planner.fallback_roles.get("text", planner.fallback),
            _no_fallback_event,
        ),
        planner.slots,
    )


class AttemptAnswer(BaseModel):
    question_id: str = Field(min_length=1)
    text: str = Field(max_length=4000)


class AttemptBody(BaseModel):
    test_id: str = Field(min_length=1)
    answers: list[AttemptAnswer] = Field(min_length=1)


class PracticeRequest(BaseModel):
    atp_week: int = Field(ge=1, le=52)
    marks: int = Field(default=20, ge=10, le=30)


class ConsentBody(BaseModel):
    codes: list[str] = Field(min_length=1)


class PagesReady(BaseModel):
    """Start reading although some pages were refused or never arrived (the teacher's call)."""

    accept_gaps: bool = False


class UploadRequest(BaseModel):
    files: list[UploadFileInfo] = Field(min_length=1)


class ReadingAnswers(BaseModel):
    answers: list[ReadingAnswer] = Field(min_length=1)


NOTE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
}
NOT_FOUND_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Mothusi</title>"
    "<p>This link does not open a note. Ask your teacher for your printed note.</p>"
)
EXPIRED_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Mothusi</title>"
    "<p>This note's link has expired. Your printed note still has everything on it.</p>"
)


def _note_response(html: str, status: int) -> Response:
    return Response(html, status_code=status, media_type="text/html", headers=NOTE_HEADERS)


class MemoAnswer(BaseModel):
    decision: Literal["use_proposed", "keep_memo", "amend"]
    question_id: str
    answer: str | None = None


class BudgetAnswer(BaseModel):
    raise_to_zar: str


class SetMark(BaseModel):
    question_id: str
    awarded: int = Field(ge=0)


class ScriptAnswer(BaseModel):
    decision: Literal["use_attempt", "set_marks"]
    attempt: Literal[1, 2, 3] | None = None
    marks: list[SetMark] | None = None


class ApprovalAnswer(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = None


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ColleagueContext(BaseModel):
    pack_id: str | None = None
    subject: str | None = None
    assessment_id: str | None = None
    lesson: Lesson | None = None


class ColleagueMessageBody(MessageBody):
    context: ColleagueContext = Field(default_factory=ColleagueContext)


app = create_app()
