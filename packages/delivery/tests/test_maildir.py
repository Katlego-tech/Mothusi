"""Local delivery produces real RFC 5322 files with stable, idempotent ids."""

import ast
import hashlib
from datetime import UTC, datetime
from email import message_from_bytes, policy
from pathlib import Path

import pytest

from mothusi_delivery import MaildirMailer, Mailer, Message


def test_mail_round_trip_and_identity(tmp_path: Path) -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    root = tmp_path / "mail"
    mailer: Mailer = MaildirMailer(root, "teacher@example.test", lambda: when)
    message = Message(
        to="parent@example.test", subject="Feedback — résumé", body="Good work.\nNext: √(4)."
    )
    identifier = mailer.send(message)
    expected = (
        hashlib.sha256(
            (
                "teacher@example.test\n" + message.to + "\n" + message.subject + "\n" + message.body
            ).encode()
        ).hexdigest()[:32]
        + "@mothusi.local"
    )
    assert identifier == expected and mailer.name == "maildir"
    parsed = message_from_bytes(
        (root / "new" / f"{identifier}.eml").read_bytes(), policy=policy.default
    )
    assert str(parsed["From"]) == "teacher@example.test"
    assert str(parsed["To"]) == message.to
    assert str(parsed["Subject"]) == message.subject
    assert str(parsed["Message-ID"]) == f"<{identifier}>"
    assert parsed["Date"].datetime == when
    payload = parsed.get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert (
        payload.decode(parsed.get_content_charset() or "utf-8").replace("\r\n", "\n").rstrip("\n")
        == message.body
    )
    assert all((root / name).is_dir() for name in ("cur", "new", "tmp"))


def test_idempotent_across_instances_and_clocks(tmp_path: Path) -> None:
    root = tmp_path / "mail"
    first = MaildirMailer(root, "t@example.test", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    message = Message(to="p@example.test", subject="Test", body="Body")
    identifier = first.send(message)
    contents = (root / "new" / f"{identifier}.eml").read_bytes()
    second = MaildirMailer(root, "t@example.test", lambda: datetime(2026, 2, 1, tzinfo=UTC))
    assert second.send(message) == identifier
    assert len(list((root / "new").iterdir())) == 1
    assert (root / "new" / f"{identifier}.eml").read_bytes() == contents
    assert not list((root / "tmp").iterdir())


def test_sent_sorted_and_changed_messages(tmp_path: Path) -> None:
    mailer = MaildirMailer(tmp_path, "t@example.test")
    assert mailer.sent() == ()
    identifiers = [
        mailer.send(Message(to="p@example.test", subject=s, body="Body")) for s in ("B", "A")
    ]
    assert len(set(identifiers)) == 2 and mailer.sent() == tuple(sorted(identifiers))


def test_bad_header_leaves_no_message(tmp_path: Path) -> None:
    mailer = MaildirMailer(tmp_path, "t@example.test")
    with pytest.raises(ValueError):
        mailer.send(
            Message(to="p@example.test", subject="Test\nBcc: secret@example.test", body="Body")
        )
    assert mailer.sent() == () and not list((tmp_path / "tmp").iterdir())


def test_module_has_no_network_imports() -> None:
    import mothusi_delivery.maildir as module

    tree = ast.parse(Path(module.__file__).read_text())
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imports.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not imports & {"socket", "smtplib", "httpx", "requests", "urllib"}
