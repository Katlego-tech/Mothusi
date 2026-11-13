"""The interface the Executor sends through. Design: docs/design/outputs-and-delivery.md §6.

Implementations: MaildirMailer (writes RFC 5322 files for the demo). A real SMTP sender is
another implementation, for after the event. The Executor never imports an implementation directly.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from mothusi_contracts import Contract


class Message(Contract):
    """One outbound message to a learner or parent. Plain text; the teacher has approved it."""

    to: str = Field(min_length=3, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class Mailer(Protocol):
    """Send one message; return its message id. Idempotent for an identical message."""

    name: str

    def send(self, message: Message) -> str: ...
