"""Check that every positive mark quotes the learner's own answer."""

from mothusi_contracts import CheckResult, MarkedScript, Script

EVIDENCE = "evidence"


def check_evidence(marked: MarkedScript, script: Script) -> CheckResult:
    if marked.script_id != script.script_id:
        raise ValueError("marked and script must have the same script_id")
    answers = {answer.question_id: " ".join(answer.text.split()) for answer in script.answers}
    failures: list[tuple[str, str]] = []
    for mark in marked.marks:
        if mark.awarded == 0:
            continue
        if mark.question_id not in answers:
            failures.append((mark.question_id, f"{mark.question_id}: no answer for this question"))
        elif " ".join(mark.evidence_quote.split()) not in answers[mark.question_id]:
            failures.append(
                (mark.question_id, f"{mark.question_id}: quote not found in the answer")
            )
    return CheckResult(
        check=EVIDENCE,
        about=script.script_id,
        passed=not failures,
        detail="; ".join(message for _, message in sorted(failures))
        or "all positive marks quote the learner's answer",
    )
