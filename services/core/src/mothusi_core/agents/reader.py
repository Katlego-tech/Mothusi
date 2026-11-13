"""The reader: one learner's answer crops to text, on the Cape Town model host (intake.md §11).

Each crop goes to the vision model alone, with an instruction to transcribe exactly and flag any
unclear line. A crop the model fails on twice becomes an unreadable Answer, which the teacher
types at `reading_check`; a failure is never a silent blank. Names never reach the reader: the
name band is blanked before cropping, and the transcription is swept for class-list names after.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import Field

from mothusi_contracts import (
    AgentRole,
    Answer,
    Brief,
    Contract,
    Result,
    ResultStatus,
    Script,
    Transcription,
)
from mothusi_intake.pages import sweep_names, to_answer
from mothusi_llm import Provider, ProviderError

SYSTEM = (
    "You read one handwritten answer box from a South African school test. Transcribe exactly "
    "what is written, line by line, top to bottom. Keep the learner's working, symbols and "
    "mistakes as written; never correct, complete or mark anything. Mark a line uncertain when "
    "any character in it is unclear. If the box is empty, return no lines."
)
USER = "Question {question_id}. Transcribe this answer box."
ATTEMPTS = 2


class CropRef(Contract):
    """One answer crop on disk, by question (the Brief carries paths, never image bytes)."""

    question_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


async def read_crop(provider: Provider, question_id: str, png: bytes) -> Transcription | None:
    """The model's transcription, or None after ATTEMPTS failures (an unreadable box)."""
    for _ in range(ATTEMPTS):
        try:
            reply, _usage = await provider.complete(
                SYSTEM, USER.format(question_id=question_id), Transcription, images=(png,)
            )
        except ProviderError:
            continue
        return reply
    return None


async def read_script(
    brief: Brief, provider: Provider, names: Mapping[str, str] | None = None
) -> Result:
    """Brief.inputs: {"pseudonym": str, "crops": [CropRef...]} -> outputs {"script", "swept"}.

    `names` (class-list name -> pseudonym) is applied by code after reading; it is not in the
    Brief, so no name is ever logged with it.
    """
    if brief.agent is not AgentRole.INTAKE:
        return Result(
            brief_id=brief.brief_id,
            agent=AgentRole.INTAKE,
            status=ResultStatus.BLOCKED,
            reason=f"brief is for {brief.agent.value}, not the reader",
        )
    pseudonym = str(brief.inputs["pseudonym"])
    refs: Sequence[CropRef] = [CropRef.model_validate(c) for c in brief.inputs["crops"]]
    answers: list[Answer] = []
    swept = 0
    for ref in refs:
        reading = await read_crop(provider, ref.question_id, Path(ref.path).read_bytes())
        if reading is None:
            answer = Answer(question_id=ref.question_id, text="", unreadable=True)
        else:
            answer = to_answer(ref.question_id, [reading])
            if names:
                answer, count = sweep_names(answer, names)
                swept += count
        answers.append(answer)
    script = Script(script_id=pseudonym, learner_pseudonym=pseudonym, answers=tuple(answers))
    return Result(
        brief_id=brief.brief_id,
        agent=AgentRole.INTAKE,
        status=ResultStatus.DONE,
        outputs={"script": script.model_dump(mode="json"), "swept": swept},
    )
