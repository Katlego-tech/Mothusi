"""The approval gate and the Executor: the only path by which anything leaves Mothusi.

Design: docs/design/marking-pipeline.md §4 steps 27-30, §5 and §6.1; the `executor` contract;
docs/design/outputs-and-delivery.md §4. `executor_brief` is the only constructor of an Executor
Brief and refuses any state but `approved` (Non-negotiable I). `execute` calls no model: it checks
the approval again, writes the bundle whole (`write_bundle`), then sends each message through the
injected Mailer (MaildirMailer in the demo). Names join the notes here, in the teacher's files.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from pydantic import AwareDatetime, Field, ValidationError

from mothusi_contracts import (
    AgentRole,
    AssessmentState,
    Brief,
    Contract,
    ConvertedMark,
    LearnerNote,
    PoATask,
    Result,
    ResultStatus,
)
from mothusi_core.agents.loop import refuse, wrong_role
from mothusi_core.delivery import Delivery
from mothusi_delivery import Mailer, Message, note_link, note_page, note_url
from mothusi_delivery.store import publish_bundle
from mothusi_outputs import learner_notes_pdf, mark_sheet_xlsx, moderation_report_pdf, write_bundle
from mothusi_rules import mark_sheet_csv


class Approval(Contract):
    """The teacher's approval of one bundle: who, and when."""

    by: str = Field(min_length=1)
    at: AwareDatetime


class SheetRow(Contract):
    learner: str = Field(min_length=1, description="The learner's name: this is the teacher's file")
    converted: ConvertedMark


class NamedNote(Contract):
    name: str = Field(min_length=1)
    note: LearnerNote


class Bundle(Contract):
    """Everything write_bundle needs, as the teacher approved it."""

    root: str = Field(min_length=1)
    title: str = Field(min_length=1)
    task: PoATask
    mark_sheet_rows: tuple[SheetRow, ...] = Field(min_length=1)
    class_feedback_md: str = Field(min_length=1)
    catch_up_plan_md: str = Field(min_length=1)
    moderation_report_md: str = Field(min_length=1)
    learner_notes: tuple[NamedNote, ...] = ()


class _Inputs(Contract):
    approval: Approval
    bundle: Bundle
    messages: tuple[Message, ...] = ()


def executor_brief(
    assessment_id: str,
    state: AssessmentState,
    approval: Approval,
    bundle: Bundle,
    messages: Sequence[Message],
) -> Brief:
    """The Executor's Brief, which exists only once the assessment is `approved`."""
    if state is not AssessmentState.APPROVED:
        raise ValueError(f"no Executor Brief before approved: the assessment is {state.value}")
    return Brief(
        brief_id=f"{assessment_id}/class/executor/1",
        assessment_id=assessment_id,
        agent=AgentRole.EXECUTOR,
        task="Write the approved bundle and send the approved messages",
        inputs={
            "approval": approval.model_dump(mode="json"),
            "bundle": bundle.model_dump(mode="json"),
            "messages": [m.model_dump(mode="json") for m in messages],
        },
    )


def _md(text: str) -> str:
    """Contracts strip ends; a markdown file still ends with one newline."""
    return text.rstrip("\n") + "\n"


def execute(brief: Brief, mailer: Mailer, delivery: Delivery | str) -> Result:
    """Write, publish, then send. Without a valid approval, or without the note key and public
    URL (`delivery` is then the sentence saying what is missing), nothing is written or sent.

    Note links are bearer credentials: they go into the printed notes' QR codes and nowhere
    else. The Result counts them (`links_issued`) and never carries one.
    """
    role = AgentRole.EXECUTOR
    if (refused := wrong_role(brief, role)) is not None:
        return refused
    if "approval" not in brief.inputs:
        return refuse(brief, role, "no teacher approval in the brief: nothing written or sent")
    try:
        inputs = _Inputs.model_validate(brief.inputs)
    except ValidationError as error:
        return refuse(brief, role, f"brief inputs do not fit the executor contract: {error}")
    if isinstance(delivery, str):
        return refuse(brief, role, f"nothing written or sent: {delivery}")
    b = inputs.bundle
    rows = [(r.learner, r.converted) for r in b.mark_sheet_rows]
    expires_at = inputs.approval.at + timedelta(days=delivery.link_days)
    try:
        urls = [
            note_url(
                delivery.public_url,
                note_link(delivery.key, brief.assessment_id, n.note.pseudonym, expires_at),
            )
            for n in b.learner_notes
        ]
        report_pdf = moderation_report_pdf(b.moderation_report_md, title=b.title)
        notes_pdf = learner_notes_pdf(
            b.title, [(n.name, n.note, url) for n, url in zip(b.learner_notes, urls, strict=True)]
        )
        written = write_bundle(
            Path(b.root),
            title=b.title,
            mark_sheet_csv=mark_sheet_csv(rows, b.task),
            mark_sheet_xlsx=mark_sheet_xlsx(rows, b.task),
            class_feedback_md=_md(b.class_feedback_md),
            catch_up_plan_md=_md(b.catch_up_plan_md),
            moderation_report_md=_md(b.moderation_report_md),
            learner_notes=[(n.name, n.note) for n in b.learner_notes],
            moderation_report_pdf=report_pdf,
            learner_notes_pdf=notes_pdf,
            note_pages=[
                (n.note.pseudonym, note_page(n.note, b.title, expires_at)) for n in b.learner_notes
            ],
        )
    except (OSError, ValueError) as error:
        return refuse(brief, role, f"bundle not written, nothing sent: {error}")
    paths = [str(p) for p in written]
    try:
        publish_bundle(Path(b.root), delivery.store, f"{brief.assessment_id}/bundle")
    except Exception as error:  # any storage failure (S3 AccessDenied, a lost connection) blocks
        return Result(
            brief_id=brief.brief_id,
            agent=role,
            status=ResultStatus.BLOCKED,
            outputs={"written": paths, "sent": []},
            reason=(
                f"bundle written but not published, nothing sent: {type(error).__name__}: {error}"
            ),
        )
    sent: list[str] = []
    for message in inputs.messages:
        try:
            sent.append(mailer.send(message))
        except Exception as error:  # any mailer failure stops the batch and is reported
            return Result(
                brief_id=brief.brief_id,
                agent=role,
                status=ResultStatus.BLOCKED,
                outputs={"written": paths, "sent": sent},
                reason=(
                    f"{mailer.name}: sending stopped after {len(sent)} of "
                    f"{len(inputs.messages)} messages: {error}"
                ),
            )
    approval = inputs.approval
    return Result(
        brief_id=brief.brief_id,
        agent=role,
        status=ResultStatus.DONE,
        outputs={
            "written": paths,
            "sent": sent,
            "links_issued": len(urls),
            "links_expire": expires_at.date().isoformat(),
        },
        evidence=(f"approved by {approval.by} at {approval.at.isoformat()}",),
    )
