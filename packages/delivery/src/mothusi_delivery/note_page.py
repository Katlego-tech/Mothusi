"""Name-free note data and HTML. Design: outputs-and-delivery.md §6.2."""

from __future__ import annotations

from datetime import datetime
from html import escape

from pydantic import AwareDatetime

from mothusi_contracts import Contract, LearnerNote


class NotePage(Contract):
    """Only feedback and expiry may cross into the public page."""

    assessment_title: str
    outcome: str
    mark_with_quote: str
    next_task: str
    self_assessment_prompt: str
    expires_at: AwareDatetime


def note_page(note: LearnerNote, assessment_title: str, expires_at: datetime) -> NotePage:
    """Copy the four note parts without carrying the pseudonym."""
    return NotePage(
        assessment_title=assessment_title,
        outcome=note.outcome,
        mark_with_quote=note.mark_with_quote,
        next_task=note.next_task,
        self_assessment_prompt=note.self_assessment_prompt,
        expires_at=expires_at,
    )


def note_page_html(page: NotePage) -> str:
    """Render escaped text in a self-contained HTML5 document."""
    sections = (
        ("What this task tested:", page.outcome),
        ("Your mark, and the work that earned it:", page.mark_with_quote),
        ("Your next task:", page.next_task),
        ("Ask yourself:", page.self_assessment_prompt),
    )
    content = "\n".join(
        f"<section><h2>{label}</h2><p>{escape(value)}</p></section>" for label, value in sections
    )
    title = escape(page.assessment_title)
    expiry = escape(page.expires_at.isoformat())
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{title}</title>\n"
        "<style>body { font-family: sans-serif; line-height: 1.6; margin: 0 auto; "
        "padding: 1.5rem; max-width: 42rem; overflow-wrap: anywhere; } "
        "h1 { font-size: 1.6rem; } h2 { font-size: 1.1rem; } "
        "p { white-space: pre-wrap; }</style>\n"
        f"</head>\n<body>\n<main><h1>{title}</h1>\n{content}\n"
        f'<p>Link expires: <time datetime="{expiry}">{expiry}</time></p>'
        "</main>\n</body>\n</html>\n"
    )
