"""Render one learner's note as markdown. Design: docs/architecture/design/outputs-and-delivery.md §3.

The teacher's side carries the name; the pseudonym never appears in the note's text.
"""

from __future__ import annotations

from mothusi_contracts import LearnerNote


def learner_note_md(note: LearnerNote, learner_name: str) -> str:
    """Three fixed parts and one self-assessment line, in this order, nothing else."""
    name = " ".join(learner_name.split())
    if not name:
        raise ValueError("the learner's name is needed for the note")
    return (
        f"# Note for {name}\n"
        "\n"
        f"**What this task tested:** {note.outcome}\n"
        "\n"
        f"**Your mark, and the work that earned it:** {note.mark_with_quote}\n"
        "\n"
        f"**Your next task:** {note.next_task}\n"
        "\n"
        f"**Ask yourself:** {note.self_assessment_prompt}\n"
    )
