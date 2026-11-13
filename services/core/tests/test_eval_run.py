"""Measured replay results and model-host failures, without network access."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from mothusi_contracts import Contract, Event, EventKind, Memo
from mothusi_core.eval import run
from mothusi_core.eval.results import CallTiming, EvalResult
from mothusi_core.eval.timed import Timed
from mothusi_core.graph.planner import Planner
from mothusi_core.smoke import PLANTED
from mothusi_intake import load_scripts
from mothusi_llm import ProviderError, Usage

PRICING = Path(__file__).parent / "fixtures/eval-pricing.toml"


class Reply(Contract):
    text: str


class FakeProvider:
    name = "fake"

    def __init__(self, fails: bool = False) -> None:
        self.fails = fails
        self.options: tuple[int | None, Sequence[bytes]] | None = None

    async def complete[T: Contract](
        self,
        system: str,
        user: str,
        output: type[T],
        *,
        budget_tokens: int | None = None,
        images: Sequence[bytes] = (),
    ) -> tuple[T, Usage]:
        self.options = budget_tokens, images
        if self.fails:
            raise ProviderError("failed", retryable=False)
        return output.model_validate({"text": "read"}), Usage(
            model="actual-model", input_tokens=12, output_tokens=3, cost_zar=Decimal("0.25")
        )


@pytest.mark.parametrize("fails", [False, True])
def test_timed_records_success_and_failure(monkeypatch: pytest.MonkeyPatch, fails: bool) -> None:
    ticks = iter([10.0, 41.0])
    monkeypatch.setattr("mothusi_core.eval.timed.time.perf_counter", lambda: next(ticks))
    calls: list[CallTiming] = []
    inner = FakeProvider(fails)
    timed = Timed(inner, "marker", "configured-model", calls)
    if fails:
        with pytest.raises(ProviderError, match="failed"):
            asyncio.run(timed.complete("s", "u", Reply, budget_tokens=80, images=(b"png",)))
    else:
        reply, usage = asyncio.run(
            timed.complete("s", "u", Reply, budget_tokens=80, images=(b"png",))
        )
        assert reply.text == "read"
        assert usage.cost_zar == Decimal("0.25")
    assert inner.options == (80, (b"png",))
    assert timed.name == "fake"
    assert len(calls) == 1
    assert calls[0].seconds == 31
    assert calls[0].role == "marker"
    assert calls[0].model == ("configured-model" if fails else "actual-model")
    assert calls[0].input_tokens == (0 if fails else 12)
    assert calls[0].output_tokens == (0 if fails else 3)


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")
    monkeypatch.delenv("MOTHUSI_REPLAY", raising=False)

    def planted(path: Path) -> Any:
        scripts, names = load_scripts(path)
        return tuple(s for s in scripts if s.script_id in PLANTED), names

    monkeypatch.setattr(run, "load_scripts", planted)

    async def no_precheck(*args: Any, **kwargs: Any) -> None:
        pytest.fail("replay must not contact Ollama")

    monkeypatch.setattr(run, "precheck", no_precheck)


def args(out: Path, instance: str = "g4dn.xlarge") -> list[str]:
    return ["--pricing", str(PRICING), "--instance", instance, "--out", str(out)]


def test_replay_result_and_summary(
    replay: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run.main(args(tmp_path)) == 0
    files = list(tmp_path.glob("results-*.json"))
    assert len(files) == 1
    result = EvalResult.model_validate_json(files[0].read_text())
    assert result.scripts == 6
    assert result.halted is None
    assert result.minutes_to_approval is not None
    assert result.minutes_to_delivered is not None
    assert result.minutes_to_delivered >= result.minutes_to_approval
    assert len(result.traps) == 4 and all(t.caught for t in result.traps)
    assert all("seq " in t.evidence for t in result.traps)
    assert result.questions_compared == 54
    assert result.disagreements == 1
    assert result.agreement_rate == Decimal("0.9815")
    assert result.teacher_questions == ("memo:2.3",)
    assert result.reader is None and "no photographed set" in result.reader_skipped
    assert result.cost_basis.instance_usd_per_hour == 1
    assert result.cost_basis.stack_usd_per_hour == Decimal("0.5")
    assert result.cost_zar_set is not None and result.cost_zar_script is not None
    assert {x.role for x in result.latency} >= {"marker", "moderator", "setter", "reviewer"}
    assert all(x.model == "scripted" for x in result.latency)
    assert next(x for x in result.latency if x.role == "marker").limit_s == 30
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 5
    # The run's whole event log is kept beside its result, so disagreements can be read after.
    log = files[0].with_name(files[0].name.replace("results-", "events-")).with_suffix(".jsonl")
    assert lines[0] == f"events: {log}"
    kept = [Event.model_validate_json(line) for line in log.read_text().splitlines()]
    assert kept and kept[-1].kind is EventKind.DELIVERED
    assert any(e.kind is EventKind.DISAGREEMENT for e in kept)
    lines = lines[1:]
    assert lines[0] == "agreement 0.9815 on 54 compared questions"
    assert lines[1].startswith("minutes ") and "to delivered" in lines[1]
    assert lines[2].endswith("per script (g4dn.xlarge)")
    assert lines[3].startswith("reader skipped: no photographed set")
    original = files[0].read_bytes()
    assert run.main(args(tmp_path)) == 0
    assert len(list(tmp_path.glob("results-*.json"))) == 2
    assert files[0].read_bytes() == original


@pytest.mark.parametrize("loop", [False, True])
def test_halted_run_is_written(
    replay: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loop: bool
) -> None:
    resumes = 0

    async def start(self: Any, initial: Any) -> Any:
        return {
            **initial,
            "halted": None if loop else "model refused",
            "question": {"topic": "memo", "question_id": "2.3"} if loop else None,
        }

    async def resume(self: Any, aid: str, answer: Any) -> Any:
        nonlocal resumes
        resumes += 1
        return {
            "assessment_id": aid,
            "state": "memo_check",
            "question": {"topic": "memo", "question_id": "2.3"},
        }

    monkeypatch.setattr(Planner, "start", start)
    monkeypatch.setattr(Planner, "resume", resume)
    assert run.main(args(tmp_path)) == 1
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.halted == ("exceeded 60 resumes" if loop else "model refused")
    assert result.minutes_to_delivered is None
    assert result.cost_zar_set is None
    assert resumes == (60 if loop else 0)


@pytest.mark.parametrize("failure", ["unreachable", "missing"])
def test_precheck_exit_two_no_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        if failure == "unreachable":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]})

    original = httpx.AsyncClient

    def client(**kw: Any) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(transport), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert run.main(args(tmp_path)) == 2
    assert not list(tmp_path.iterdir())
    error = capsys.readouterr().err
    assert (
        "model host unreachable at http://test-host:11434" if failure == "unreachable" else "phi4"
    ) in error


def test_precheck_accepts_default_latest_tag() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "models": [
                    {"name": "phi4:latest"},
                    {"name": "qwen3:14b"},
                    {"name": "qwen3-vl:8b-instruct"},
                ]
            },
        )
    )

    async def check() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await run.precheck(
                "http://test", ("phi4", "qwen3:14b", "qwen3-vl:8b-instruct"), client=client
            )

    asyncio.run(check())


def test_first_offered_answer(amended_memo: Memo) -> None:
    assert run.teacher_answer(
        {"topic": "extra", "options": [{"decision": "first"}, {"decision": "second"}]},
        amended_memo,
    ) == {"decision": "first"}
    with pytest.raises(ValueError, match="no offered option"):
        run.teacher_answer({"topic": "extra", "options": []}, amended_memo)


@pytest.mark.parametrize("instance, expected", [("g4dn.xlarge", "6.00"), ("laptop", "0.00")])
def test_wall_cost_and_approval_clock(
    replay: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, instance: str, expected: str
) -> None:
    ticks = iter([0.0, 600.0, 720.0])
    monkeypatch.setattr("mothusi_core.eval.run.time.perf_counter", lambda: next(ticks))

    async def start(self: Any, initial: Any) -> Any:
        return {**initial, "state": "awaiting_approval"}

    async def resume(self: Any, aid: str, answer: Any) -> Any:
        assert answer == {"decision": "approve", "by": "the teacher"}
        return {"assessment_id": aid, "state": "delivered"}

    monkeypatch.setattr(Planner, "start", start)
    monkeypatch.setattr(Planner, "resume", resume)
    assert run.main(args(tmp_path, instance)) == 0
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.minutes_to_approval == Decimal("10.00")
    assert result.minutes_to_delivered == Decimal("12.00")
    assert result.cost_zar_set == Decimal(expected)
    assert result.cost_zar_script == Decimal(expected) / 6
    assert result.agreement_rate is None
    assert not any(t.caught for t in result.traps)
    assert result.traps[2].evidence == "not sampled"


def test_default_loads_all_32_scripts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")

    async def start(self: Any, initial: Any) -> Any:
        assert len(initial["scripts"]) == 32
        assert len(initial["names"]) == 32
        return {**initial, "halted": "test ends before model calls"}

    monkeypatch.setattr(Planner, "start", start)
    assert run.main(args(tmp_path)) == 1
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.scripts == 32


def test_exception_after_start_writes_failure(
    replay: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def start(self: Any, initial: Any) -> Any:
        raise RuntimeError("graph failure")

    monkeypatch.setattr(Planner, "start", start)
    assert run.main(args(tmp_path)) == 1
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.halted == "RuntimeError: graph failure"
    assert result.minutes_to_delivered is None


def test_supplied_pricing_prices_paid_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mothusi_core.providers import ProviderSet

    inner = FakeProvider()
    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")
    monkeypatch.setattr(
        run, "from_environment", lambda: ProviderSet(inner, inner, inner, {}, False)
    )

    async def start(self: Planner, initial: Any) -> Any:
        assert self.priced
        _, usage = await self.provider(initial["assessment_id"], "moderator").complete(
            "s", "u", Reply
        )
        assert usage.cost_zar == Decimal("0.0006")
        return {**initial, "halted": "test ends after metered paid call"}

    monkeypatch.setattr(Planner, "start", start)
    assert run.main(args(tmp_path)) == 1


@pytest.mark.parametrize("complete", [True, False])
def test_script_question_uses_a_complete_attempt_or_sets_the_marks(
    replay: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, complete: bool
) -> None:
    async def start(self: Planner, initial: Any) -> Any:
        marks = [{"question_id": i["question_id"], "awarded": 0} for i in initial["memo"]["items"]]
        attempts: list[dict[str, Any]] = [
            {"attempt": 1, "marks": marks[:-1]},
            {"attempt": 2, "marks": []},
        ]
        if complete:
            attempts = [
                {"attempt": 1, "marks": marks[:-1]},
                {"attempt": 2, "marks": marks},
                {"attempt": 3, "marks": marks},
            ]
        return {
            **initial,
            "question": {
                "topic": "script",
                "script_id": "L-014",
                "question_id": "3.1",
                "attempts": attempts,
            },
        }

    async def resume(self: Planner, aid: str, answer: Any) -> Any:
        if complete:
            assert answer == {"decision": "use_attempt", "attempt": 2}
        else:  # no complete attempt: the teacher sets the marks, attempt 1's plus a 0
            assert answer["decision"] == "set_marks"
            assert [m["awarded"] for m in answer["marks"]] == [0] * len(answer["marks"])
        return {"assessment_id": aid, "state": "delivered"}

    monkeypatch.setattr(Planner, "start", start)
    monkeypatch.setattr(Planner, "resume", resume)
    assert run.main(args(tmp_path)) == 0
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.teacher_questions == ("script:3.1",)
    assert result.halted is None


def test_budget_question_raises_above_spent_and_is_recorded(
    replay: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def start(self: Planner, initial: Any) -> Any:
        return {
            **initial,
            "spent_zar": "42.75",
            "question": {
                "topic": "budget",
                "spent_zar": "42.75",
                "limit_zar": "40.00",
                "scripts_total": 32,
                "scripts_settled": 6,
                "spent": [{"stage": "marking", "zar": "42.75"}],
                "estimate": [],
            },
        }

    async def resume(self: Planner, aid: str, answer: Any) -> Any:
        assert answer == {"raise_to_zar": "82.75"}
        return {"assessment_id": aid, "state": "delivered", "spent_zar": "42.75"}

    monkeypatch.setattr(Planner, "start", start)
    monkeypatch.setattr(Planner, "resume", resume)
    assert run.main(args(tmp_path)) == 0
    result = EvalResult.model_validate_json(next(tmp_path.glob("results-*.json")).read_text())
    assert result.teacher_questions == ("budget:",)
    assert result.halted is None


@pytest.mark.parametrize("failure", ["pricing", "git", "url", "provider", "initial_state"])
def test_all_pre_run_failures_exit_two_without_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    import subprocess

    monkeypatch.setenv("MOTHUSI_PROVIDER", "replay")
    output = tmp_path / "results"
    command = args(output)
    if failure == "pricing":
        command[1] = str(tmp_path / "missing-pricing.toml")
    elif failure == "git":

        def git_failure(*args: Any, **kwargs: Any) -> Any:
            assert kwargs.get("stderr") == subprocess.PIPE
            raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

        monkeypatch.setattr(subprocess, "check_output", git_failure)
    elif failure == "url":
        monkeypatch.setenv("MOTHUSI_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_URL", "http://test-host:not-a-port")
        original = httpx.AsyncClient

        def no_network(request: httpx.Request) -> httpx.Response:
            pytest.fail("an invalid URL must fail before transport")

        def client(**kw: Any) -> httpx.AsyncClient:
            return original(transport=httpx.MockTransport(no_network), **kw)

        monkeypatch.setattr(httpx, "AsyncClient", client)
    elif failure == "provider":

        def provider_failure() -> Any:
            raise RuntimeError("provider setup\nfailed")

        monkeypatch.setattr(run, "from_environment", provider_failure)
    else:

        def invalid_initial(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("invalid initial state")

        monkeypatch.setattr(run, "new_run", invalid_initial)

    async def must_not_start(self: Planner, initial: Any) -> Any:
        pytest.fail("a setup failure must not start the Planner")

    monkeypatch.setattr(Planner, "start", must_not_start)
    assert run.main(command) == 2
    assert not output.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err
    expected_error = {
        "pricing": "FileNotFoundError",
        "git": "CalledProcessError",
        "url": "InvalidURL",
        "provider": "RuntimeError",
        "initial_state": "RuntimeError",
    }[failure]
    assert expected_error in captured.err


def test_no_complete_attempt_gets_the_teachers_own_marks(amended_memo: Memo) -> None:
    """2026-11-12 eval: no attempt marked every question of L-030, so the run halted. A teacher
    sets the marks instead: the most complete attempt, a question it left out scored 0."""
    items = [i.question_id for i in amended_memo.items]

    def row(qs: list[str]) -> list[dict[str, Any]]:
        return [{"question_id": q, "awarded": 1} for q in qs]

    question: dict[str, Any] = {
        "topic": "script",
        "script_id": "L-030",
        "attempts": [
            {"attempt": 1, "marks": row(items[:2])},
            {"attempt": 2, "marks": row(items[:-1])},
            {"attempt": 3, "marks": []},
        ],
    }
    answer = run.teacher_answer(question, amended_memo)
    assert answer["decision"] == "set_marks"
    assert [m["question_id"] for m in answer["marks"]] == items
    assert [m["awarded"] for m in answer["marks"]] == [1] * (len(items) - 1) + [0]
