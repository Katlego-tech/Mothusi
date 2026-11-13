"""Short, expiring note credentials. Design: outputs-and-delivery.md §6.2."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime


class LinkInvalid(ValueError):  # noqa: N818 — public name fixed by §6.2
    """The token is malformed or cannot be authenticated with this key."""


class LinkExpired(ValueError):  # noqa: N818 — public name fixed by §6.2
    """An authenticated token has reached its expiry."""


@dataclass(frozen=True)
class NoteLinkClaims:
    """Authenticated routing claims; never include them in public page content."""

    assessment_id: str
    learner_pseudonym: str
    expires_at: datetime


def _validate_inputs(key: bytes, at: datetime) -> None:
    if len(key) < 32:
        raise ValueError("note links need a key of at least 32 bytes")
    if at.utcoffset() is None:
        raise ValueError("note links need a timezone-aware datetime")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    # Enforce the unpadded URL alphabet, including canonical unused trailing bits.
    if not value or "=" in value:
        raise ValueError("invalid base64url")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise ValueError("noncanonical base64url")
    return decoded


def note_link(key: bytes, assessment_id: str, learner_pseudonym: str, expires_at: datetime) -> str:
    """Mint the exact v1 payload and truncated HMAC specified in §6.2."""
    _validate_inputs(key, expires_at)
    if "|" in assessment_id or "|" in learner_pseudonym:
        raise ValueError("note link identifiers cannot contain '|'")
    payload = f"v1|{assessment_id}|{learner_pseudonym}|{int(expires_at.timestamp())}".encode()
    tag = hmac.digest(key, payload, hashlib.sha256)[:16]
    return _encode(payload) + "." + _encode(tag)


def read_note_link(key: bytes, token: str, now: datetime) -> NoteLinkClaims:
    """Authenticate before interpreting claims, then enforce the expiry boundary."""
    _validate_inputs(key, now)
    try:
        encoded_payload, encoded_tag = token.split(".")
        payload = _decode(encoded_payload)
        tag = _decode(encoded_tag)
    except ValueError as exc:
        raise LinkInvalid("invalid note link") from exc

    expected = hmac.digest(key, payload, hashlib.sha256)[:16]
    if not hmac.compare_digest(expected, tag):
        raise LinkInvalid("invalid note link")

    try:
        version, assessment_id, learner_pseudonym, expiry = payload.decode("utf-8").split("|")
        if version != "v1":
            raise ValueError("unknown note link version")
        timestamp = int(expiry)
        if str(timestamp) != expiry:
            raise ValueError("invalid expiry")
        expires_at = datetime.fromtimestamp(timestamp, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise LinkInvalid("invalid note link") from exc

    if now >= expires_at:
        raise LinkExpired("note link has expired")
    return NoteLinkClaims(assessment_id, learner_pseudonym, expires_at)


def note_url(base_url: str, token: str) -> str:
    """Place a credential in the public note route."""
    return base_url.rstrip("/") + "/n/" + token
