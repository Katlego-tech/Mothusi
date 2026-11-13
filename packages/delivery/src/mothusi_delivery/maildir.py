"""Deliver approved messages to a local Maildir without using a network."""

import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from mothusi_delivery.mailer import Message


class MaildirMailer:
    """Stable message identities and atomic local delivery into new/."""

    name = "maildir"

    def __init__(
        self, root: Path, sender: str, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._root = root
        self._sender = sender
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        for directory in ("cur", "new", "tmp"):
            (root / directory).mkdir(parents=True, exist_ok=True)

    def send(self, message: Message) -> str:
        source = f"{self._sender}\n{message.to}\n{message.subject}\n{message.body}"
        identifier = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32] + "@mothusi.local"
        destination = self._root / "new" / f"{identifier}.eml"
        if destination.exists():
            return identifier
        email = EmailMessage(policy=policy.SMTP)
        email["From"] = self._sender
        email["To"] = message.to
        email["Subject"] = message.subject
        email["Date"] = format_datetime(self._clock())
        email["Message-ID"] = f"<{identifier}>"
        email.set_content(message.body, charset="utf-8")
        with NamedTemporaryFile(dir=self._root / "tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(email.as_bytes())
                temporary.flush()
                os.fsync(temporary.fileno())
                try:
                    os.link(temporary_path, destination)
                except FileExistsError:
                    # A concurrent send already published this same content-addressed message.
                    return identifier
            finally:
                temporary_path.unlink(missing_ok=True)
        return identifier

    def sent(self) -> tuple[str, ...]:
        return tuple(
            sorted(path.stem for path in (self._root / "new").glob("*.eml") if path.is_file())
        )
