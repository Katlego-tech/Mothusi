"""US4 scenarios 1 and 3: Analyst, Support and Writer on the synthetic class set (replay).

Design: docs/architecture/design/marking-pipeline.md §6.1; the `analyst`, `support` and `writer` contracts.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mothusi_contracts import (
    AgentRole,
    AtpWeek,
    CatchUpItem,
    Disagreement,
    ItemStat,
    LearnerNote,
    MarkedScript,
    Memo,
    ResultStatus,
    Script,
    SubjectPack,
)
from mothusi_core.agents.analyst import (
    AnalystReply,
    Misconception,
    analyse,
    analyst_brief,
    analyst_prompts,
)
from mothusi_core.agents.loop import run_with_retries
from mothusi_core.agents.support import SupportReply, plan_catch_up, support_brief, support_prompts
from mothusi_core.agents.writer import (
    CorrectionsReply,
    ModerationReportData,
    NoteReply,
    corrections_prompts,
    note_prompts,
    weakest,
    write,
    writer_brief,
)
from mothusi_delivery import note_link, note_page, note_url
from mothusi_outputs import (
    BUNDLE_FILES,
    learner_notes_pdf,
    mark_sheet_xlsx,
    moderation_report_pdf,
    write_bundle,
)
from mothusi_rules import (
    check_brief_blind,
    check_catch_up,
    class_feedback_md,
    convert_mark,
    find_task,
    load_atp_weeks,
    mark_sheet_csv,
    moderation_report_md,
)

from .conftest import ASSESSMENT, FIXTURES, Recorder

Scripts = tuple[tuple[Script, ...], dict[str, str]]


@pytest.fixture(scope="module")
def weeks() -> tuple[AtpWeek, ...]:
    return load_atp_weeks(FIXTURES / "atp-weeks.toml")


KEY = b"outputs-test-key-0123456789abcdefghij"
EXPIRES = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def losing_finals(
    qid: str, scripts: Sequence[Script], marked: Sequence[MarkedScript]
) -> Counter[str]:
    """The last lines of the answers that lost marks on qid, most common first."""
    lost = {
        m.script_id
        for m in marked
        for k in m.marks
        if k.question_id == qid and k.awarded < k.max_marks
    }
    finals: Counter[str] = Counter()
    for s in scripts:
        if s.script_id in lost:
            for a in s.answers:
                if a.question_id == qid and a.text.strip():
                    finals[a.text.strip().splitlines()[-1].strip()] += 1
    return finals


def reteach(stats: Sequence[ItemStat]) -> list[str]:
    return [s.question_id for s in stats if s.flag == "re-teach"]


def grounded_reply(
    stats: Sequence[ItemStat], scripts: Sequence[Script], marked: Sequence[MarkedScript]
) -> AnalystReply:
    drafts: list[dict[str, Any]] = []
    for qid in reteach(stats):
        quote, _ = losing_finals(qid, scripts, marked).most_common(1)[0]
        drafts.append(
            {
                "question_id": qid,
                "pattern": f"the most common wrong final step on {qid}",
                "evidence_quotes": [quote],
            }
        )
    return AnalystReply.model_validate({"misconceptions": drafts, "language_load_flags": ["4"]})


def analyst_outputs(
    recorder: Recorder,
    memo: Memo,
    stats: tuple[ItemStat, ...],
    scripts: tuple[Script, ...],
    marked: tuple[MarkedScript, ...],
) -> list[Misconception]:
    reply = grounded_reply(stats, scripts, marked)
    recorder.add(
        analyst_prompts(memo, stats, marked, scripts), "AnalystReply", reply.model_dump(mode="json")
    )
    brief = analyst_brief(ASSESSMENT, memo, stats, marked, scripts)
    result = asyncio.run(analyse(brief, recorder.provider()))
    assert result.status is ResultStatus.DONE, result.reason
    return [Misconception.model_validate(m) for m in result.outputs["misconceptions"]]


# ----------------------------------------------------------------------------------- analyst


def test_analyst_names_one_grounded_misconception_per_reteach_item(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    scripts, _ = class_set
    found = analyst_outputs(recorder, amended_memo, stats, scripts, marked_class)
    assert [m.question_id for m in found] == reteach(stats)
    for m in found:  # scripts is counted by code: the losing scripts that contain the quote
        expected = losing_finals(m.question_id, scripts, marked_class)[m.evidence_quotes[0]]
        assert m.scripts >= expected >= 1


def test_analyst_refuses_a_quote_no_learner_wrote_and_the_retry_passes(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    scripts, _ = class_set
    good = grounded_reply(stats, scripts, marked_class)
    first = good.misconceptions[0]
    bad = good.model_copy(
        update={
            "misconceptions": (
                first.model_copy(update={"evidence_quotes": ("3^(n+1) because the bases add",)}),
                *good.misconceptions[1:],
            )
        }
    )
    recorder.add(
        analyst_prompts(amended_memo, stats, marked_class, scripts),
        "AnalystReply",
        bad.model_dump(mode="json"),
    )
    failure: list[str] = []

    def brief(attempt: int, previous: str | None):
        if previous:
            failure.append(previous)
            recorder.add(
                analyst_prompts(amended_memo, stats, marked_class, scripts, previous),
                "AnalystReply",
                good.model_dump(mode="json"),
            )
        return analyst_brief(
            ASSESSMENT, amended_memo, stats, marked_class, scripts, attempt, previous
        )

    async def run(b: Any):
        return await analyse(b, recorder.provider())

    outcome = asyncio.run(run_with_retries(brief, run))
    assert outcome.status == "done" and len(outcome.attempts) == 2
    assert outcome.attempts[0].result.status is ResultStatus.RETRY
    assert "3^(n+1) because the bases add" in failure[0]
    assert outcome.attempts[1].brief.attempt == 2


def test_analyst_must_cover_every_reteach_item(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    scripts, _ = class_set
    good = grounded_reply(stats, scripts, marked_class)
    short = good.model_copy(update={"misconceptions": good.misconceptions[1:]})
    recorder.add(
        analyst_prompts(amended_memo, stats, marked_class, scripts),
        "AnalystReply",
        short.model_dump(mode="json"),
    )
    brief = analyst_brief(ASSESSMENT, amended_memo, stats, marked_class, scripts)
    result = asyncio.run(analyse(brief, recorder.provider()))
    assert result.status is ResultStatus.RETRY
    assert good.misconceptions[0].question_id in result.reason


def test_a_class_with_nothing_to_reteach_costs_no_model_call(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    scripts, _ = class_set
    calm = tuple(s.model_copy(update={"flag": None}) for s in stats)
    brief = analyst_brief(ASSESSMENT, amended_memo, calm, marked_class, scripts)
    result = asyncio.run(analyse(brief, recorder.provider()))  # empty recording: no call possible
    assert result.status is ResultStatus.DONE
    assert result.outputs == {"misconceptions": [], "language_load_flags": []}


def test_the_analyst_prompt_carries_no_learner_name(
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    scripts, names = class_set
    brief = analyst_brief(ASSESSMENT, amended_memo, stats, marked_class, scripts)
    assert check_brief_blind(brief, list(names)).passed
    system, user = analyst_prompts(amended_memo, stats, marked_class, scripts)
    assert not any(name in system + user for name in names)


# ----------------------------------------------------------------------------------- support


def support_reply(found: Sequence[Misconception], weeks: Sequence[AtpWeek]) -> SupportReply:
    """Catch-up for each misconception in the first remaining week, source copied exactly."""
    return SupportReply.model_validate(
        {
            "items": [
                {
                    "question_id": m.question_id,
                    "week": weeks[i % len(weeks)].week,
                    "topic": f"Re-teach {m.question_id}",
                    "activity": f"Ten-minute starter on {m.question_id}, then two worked examples",
                    "atp_source": weeks[i % len(weeks)].source,
                }
                for i, m in enumerate(found)
            ]
        }
    )


@pytest.fixture
def misconceptions(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> list[Misconception]:
    return analyst_outputs(recorder, amended_memo, stats, class_set[0], marked_class)


def test_every_catch_up_week_is_one_the_atp_has_left(
    recorder: Recorder, misconceptions: list[Misconception], weeks: tuple[AtpWeek, ...]
) -> None:
    """SPEC US4 scenario 3."""
    reply = support_reply(misconceptions, weeks)
    recorder.add(
        support_prompts(misconceptions, weeks), "SupportReply", reply.model_dump(mode="json")
    )
    result = asyncio.run(
        plan_catch_up(support_brief(ASSESSMENT, misconceptions, weeks), recorder.provider())
    )
    assert result.status is ResultStatus.DONE, result.reason
    plan = [CatchUpItem.model_validate(i) for i in result.outputs["catch_up_plan"]]
    assert len(plan) == len(misconceptions)
    assert check_catch_up(plan, weeks).passed
    assert {i.week for i in plan} <= {w.week for w in weeks}
    assert set(result.outputs["catch_up_plan"][0]) == {
        "week",
        "topic",
        "activity",
        "atp_source",
        "citation",
    }


def test_a_week_already_taught_is_sent_back_then_goes_to_the_teacher(
    recorder: Recorder, misconceptions: list[Misconception], weeks: tuple[AtpWeek, ...]
) -> None:
    good = support_reply(misconceptions, weeks)
    past = good.model_copy(
        update={"items": (good.items[0].model_copy(update={"week": 9}), *good.items[1:])}
    )
    seen: list[str] = []

    def brief(attempt: int, previous: str | None):
        seen.append(previous or "")
        recorder.add(
            support_prompts(misconceptions, weeks, previous),
            "SupportReply",
            past.model_dump(mode="json"),
        )
        return support_brief(ASSESSMENT, misconceptions, weeks, attempt, previous)

    async def run(b: Any):
        return await plan_catch_up(b, recorder.provider())

    outcome = asyncio.run(run_with_retries(brief, run))
    assert outcome.status == "needs_teacher" and len(outcome.attempts) == 3
    assert "week 9 is not in the remaining ATP" in seen[1]
    assert "week 9" in outcome.reason


def test_every_misconception_gets_catch_up_work(
    recorder: Recorder, misconceptions: list[Misconception], weeks: tuple[AtpWeek, ...]
) -> None:
    good = support_reply(misconceptions, weeks)
    short = good.model_copy(update={"items": good.items[1:]})
    recorder.add(
        support_prompts(misconceptions, weeks), "SupportReply", short.model_dump(mode="json")
    )
    result = asyncio.run(
        plan_catch_up(support_brief(ASSESSMENT, misconceptions, weeks), recorder.provider())
    )
    assert result.status is ResultStatus.RETRY
    assert misconceptions[0].question_id in result.reason


# ------------------------------------------------------------------------------------ writer


def note_reply(script: Script, marked: MarkedScript) -> NoteReply:
    """A grounded three-part note: quotes the learner's work and states a mark it was given."""
    best = max(marked.marks, key=lambda m: (m.awarded, m.question_id))
    worst = min(marked.marks, key=lambda m: (m.awarded / m.max_marks, m.question_id))
    quote = best.evidence_quote or next(a.text for a in script.answers).splitlines()[0]
    return NoteReply(
        outcome="Solving and simplifying with algebra and exponents (Term 1 Test)",
        mark_with_quote=(
            f'{best.awarded}/{best.max_marks} on {best.question_id}: you wrote "{quote}"'
        ),
        next_task=f"Redo question {worst.question_id} and check each step against the memo method",
        self_assessment_prompt=f"Which step of question {worst.question_id} would you check first?",
    )


def corrections_reply(stats: Sequence[ItemStat]) -> CorrectionsReply:
    return CorrectionsReply.model_validate(
        {
            "corrections": [
                {
                    "question_id": s.question_id,
                    "common_error": f"the step most scripts dropped on {s.question_id}",
                    "worked_correction": f"Work {s.question_id} again line by line.",
                }
                for s in weakest(stats)
            ]
        }
    )


@pytest.fixture
def plan(misconceptions: list[Misconception], weeks: tuple[AtpWeek, ...]) -> list[CatchUpItem]:
    reply = support_reply(misconceptions, weeks)
    return [
        CatchUpItem(week=i.week, topic=i.topic, activity=i.activity, atp_source=i.atp_source)
        for i in reply.items
    ]


@pytest.fixture(scope="module")
def moderation() -> ModerationReportData:
    return ModerationReportData(
        title="Grade 10 Mathematics Term 1 Test",
        sample=("L-003", "L-014", "L-021"),
        disagreements=(
            Disagreement(script_id="L-021", question_id="4", first=5, second=2, reason="CA"),
        ),
        resolutions={"L-021/4": "re-marked to 2"},
        questions_compared=27,
        moderator="moderator",
    )


def record_writer(
    recorder: Recorder,
    memo: Memo,
    scripts: Sequence[Script],
    marked: Sequence[MarkedScript],
    stats: Sequence[ItemStat],
    found: Sequence[Misconception],
    plan: Sequence[CatchUpItem],
    teacher_note: str | None = None,
) -> None:
    by_id = {s.script_id: s for s in scripts}
    for m in marked:
        script = by_id[m.script_id]
        recorder.add(
            note_prompts(memo, script, m, found, catch_up_plan=plan, teacher_note=teacher_note),
            "NoteReply",
            note_reply(script, m).model_dump(mode="json"),
        )
    recorder.add(
        corrections_prompts(memo, weakest(stats), found, teacher_note=teacher_note),
        "CorrectionsReply",
        corrections_reply(stats).model_dump(mode="json"),
    )


def test_the_writer_drafts_every_document_from_computed_numbers(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
    misconceptions: list[Misconception],
    plan: list[CatchUpItem],
    moderation: ModerationReportData,
) -> None:
    scripts, names = class_set
    record_writer(recorder, amended_memo, scripts, marked_class, stats, misconceptions, plan)
    brief = writer_brief(
        ASSESSMENT,
        memo=amended_memo,
        marked=marked_class,
        scripts=scripts,
        item_stats=stats,
        misconceptions=misconceptions,
        catch_up_plan=plan,
        moderation=moderation,
    )
    assert brief.agent is AgentRole.WRITER
    assert check_brief_blind(brief, list(names)).passed  # no learner name, the Writer's included
    result = asyncio.run(write(brief, recorder.provider()))
    assert result.status is ResultStatus.DONE, result.reason
    out = result.outputs

    notes = [LearnerNote.model_validate(n) for n in out["learner_notes"]]
    assert [n.pseudonym for n in notes] == sorted(m.script_id for m in marked_class)
    assert all(
        n.outcome and n.mark_with_quote and n.next_task and n.self_assessment_prompt for n in notes
    )

    sheet = out["class_sheet_md"]
    assert class_feedback_md(stats) in sheet  # the numbers are the renderer's, verbatim
    for s in weakest(stats):
        item = next(i for i in amended_memo.items if i.question_id == s.question_id)
        assert f"**Memo answer:** {item.answer}" in sheet
    for m in misconceptions:
        assert m.evidence_quotes[0] in sheet and f"{m.scripts} script" in sheet

    for item in plan:
        assert f"| {item.week} |" in out["catch_up_plan_md"]
        assert item.atp_source in out["catch_up_plan_md"]

    assert out["moderation_report_md"] == moderation_report_md(
        moderation.title,
        moderation.sample,
        moderation.disagreements,
        moderation.resolutions,
        moderation.questions_compared,
        moderation.moderator,
    )


def test_corrections_for_the_wrong_questions_are_sent_back(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
    misconceptions: list[Misconception],
    plan: list[CatchUpItem],
    moderation: ModerationReportData,
) -> None:
    scripts, _ = class_set
    by_id = {s.script_id: s for s in scripts}
    for m in marked_class:
        recorder.add(
            note_prompts(amended_memo, by_id[m.script_id], m, misconceptions, catch_up_plan=plan),
            "NoteReply",
            note_reply(by_id[m.script_id], m).model_dump(mode="json"),
        )
    wrong = CorrectionsReply.model_validate(
        {
            "corrections": [
                {"question_id": "1.1", "common_error": "x", "worked_correction": "y"},
                *[c.model_dump() for c in corrections_reply(stats).corrections[1:]],
            ]
        }
    )
    recorder.add(
        corrections_prompts(amended_memo, weakest(stats), misconceptions),
        "CorrectionsReply",
        wrong.model_dump(mode="json"),
    )
    brief = writer_brief(
        ASSESSMENT,
        memo=amended_memo,
        marked=marked_class,
        scripts=scripts,
        item_stats=stats,
        misconceptions=misconceptions,
        catch_up_plan=plan,
        moderation=moderation,
    )
    result = asyncio.run(write(brief, recorder.provider()))
    assert result.status is ResultStatus.RETRY
    assert "1.1" in result.reason


def test_a_rewrite_redrafts_only_the_rejected_note(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
    misconceptions: list[Misconception],
    plan: list[CatchUpItem],
    moderation: ModerationReportData,
) -> None:
    scripts, _ = class_set
    by_id = {s.script_id: s for s in scripts}
    previous = [
        LearnerNote(pseudonym=m.script_id, **note_reply(by_id[m.script_id], m).model_dump())
        for m in marked_class
    ]
    reason = "labels the learner: 'is weak'"
    target = next(m for m in marked_class if m.script_id == "L-007")
    rewritten = note_reply(by_id["L-007"], target).model_copy(update={"outcome": "Rewritten"})
    recorder.add(
        note_prompts(
            amended_memo, by_id["L-007"], target, misconceptions, catch_up_plan=plan, reason=reason
        ),
        "NoteReply",
        rewritten.model_dump(mode="json"),
    )
    recorder.add(
        corrections_prompts(amended_memo, weakest(stats), misconceptions),
        "CorrectionsReply",
        corrections_reply(stats).model_dump(mode="json"),
    )
    brief = writer_brief(
        ASSESSMENT,
        memo=amended_memo,
        marked=marked_class,
        scripts=scripts,
        item_stats=stats,
        misconceptions=misconceptions,
        catch_up_plan=plan,
        moderation=moderation,
        attempt=2,
        notes=previous,
        rewrite={"L-007": reason},
    )
    result = asyncio.run(write(brief, recorder.provider()))  # only L-007 is recorded
    assert result.status is ResultStatus.DONE, result.reason
    notes = {n["pseudonym"]: n for n in result.outputs["learner_notes"]}
    assert notes["L-007"]["outcome"] == "Rewritten"
    assert notes["L-001"] == previous[0].model_dump(mode="json")


# ------------------------------------------------------------------------------------ bundle


def test_the_five_documents_are_written_as_files(
    tmp_path: Path,
    recorder: Recorder,
    amended_memo: Memo,
    maths_pack: SubjectPack,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
    misconceptions: list[Misconception],
    plan: list[CatchUpItem],
    moderation: ModerationReportData,
) -> None:
    """SPEC US4 scenario 1: mark sheet, item analysis, catch-up plan, notes, moderation report."""
    scripts, names = class_set
    record_writer(recorder, amended_memo, scripts, marked_class, stats, misconceptions, plan)
    brief = writer_brief(
        ASSESSMENT,
        memo=amended_memo,
        marked=marked_class,
        scripts=scripts,
        item_stats=stats,
        misconceptions=misconceptions,
        catch_up_plan=plan,
        moderation=moderation,
    )
    out = asyncio.run(write(brief, recorder.provider())).outputs
    task = find_task(maths_pack, "Test")
    name_of = {pseudonym: name for name, pseudonym in names.items()}
    rows = [
        (name_of[m.script_id], convert_mark(m.script_id, m.total, task, maths_pack))
        for m in marked_class
    ]
    notes = [LearnerNote.model_validate(n) for n in out["learner_notes"]]
    paths = write_bundle(
        tmp_path / "bundle",
        title=moderation.title,
        mark_sheet_csv=mark_sheet_csv(rows, task),
        mark_sheet_xlsx=mark_sheet_xlsx(rows, task),
        class_feedback_md=out["class_sheet_md"],
        catch_up_plan_md=out["catch_up_plan_md"],
        moderation_report_md=out["moderation_report_md"],
        learner_notes=[(name_of[n.pseudonym], n) for n in notes],
        moderation_report_pdf=moderation_report_pdf(out["moderation_report_md"], title="Test"),
        learner_notes_pdf=learner_notes_pdf(
            "Test",
            [
                (
                    name_of[n.pseudonym],
                    n,
                    note_url("https://mothusi.test", note_link(KEY, "a1", n.pseudonym, EXPIRES)),
                )
                for n in notes
            ],
        ),
        note_pages=[(n.pseudonym, note_page(n, "Test", EXPIRES)) for n in notes],
    )
    written = {p.relative_to(tmp_path / "bundle").as_posix() for p in paths}
    assert set(BUNDLE_FILES) <= written
    assert len([p for p in written if p.startswith("notes/")]) == len(marked_class)
    report = (tmp_path / "bundle/moderation-report.md").read_text(encoding="utf-8")
    assert "Head of Department: ____________" in report
    assert "Weakest questions" in (tmp_path / "bundle/class-feedback.md").read_text(
        encoding="utf-8"
    )


def test_item_stats_for_a_question_not_in_the_memo_are_refused(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
) -> None:
    stray = (*stats, stats[0].model_copy(update={"question_id": "9.9"}))
    brief = analyst_brief(ASSESSMENT, amended_memo, stray, marked_class, class_set[0])
    result = asyncio.run(analyse(brief, recorder.provider()))
    assert result.status is ResultStatus.BLOCKED and "9.9" in result.reason


def test_a_short_quote_counts_only_as_a_whole_answer(
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    marked_class: tuple[MarkedScript, ...],
) -> None:
    """Codex review P2: an answer of '4' can be quoted; '4' inside a longer answer cannot."""
    from mothusi_contracts import Answer
    from mothusi_core.agents.analyst import check_misconceptions

    qid = reteach(stats)[0]
    lost = [
        m.script_id
        for m in marked_class
        if any(k.question_id == qid and k.awarded < k.max_marks for k in m.marks)
    ]
    scripts = (
        Script(
            script_id=lost[0],
            learner_pseudonym=lost[0],
            answers=(Answer(question_id=qid, text="4"),),
        ),
        Script(
            script_id=lost[1],
            learner_pseudonym=lost[1],
            answers=(Answer(question_id=qid, text="x = 45"),),
        ),
    )
    only = tuple(
        s.model_copy(update={"flag": "re-teach" if s.question_id == qid else None}) for s in stats
    )
    reply = AnalystReply.model_validate(
        {"misconceptions": [{"question_id": qid, "pattern": "p", "evidence_quotes": ["4"]}]}
    )
    found, failures = check_misconceptions(reply, amended_memo, only, marked_class, scripts)
    assert failures == [] and found[0].scripts == 1


def test_a_failed_note_is_retried_with_its_failure_in_the_prompt(
    recorder: Recorder,
    amended_memo: Memo,
    stats: tuple[ItemStat, ...],
    class_set: Scripts,
    marked_class: tuple[MarkedScript, ...],
    misconceptions: list[Misconception],
    plan: list[CatchUpItem],
    moderation: ModerationReportData,
) -> None:
    """Codex review P2: attempt 2's note prompts carry attempt 1's failure, naming the note."""
    scripts, _ = class_set
    two = marked_class[:2]
    by_id = {s.script_id: s for s in scripts}
    first, second = (by_id[m.script_id] for m in two)
    # Attempt 1: the first note's reply is malformed (an empty field), the rest are fine.
    recorder.add(
        note_prompts(amended_memo, first, two[0], misconceptions, catch_up_plan=plan),
        "NoteReply",
        {**note_reply(first, two[0]).model_dump(mode="json"), "next_task": ""},
    )
    recorder.add(
        note_prompts(amended_memo, second, two[1], misconceptions, catch_up_plan=plan),
        "NoteReply",
        note_reply(second, two[1]).model_dump(mode="json"),
    )
    recorder.add(
        corrections_prompts(amended_memo, weakest(stats), misconceptions),
        "CorrectionsReply",
        corrections_reply(stats).model_dump(mode="json"),
    )
    seen: list[str] = []

    def brief(attempt: int, previous: str | None):
        if previous:
            seen.append(previous)
            for script, m in ((first, two[0]), (second, two[1])):
                recorder.add(
                    note_prompts(
                        amended_memo,
                        script,
                        m,
                        misconceptions,
                        catch_up_plan=plan,
                        previous_failure=previous,
                    ),
                    "NoteReply",
                    note_reply(script, m).model_dump(mode="json"),
                )
            recorder.add(
                corrections_prompts(
                    amended_memo, weakest(stats), misconceptions, previous_failure=previous
                ),
                "CorrectionsReply",
                corrections_reply(stats).model_dump(mode="json"),
            )
        return writer_brief(
            ASSESSMENT,
            memo=amended_memo,
            marked=two,
            scripts=scripts,
            item_stats=stats,
            misconceptions=misconceptions,
            catch_up_plan=plan,
            moderation=moderation,
            attempt=attempt,
            previous_failure=previous,
        )

    async def run(b: Any):
        return await write(b, recorder.provider())

    outcome = asyncio.run(run_with_retries(brief, run))
    assert outcome.status == "done" and len(outcome.attempts) == 2
    assert seen[0].startswith(f"note {first.learner_pseudonym}: ")
