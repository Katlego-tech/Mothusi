"""Shared fixtures for the output agents' tests: the synthetic class set, marked by code.

The marking here is test data, not Mothusi's: a learner's last line that `equivalent` reads as the
memo answer earns full marks with that line quoted, anything else earns 0. It passes every rules
check, which is all the Analyst, Writer and Reviewer need from it. Memo item 2.3 is amended to
x = -4, as the teacher does at memo_check (traps.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import JsonValue

from mothusi_contracts import (
    ItemStat,
    Mark,
    MarkedScript,
    Memo,
    Script,
    SubjectPack,
)
from mothusi_core.delivery import Delivery
from mothusi_delivery.store import LocalBundleStore
from mothusi_intake import load_memo, load_scripts
from mothusi_llm import ReplayProvider, record
from mothusi_rules import check_evidence, check_memo_lines, check_script, equivalent, item_analysis
from mothusi_rules import load_pack as _load_pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "services/core/fixtures/gr10-maths-t1"
ASSESSMENT = "gr10-maths-t1-test"


def no_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return Decimal(0)


class Recorder:
    """Append replay records keyed on the exact prompts an agent builds; then replay them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.touch()

    def add(
        self, prompts: tuple[str, str], output_model: str, reply: Mapping[str, JsonValue]
    ) -> None:
        system, user = prompts
        record(self.path, system, user, output_model, "replay-model", reply, 900, 300)

    def provider(self) -> ReplayProvider:
        return ReplayProvider(self.path, no_cost)


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    return Recorder(tmp_path / "calls.jsonl")


@pytest.fixture(scope="session")
def amended_memo() -> Memo:
    memo = load_memo(FIXTURES / "memo.toml")
    items = tuple(
        item.model_copy(update={"answer": "x = -4"}) if item.question_id == "2.3" else item
        for item in memo.items
    )
    return memo.model_copy(update={"items": items})


@pytest.fixture(scope="session")
def maths_pack() -> SubjectPack:
    return _load_pack(ROOT / "packs/dbe-caps/mathematics-gr10.toml")


@pytest.fixture(scope="session")
def class_set() -> tuple[tuple[Script, ...], dict[str, str]]:
    """The 32 pseudonymised scripts and the name -> pseudonym map intake returns."""
    return load_scripts(FIXTURES / "scripts.csv")


def _final(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def _matches(memo_answer: str, final: str) -> bool:
    try:
        return equivalent(memo_answer, final.lstrip("= ").strip())
    except ValueError:
        return False


def code_marking(memo: Memo, script: Script) -> MarkedScript:
    answers = {a.question_id: a.text for a in script.answers}
    marks: list[Mark] = []
    for item in memo.items:
        text = answers.get(item.question_id)
        final = _final(text) if text else ""
        if text and final and _matches(item.answer, final):
            marks.append(
                Mark(
                    question_id=item.question_id,
                    awarded=item.max_marks,
                    max_marks=item.max_marks,
                    evidence_quote=final,
                    memo_line=item.answer,
                    principle="answer matches memo",
                )
            )
        else:
            marks.append(
                Mark(
                    question_id=item.question_id,
                    awarded=0,
                    max_marks=item.max_marks,
                    memo_line=item.answer,
                    principle="no attempt" if not text else "answer does not match memo",
                )
            )
    return MarkedScript(
        script_id=script.script_id,
        marker="marker",
        marks=tuple(marks),
        total=sum(m.awarded for m in marks),
    )


@pytest.fixture(scope="session")
def marked_class(
    amended_memo: Memo, class_set: tuple[tuple[Script, ...], dict[str, str]]
) -> tuple[MarkedScript, ...]:
    scripts, _ = class_set
    marked = tuple(code_marking(amended_memo, s) for s in scripts)
    by_id = {s.script_id: s for s in scripts}
    for m in marked:  # the test data must itself pass the rules, or the tests prove nothing
        checks = (
            *check_script(m, amended_memo),
            check_evidence(m, by_id[m.script_id]),
            check_memo_lines(m, amended_memo),
        )
        assert all(c.passed for c in checks), [c.detail for c in checks if not c.passed]
    return marked


@pytest.fixture(scope="session")
def stats(amended_memo: Memo, marked_class: Sequence[MarkedScript]) -> tuple[ItemStat, ...]:
    return item_analysis(marked_class, amended_memo)


TEST_NOTE_KEY = "test-note-key-0123456789abcdefghijklmnopqrstuvwxyz"
TEST_PUBLIC_URL = "https://mothusi.test"


def delivery_at(root: Path) -> Delivery:
    """Delivery settings for a test: a fixed key, an https base and a local store under root."""
    return Delivery(
        TEST_NOTE_KEY.encode(), TEST_PUBLIC_URL, 14, LocalBundleStore(root / "published")
    )


@pytest.fixture(autouse=True)
def _note_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end runs reach the Executor, which signs learner note links."""
    monkeypatch.setenv("MOTHUSI_NOTE_KEY", TEST_NOTE_KEY)
    monkeypatch.setenv("MOTHUSI_PUBLIC_URL", TEST_PUBLIC_URL)
    monkeypatch.delenv("MOTHUSI_BUNDLE_BUCKET", raising=False)
