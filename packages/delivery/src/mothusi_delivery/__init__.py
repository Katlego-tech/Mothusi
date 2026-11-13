"""Mothusi delivery: the Mailer protocol and its implementations."""

from mothusi_delivery.links import (
    LinkExpired,
    LinkInvalid,
    NoteLinkClaims,
    note_link,
    note_url,
    read_note_link,
)
from mothusi_delivery.maildir import MaildirMailer
from mothusi_delivery.mailer import Mailer, Message
from mothusi_delivery.note_page import NotePage, note_page, note_page_html

__all__ = [
    "LinkExpired",
    "LinkInvalid",
    "MaildirMailer",
    "Mailer",
    "Message",
    "NoteLinkClaims",
    "NotePage",
    "note_link",
    "note_page",
    "note_page_html",
    "note_url",
    "read_note_link",
]
