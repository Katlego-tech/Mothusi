"""Signed note credentials follow outputs-and-delivery.md §6.2."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mothusi_delivery import (
    LinkExpired,
    LinkInvalid,
    NoteLinkClaims,
    note_link,
    note_url,
    read_note_link,
)

KEY = bytes(range(32))
EXPIRY = datetime(2026, 11, 25, 12, tzinfo=UTC)
NOW = EXPIRY - timedelta(days=1)


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def signed(payload: bytes) -> str:
    return encode(payload) + "." + encode(hmac.digest(KEY, payload, hashlib.sha256)[:16])


def test_links_exact_wire_format_and_round_trip() -> None:
    token = note_link(KEY, "maths-é", "L007", EXPIRY)
    assert token == signed(f"v1|maths-é|L007|{int(EXPIRY.timestamp())}".encode())
    assert "=" not in token
    claims = read_note_link(KEY, token, NOW)
    assert isinstance(claims, NoteLinkClaims)
    assert claims.assessment_id == "maths-é"
    assert claims.learner_pseudonym == "L007"
    assert claims.expires_at == EXPIRY
    assert claims.expires_at.tzinfo == UTC


def test_links_flipped_payload_byte_and_rotated_key_are_invalid() -> None:
    token = note_link(KEY, "maths", "L007", EXPIRY)
    payload, tag = token.split(".")
    raw = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw[3] ^= 1
    with pytest.raises(LinkInvalid):
        read_note_link(KEY, encode(bytes(raw)) + "." + tag, NOW)
    with pytest.raises(LinkInvalid):
        read_note_link(b"x" * 32, token, NOW)


@pytest.mark.parametrize("now", [EXPIRY, EXPIRY + timedelta(seconds=1)])
def test_links_expiry_only_after_authentication(now: datetime) -> None:
    token = note_link(KEY, "maths", "L007", EXPIRY)
    with pytest.raises(LinkExpired):
        read_note_link(KEY, token, now)
    payload, tag = token.split(".")
    bad_tag = bytes([base64.urlsafe_b64decode(tag + "==")[0] ^ 1])
    bad_tag += base64.urlsafe_b64decode(tag + "==")[1:]
    with pytest.raises(LinkInvalid):
        read_note_link(KEY, payload + "." + encode(bad_tag), now)


@pytest.mark.parametrize("key", [b"", b"a" * 31])
def test_links_refuse_short_keys(key: bytes) -> None:
    with pytest.raises(ValueError):
        note_link(key, "maths", "L007", EXPIRY)
    with pytest.raises(ValueError):
        read_note_link(key, note_link(KEY, "maths", "L007", EXPIRY), NOW)


def test_links_refuse_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        note_link(KEY, "maths", "L007", EXPIRY.replace(tzinfo=None))
    with pytest.raises(ValueError):
        read_note_link(KEY, note_link(KEY, "maths", "L007", EXPIRY), NOW.replace(tzinfo=None))


def test_links_normalise_aware_time_and_truncate_fractional_expiry() -> None:
    local = EXPIRY.astimezone(timezone(timedelta(hours=2))).replace(microsecond=123)
    token = note_link(KEY, "maths", "L007", local)
    assert read_note_link(KEY, token, NOW).expires_at == EXPIRY


@pytest.mark.parametrize("assessment,pseudonym", [("a|b", "L1"), ("a", "L|1")])
def test_links_refuse_payload_delimiters(assessment: str, pseudonym: str) -> None:
    with pytest.raises(ValueError):
        note_link(KEY, assessment, pseudonym, EXPIRY)


@pytest.mark.parametrize("token", ["", "a", "a.b.c", ".", "a.!", "é.a", "YQ==.Yg=="])
def test_links_malformed_tokens_are_invalid(token: str) -> None:
    with pytest.raises(LinkInvalid):
        read_note_link(KEY, token, NOW)


@pytest.mark.parametrize(
    "payload",
    [b"v2|a|L1|0", b"v1|a|L1", b"v1|a|L1|x", b"\xff", b"v1|a|L1|99999999999999999999"],
)
def test_links_signed_malformed_claims_are_invalid(payload: bytes) -> None:
    with pytest.raises(LinkInvalid):
        read_note_link(KEY, signed(payload), NOW)


@pytest.mark.parametrize("base", ["https://example.org", "https://example.org///"])
def test_links_note_url(base: str) -> None:
    token = note_link(KEY, "maths", "L007", EXPIRY)
    assert note_url(base, token) == "https://example.org/n/" + token
