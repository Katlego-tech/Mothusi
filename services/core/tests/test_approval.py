"""US5: nothing reaches a learner before the teacher approves.

Design: docs/architecture/design/marking-pipeline.md §5, §6.1; the `executor` contract;
docs/architecture/design/outputs-and-delivery.md §4. The router-level proof (no Executor Brief
before
`approved` in a whole run) is the graph test's job; here the gate and the Executor are tested alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mothusi_contracts import (
    AgentRole,
    AssessmentState,
    Brief,
    CatchUpItem,
    ItemStat,
    LearnerNote,
    MarkedScript,
    Memo,
    ResultStatus,
    Script,
    SubjectPack,
)
from mothusi_core.agents.executor import (
    Approval,
    Bundle,
    NamedNote,
    SheetRow,
    execute,
    executor_brief,
)
from mothusi_core.agents.writer import ModerationReportData, note_prompts, writer_brief
from mothusi_core.delivery import Delivery
from mothusi_delivery import MaildirMailer, Message
from mothusi_delivery.store import LocalBundleStore
from mothusi_rules import convert_mark, find_task

from .conftest import ASSESSMENT, delivery_at

APPROVAL = Approval(by="Ms T. Mokoena", at=datetime(2026, 11, 14, 9, 30, tzinfo=UTC))
CLOCK = datetime(2026, 11, 14, 9, 31, tzinfo=UTC)


@pytest.fixture
def bundle(
    tmp_path: Path,
    maths_pack: SubjectPack,
    class_set: tuple[tuple[Script, ...], dict[str, str]],
    marked_class: tuple[MarkedScript, ...],
) -> Bundle:
    _, names = class_set
    name_of = {pseudonym: name for name, pseudonym in names.items()}
    task = find_task(maths_pack, "Test")
    return Bundle(
        root=str(tmp_path / "bundle"),
        title="Grade 10 Mathematics Term 1 Test",
        task=task,
        mark_sheet_rows=tuple(
            SheetRow(
                learner=name_of[m.script_id],
                converted=convert_mark(m.script_id, m.total, task, maths_pack),
            )
            for m in marked_class
        ),
        class_feedback_md="# Class feedback\n",
        catch_up_plan_md="# Catch-up plan\n",
        moderation_report_md="# Moderation report\n",
        learner_notes=tuple(
            NamedNote(
                name=name_of[m.script_id],
                note=LearnerNote(
                    pseudonym=m.script_id,
                    outcome="Algebra and exponents",
                    mark_with_quote=f"{m.total}/50 overall",
                    next_task="Redo 2.2",
                    self_assessment_prompt="Which law did I use first?",
                ),
            )
            for m in marked_class[:3]
        ),
    )


MESSAGES = (
    Message(to="parent.one@example.org", subject="Term 1 Test feedback", body="Approved note one."),
    Message(to="parent.two@example.org", subject="Term 1 Test feedback", body="Approved note two."),
)


class FailingSecond:
    """A mailer whose second send fails, the way an SMTP server can drop mid-batch."""

    name = "failing"

    def __init__(self, inner: MaildirMailer) -> None:
        self.inner = inner
        self.calls = 0

    def send(self, message: Message) -> str:
        self.calls += 1
        if self.calls == 2:
            raise OSError("connection reset")
        return self.inner.send(message)


@pytest.mark.parametrize("state", [s for s in AssessmentState if s is not AssessmentState.APPROVED])
def test_no_executor_brief_exists_before_approved(state: AssessmentState, bundle: Bundle) -> None:
    """SPEC US5 scenario 1, at the one constructor of an Executor Brief."""
    with pytest.raises(ValueError, match="approved"):
        executor_brief(ASSESSMENT, state, APPROVAL, bundle, MESSAGES)


def test_a_brief_without_approval_writes_and_sends_nothing(tmp_path: Path, bundle: Bundle) -> None:
    good = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    forged = Brief.model_validate(
        {**good.model_dump(), "inputs": {k: v for k, v in good.inputs.items() if k != "approval"}}
    )
    mailer = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    result = execute(forged, mailer, delivery_at(tmp_path))
    assert result.status is ResultStatus.BLOCKED and "approval" in result.reason
    assert not Path(bundle.root).exists()
    assert mailer.sent() == ()


def test_approval_writes_the_bundle_and_sends_every_message(tmp_path: Path, bundle: Bundle) -> None:
    """SPEC US5 scenario 3: files written, messages through the configured mailer."""
    mailer = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    brief = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    assert brief.agent is AgentRole.EXECUTOR
    result = execute(brief, mailer, delivery_at(tmp_path))
    assert result.status is ResultStatus.DONE, result.reason
    written = [Path(p) for p in result.outputs["written"]]
    assert Path(bundle.root) / "mark-sheet.csv" in written
    assert all(p.is_file() for p in written)
    assert sorted(result.outputs["sent"]) == list(mailer.sent()) and len(mailer.sent()) == 2
    note = (Path(bundle.root) / "notes" / "L-001.md").read_text(encoding="utf-8")
    assert bundle.learner_notes[0].name in note  # names appear only in the teacher's outputs
    assert f"approved by {APPROVAL.by}" in result.evidence[0]


def test_a_send_failing_midway_reports_what_was_sent(tmp_path: Path, bundle: Bundle) -> None:
    inner = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    brief = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    result = execute(brief, FailingSecond(inner), delivery_at(tmp_path))
    assert result.status is ResultStatus.BLOCKED and "connection reset" in result.reason
    assert result.outputs["sent"] == list(inner.sent()) and len(inner.sent()) == 1
    assert result.outputs["written"]  # the bundle stays: it is the record of what was approved


def test_a_bundle_root_already_used_is_refused_before_anything_is_sent(
    tmp_path: Path, bundle: Bundle
) -> None:
    Path(bundle.root).mkdir(parents=True)
    (Path(bundle.root) / "README.md").write_text("an earlier run\n", encoding="utf-8")
    mailer = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    brief = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    result = execute(brief, mailer, delivery_at(tmp_path))
    assert result.status is ResultStatus.BLOCKED and "not empty" in result.reason
    assert mailer.sent() == ()


def test_a_brief_for_another_agent_is_refused(tmp_path: Path, bundle: Bundle) -> None:
    good = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    wrong = good.model_copy(update={"agent": AgentRole.WRITER})
    mailer = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    assert execute(wrong, mailer, delivery_at(tmp_path)).status is ResultStatus.BLOCKED
    assert mailer.sent() == ()


def test_a_teacher_rejection_reaches_the_writer_and_leaves_the_marks(
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: tuple[tuple[Script, ...], dict[str, str]],
    marked_class: tuple[MarkedScript, ...],
) -> None:
    """SPEC US5 scenario 2, the Writer's half: the note is in its next Brief; marks unchanged."""
    scripts, _ = class_set
    note = "Please say which ATP week each catch-up activity is in, in the notes too."
    brief = writer_brief(
        ASSESSMENT,
        memo=amended_memo,
        marked=marked_class,
        scripts=scripts,
        item_stats=stats,
        misconceptions=(),
        catch_up_plan=(),
        moderation=ModerationReportData(
            title="T1", sample=("L-001",), questions_compared=9, moderator="moderator"
        ),
        attempt=2,
        teacher_note=note,
    )
    assert brief.inputs["teacher_note"] == note
    assert brief.inputs["marked"] == [m.model_dump(mode="json") for m in marked_class]
    _, user = note_prompts(amended_memo, scripts[0], marked_class[0], (), teacher_note=note)
    assert note in user


def test_a_note_can_name_atp_weeks_only_from_the_checked_plan(
    amended_memo: Memo,
    class_set: tuple[tuple[Script, ...], dict[str, str]],
    marked_class: tuple[MarkedScript, ...],
) -> None:
    """Codex review P2: the teacher's 'say which ATP week' needs the plan in the note prompt."""
    scripts, _ = class_set
    plan = (
        CatchUpItem(
            week=11,
            topic="Re-teach 2.2",
            activity="Starter on prime bases",
            atp_source="RATP Mathematics Grade 10 2024, week 11 (03-05 April)",
        ),
    )
    _, user = note_prompts(amended_memo, scripts[0], marked_class[0], (), catch_up_plan=plan)
    assert "ATP week 11: Re-teach 2.2" in user and plan[0].atp_source in user
    _, bare = note_prompts(amended_memo, scripts[0], marked_class[0], ())
    assert "ATP week" not in bare


def test_a_storage_failure_blocks_with_the_written_paths(tmp_path: Path, bundle: Bundle) -> None:
    class Broken(LocalBundleStore):
        def put(self, key: str, data: bytes, content_type: str) -> None:
            raise RuntimeError("AccessDenied")

    brief = executor_brief(ASSESSMENT, AssessmentState.APPROVED, APPROVAL, bundle, MESSAGES)
    mailer = MaildirMailer(tmp_path / "mail", "mothusi@school.example", lambda: CLOCK)
    good = delivery_at(tmp_path)
    broken = Delivery(good.key, good.public_url, good.link_days, Broken(tmp_path / "published"))
    result = execute(brief, mailer, broken)
    assert result.status is ResultStatus.BLOCKED and "not published" in result.reason
    assert result.outputs["written"] and mailer.sent() == ()
