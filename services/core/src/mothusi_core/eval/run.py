"""Measure the marking graph and write immutable dated results (eval.md §4.1, §6)."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from mothusi_contracts import Contract, Event, EventKind, Memo
from mothusi_core.eval.metrics import agreement, run_cost, summarise_latency
from mothusi_core.eval.results import CallTiming, CostBasis, EvalResult, TrapCatch
from mothusi_core.eval.timed import Timed
from mothusi_core.graph.planner import Planner, attempt_is_complete, new_run
from mothusi_core.graph.state import RunState
from mothusi_core.knowledge import knowledge_from_environment
from mothusi_core.providers import ROOT, Role, from_environment
from mothusi_core.smoke import ASSESSMENT, FIXTURE, GOAL, PACK
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import Provider, Usage, load_pricing
from mothusi_llm.meter import Pricing, instance_price, price
from mothusi_rules import load_atp_weeks


class _Model(BaseModel):
    name: str


class _Tags(BaseModel):
    models: list[_Model] = Field(default_factory=list[_Model])


async def precheck(
    url: str, models: Sequence[str], *, client: httpx.AsyncClient | None = None
) -> None:
    """One tags request; absent models and unreachable hosts are pre-run failures."""
    if client is None:
        async with httpx.AsyncClient(timeout=10) as owned:
            await precheck(url, models, client=owned)
        return
    try:
        response = await client.get(f"{url.rstrip('/')}/api/tags")
        response.raise_for_status()
        tags = _Tags.model_validate_json(response.content)
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError(f"model host unreachable at {url}: {error}") from error
    available = {m.name if ":" in m.name else f"{m.name}:latest" for m in tags.models}
    missing = sorted({m for m in models if (m if ":" in m else f"{m}:latest") not in available})
    if missing:
        raise ValueError(f"models not pulled at {url}: {', '.join(missing)}")


# The Planner shares its text provider; output contracts identify the agent at dispatch.
_TEXT_ROLES = {
    "MarkerReply": "marker",
    "SetterReply": "setter",
    "AnalystReply": "analyst",
    "SupportReply": "support",
    "NoteReply": "writer",
    "CorrectionsReply": "writer",
    "ToneReply": "reviewer",
}


class _AgentTimed:
    def __init__(
        self, inner: Provider, role: Role, calls: list[CallTiming], pricing: Pricing
    ) -> None:
        self.name = inner.name
        self.inner = inner
        self.role = role
        self.calls = calls
        self.pricing = pricing

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        # A reply class this table does not know is timed under its own name, never a crash.
        named = output.__name__.removesuffix("Reply").lower()
        role = _TEXT_ROLES.get(output.__name__, named) if self.role == "text" else self.role
        configured: object = getattr(self.inner, "model", self.inner.name)
        timed = Timed(self.inner, role, str(configured), self.calls)
        reply, usage = await timed.complete(
            system, user, output, budget_tokens=budget_tokens, images=images
        )
        # The CLI price list is authoritative for paid calls as well as wall time.
        if self.inner.name not in {"replay", "ollama"}:
            usage = usage.model_copy(
                update={
                    "cost_zar": price(
                        self.pricing, usage.model, usage.input_tokens, usage.output_tokens
                    )
                }
            )
        return reply, usage


def teacher_answer(
    question: dict[str, Any], memo: Memo, memo_truth: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Accept planted memo corrections, or all proposals when fixture truth is absent."""
    if question["topic"] == "memo":
        question_id = question["question_id"]
        decision = (
            "use_proposed" if memo_truth is None or question_id in memo_truth else "keep_memo"
        )
        return {"decision": decision, "question_id": question_id}
    if question["topic"] == "budget":
        return {"raise_to_zar": str(Decimal(question["spent_zar"]) + Decimal("40"))}
    if question["topic"] == "script":
        attempts: list[dict[str, Any]] = question.get("attempts", [])
        for attempt in attempts:
            if attempt_is_complete(memo, attempt["marks"]):
                return {"decision": "use_attempt", "attempt": attempt["attempt"]}
        # As a teacher would: set the marks from the most complete attempt, a question it left
        # out scored 0 (2026-11-12: a script with no complete attempt halted the whole run).
        rows: list[list[dict[str, Any]]] = [
            cast(list[dict[str, Any]], a["marks"]) for a in attempts
        ]
        none: list[dict[str, Any]] = []
        best = max(rows, key=len, default=none)
        given = {str(m["question_id"]): int(m["awarded"]) for m in best}
        return {
            "decision": "set_marks",
            "marks": [
                {"question_id": i.question_id, "awarded": given.get(i.question_id, 0)}
                for i in memo.items
            ],
        }
    options: list[Any] = question.get("options", [])
    if options:
        first = options[0]
        if isinstance(first, dict):
            return dict(cast(dict[str, Any], first))
        return {"decision": str(first)}
    raise ValueError(f"no offered option for teacher question {question['topic']}")


def _evidence(*events: Event) -> str:
    return "; ".join(f"seq {e.seq}: {e.summary}" for e in events)


def traps(state: RunState, events: tuple[Event, ...]) -> tuple[TrapCatch, ...]:
    """The smoke's four checks, with deciding events and successful recovery required."""
    first_marker = next(
        (e.seq for e in events if e.kind is EventKind.BRIEF and e.payload.get("agent") == "marker"),
        None,
    )
    memo = next(
        (
            e
            for e in events
            if e.kind is EventKind.NEEDS_TEACHER and e.payload.get("question_id") == "2.3"
        ),
        None,
    )
    failed = next(
        (
            e
            for e in events
            if e.kind is EventKind.CHECK
            and e.payload.get("about") == "L-014"
            and e.payload.get("check") == "evidence"
            and not e.payload["passed"]
        ),
        None,
    )
    retried = next(
        (
            e
            for e in events
            if e.kind is EventKind.RESULT
            and e.payload.get("script_id") == "L-014"
            and str(e.payload.get("brief_id")).endswith("/L-014/marker/2")
            and e.payload.get("status") == "done"
        ),
        None,
    )
    disagreement = next(
        (
            e
            for e in events
            if e.kind is EventKind.DISAGREEMENT
            and e.payload.get("script_id") == "L-021"
            and e.payload.get("question_id") == "4"
        ),
        None,
    )
    rejection = next(
        (
            e
            for e in events
            if e.kind is EventKind.DECISION
            and str(e.payload.get("reason", "")).startswith("L-007:")
        ),
        None,
    )
    final_verdict = next(
        (v for v in state.get("verdicts", []) if v.get("pseudonym") == "L-007"), dict[str, Any]()
    )
    q4 = next(
        (
            m
            for m in state.get("marked", {}).get("L-021", {}).get("marks", [])
            if m["question_id"] == "4"
        ),
        dict[str, Any](),
    )
    sampled = "L-021" in state.get("moderated", {})
    return (
        TrapCatch(
            trap="memo 2.3",
            caught=memo is not None and first_marker is not None and memo.seq < first_marker,
            evidence=_evidence(memo) if memo else "memo not flagged",
        ),
        TrapCatch(
            trap="L-014 evidence",
            caught=failed is not None and retried is not None and failed.seq < retried.seq,
            evidence=_evidence(*(e for e in (failed, retried) if e is not None)),
        ),
        TrapCatch(
            trap="L-021 over-mark",
            caught=disagreement is not None
            and "L-021" in state.get("re_marked", [])
            and q4.get("awarded") == 2,
            evidence=_evidence(disagreement)
            if disagreement
            else ("not sampled" if not sampled else "no Q4 disagreement"),
        ),
        TrapCatch(
            trap="L-007 note",
            caught=rejection is not None and final_verdict.get("passed") is True,
            evidence=_evidence(rejection) if rejection else "note not rejected",
        ),
    )


def _minutes(seconds: float) -> Decimal:
    return (Decimal(str(seconds)) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PreRunError(RuntimeError):
    """Setup failed before any Planner execution could be measured."""


async def evaluate(
    fixture: Path,
    instance: str,
    pricing_path: Path,
    events_to: list[Event] | None = None,
) -> EvalResult:
    """Run all fixture scripts; failures after start remain measured results."""
    with ExitStack() as resources:
        try:
            pricing = load_pricing(pricing_path)
            host = instance_price(pricing, instance)
            if instance != "laptop" and pricing.stack is None:
                raise ValueError("GPU-time eval requires a sourced [stack] price")
            basis = CostBasis(
                instance=instance,
                instance_usd_per_hour=host.usd_per_hour if instance != "laptop" else Decimal(0),
                stack_usd_per_hour=pricing.stack.usd_per_hour
                if pricing.stack and instance != "laptop"
                else Decimal(0),
                zar_per_usd=pricing.rates.zar_per_usd,
                sources=(host.source, pricing.rates.source)
                + ((pricing.stack.source,) if pricing.stack and instance != "laptop" else ()),
            )
            if os.environ.get("MOTHUSI_PROVIDER") == "ollama":
                await precheck(
                    os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                    (
                        os.environ.get("MOTHUSI_OLLAMA_MODEL", "qwen3:14b"),
                        os.environ.get("MOTHUSI_OLLAMA_MODERATOR_MODEL", "phi4"),
                        os.environ.get("MOTHUSI_OLLAMA_READER_MODEL", "qwen3-vl:8b-instruct"),
                    ),
                )
            providers = from_environment()
            calls: list[CallTiming] = []
            roles: tuple[Role, ...] = ("text", "moderator", "reader")
            wrapped: dict[Role, Provider] = {
                r: _AgentTimed(providers.for_role(r), r, calls, pricing) for r in roles
            }
            fallback: dict[Role, Provider] = {
                r: _AgentTimed(p, r, calls, pricing) for r, p in providers.fallbacks.items()
            }
            memo = load_memo(fixture / "memo.toml")
            memo_truth: dict[str, str] | None = None
            truth_path = fixture / "memo-truth.toml"
            if truth_path.exists():
                with truth_path.open("rb") as stream:
                    memo_truth = {
                        error["question_id"]: error["correct_answer"]
                        for error in tomllib.load(stream)["errors"]
                    }
            scripts, by_name = load_scripts(fixture / "scripts.csv")
            names = {p: n for n, p in by_name.items() if p in {s.script_id for s in scripts}}
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.PIPE
            ).strip()
            approval: Decimal | None = None
            delivered: Decimal | None = None
            questions: list[str] = []
            tmp = resources.enter_context(tempfile.TemporaryDirectory(prefix="mothusi-eval-"))
            planner = Planner(
                primary=wrapped["text"],
                fallback=None,
                roles=wrapped,
                fallback_roles=fallback,
                priced=True,
                data_dir=Path(tmp),
                checkpointer=InMemorySaver(),
                knowledge=knowledge_from_environment(),  # retrieval as production runs it
            )
            state = new_run(
                planner,
                assessment_id=ASSESSMENT,
                goal=GOAL,
                memo=memo,
                scripts=scripts,
                names=names,
                pack_path=PACK,
                poa_task="Test",
                budget_zar=Decimal("40"),
                atp_weeks=load_atp_weeks(fixture / "atp-weeks.toml"),
            )
            started = time.perf_counter()
        except Exception as error:
            detail = " ".join(str(error).split())
            raise PreRunError(f"eval could not start ({type(error).__name__}): {detail}") from error
        try:
            state = await planner.start(state)
            resumes = 0
            while True:
                if state["state"] == "awaiting_approval" and approval is None:
                    approval = _minutes(time.perf_counter() - started)
                if state.get("halted"):
                    break
                if state["state"] == "delivered":
                    delivered = _minutes(time.perf_counter() - started)
                    break
                if resumes == 60:
                    state["halted"] = "exceeded 60 resumes"
                    break
                question = state.get("question")
                if question:
                    questions.append(f"{question['topic']}:{question.get('question_id', '')}")
                    answer = teacher_answer(
                        question,
                        Memo.model_validate(state["memo"])
                        if question["topic"] == "script"
                        else memo,
                        memo_truth,
                    )
                elif state["state"] == "awaiting_approval":
                    answer = {"decision": "approve", "by": "the teacher"}
                else:
                    state["halted"] = f"run stopped in {state['state']}"
                    break
                state = await planner.resume(ASSESSMENT, answer)
                resumes += 1
        except Exception as error:
            # Recover the latest checkpoint where available, retaining partial metrics.
            try:
                snapshot = await planner.snapshot(ASSESSMENT)
                if snapshot:
                    state = snapshot
            except Exception:
                snapshot = None
            state["halted"] = f"{type(error).__name__}: {error}"
        events = planner.sink(ASSESSMENT).read()
        if events_to is not None:  # the run's folder is deleted on exit; the caller keeps the log
            events_to.extend(events)
    compared = state.get("questions_compared", 0)
    differences = len(state.get("disagreements", []))
    cost = (
        run_cost(basis, delivered, Decimal(state.get("spent_zar", "0")))
        if delivered is not None
        else None
    )
    photos = fixture.with_name(f"{fixture.name}-photos")
    return EvalResult(
        run_date=datetime.now(UTC).date(),
        git_commit=commit,
        fixture=str(fixture),
        scripts=len(scripts),
        instance=instance,
        models={c.role: c.model for c in calls},
        minutes_to_approval=approval,
        minutes_to_delivered=delivered,
        halted=state.get("halted"),
        questions_compared=compared,
        disagreements=differences,
        agreement_rate=agreement(compared, differences),
        traps=traps(state, events),
        teacher_questions=tuple(questions),
        latency=summarise_latency(calls, {"marker": 30}),
        cost_zar_set=cost,
        cost_zar_script=(cost / len(scripts)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if cost is not None
        else None,
        cost_basis=basis,
        reader=None,
        reader_skipped=f"no photographed set at {photos}"
        if not photos.exists()
        else "photographed reader pass is outside this runner-only task",
    )


def write_result(result: EvalResult, out: Path) -> Path:
    """Exclusive creation also protects results from concurrent eval processes."""
    out.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        path = out / f"results-{result.run_date}{'' if suffix == 1 else f'-{suffix}'}.json"
        try:
            with path.open("x") as stream:
                stream.write(result.model_dump_json(indent=2) + "\n")
            return path
        except FileExistsError:
            suffix += 1


def write_events(events: Sequence[Event], result_path: Path) -> Path:
    """The run's whole event log beside its result (results-X.json -> events-X.jsonl), so every
    disagreement, check and teacher question can be read after the run."""
    path = result_path.with_name(result_path.name.replace("results-", "events-", 1)).with_suffix(
        ".jsonl"
    )
    with path.open("x") as stream:
        for event in events:
            stream.write(event.model_dump_json() + "\n")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pricing", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--out", type=Path, default=ROOT / "services/core/eval")
    args = parser.parse_args(argv)
    try:
        events: list[Event] = []
        result = asyncio.run(evaluate(args.fixture, args.instance, args.pricing, events))
    except PreRunError as error:
        print(error, file=sys.stderr)
        return 2
    path = write_result(result, args.out)
    log = write_events(events, path)
    print(f"events: {log}")
    if result.halted:
        print(f"halted: {result.halted}; result: {path}", file=sys.stderr)
    for latency in result.latency:
        if latency.over_limit:
            print(
                f"Open question: {latency.role} on {latency.model}: "
                f"{latency.over_limit} calls above {latency.limit_s}s; max {latency.max_s}s",
                file=sys.stderr,
            )
    print(f"agreement {result.agreement_rate} on {result.questions_compared} compared questions")
    print(f"minutes {result.minutes_to_approval} ({result.minutes_to_delivered} to delivered)")
    print(
        f"rand {result.cost_zar_set} per set, "
        f"{result.cost_zar_script} per script ({result.instance})"
    )
    if result.reader:
        print(
            f"reader {result.reader.line_exact_rate} lines exact, CER {result.reader.cer}, "
            f"{result.reader.wrong_not_flagged} wrong unflagged"
        )
    else:
        print(f"reader skipped: {result.reader_skipped}")
    return 1 if result.halted else 0


if __name__ == "__main__":
    raise SystemExit(main())
