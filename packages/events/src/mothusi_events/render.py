"""One line per event, for the console and the terminal. Design: docs/architecture/design/event-log.md §6."""

from __future__ import annotations

from collections.abc import Iterable

from mothusi_contracts import Event


def summarise(event: Event) -> str:
    """`00012 2026-11-13T11:05:00+02:00 check    marker    L-014 Q3: ...`, fixed widths."""
    agent = event.agent.value if event.agent is not None else "-"
    stamp = event.at.isoformat(timespec="seconds")
    return f"{event.seq:05d} {stamp} {event.kind.value:<8} {agent:<9} {event.summary}"


def render(events: Iterable[Event]) -> str:
    """The log as text, one summarised event per line, LF-terminated."""
    return "".join(summarise(event) + "\n" for event in events)
