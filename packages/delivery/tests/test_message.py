"""A message has a real address, a subject and a body."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mothusi_delivery import Message


def test_message_accepts_an_address() -> None:
    message = Message(to="parent@example.org", subject="Term 1 Maths test", body="Dear parent ...")
    assert message.to == "parent@example.org"


@pytest.mark.parametrize("to", ["parent", "parent@", "@example.org", "a b@example.org"])
def test_message_rejects_a_non_address(to: str) -> None:
    with pytest.raises(ValidationError):
        Message(to=to, subject="s", body="b")


def test_message_needs_subject_and_body() -> None:
    with pytest.raises(ValidationError):
        Message(to="p@example.org", subject="", body="b")
    with pytest.raises(ValidationError):
        Message(to="p@example.org", subject="s", body="")
