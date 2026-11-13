"""Where approved bundles and learner note links live (outputs-and-delivery.md §6.2).

The note key signs every learner's link; the public URL is the base the QR codes point at; the
store is S3 in af-south-1 on AWS (MOTHUSI_BUNDLE_BUCKET) or a folder on the laptop. Missing key or
URL is not guessed: the Executor reports it blocked and writes nothing (`./hack env` makes both).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mothusi_delivery.store import BundleStore, LocalBundleStore, S3BundleStore

DEFAULT_LINK_DAYS = 14


@dataclass(frozen=True)
class Delivery:
    key: bytes
    public_url: str
    link_days: int
    store: BundleStore


def store_from_environment(data_dir: Path) -> BundleStore:
    bucket = os.environ.get("MOTHUSI_BUNDLE_BUCKET")
    if bucket:
        import boto3  # pyright: ignore[reportMissingTypeStubs]

        client: object = boto3.client("s3")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return S3BundleStore(bucket, "", client)
    return LocalBundleStore(data_dir / "published")


def delivery_from_environment(data_dir: Path) -> Delivery | str:
    """The delivery settings, or the sentence saying what is missing."""
    key = os.environ.get("MOTHUSI_NOTE_KEY", "")
    url = os.environ.get("MOTHUSI_PUBLIC_URL", "")
    if len(key.encode()) < 32:
        return "MOTHUSI_NOTE_KEY is not set (32 bytes or more): run ./hack env"
    if not url.startswith("https://"):
        return "MOTHUSI_PUBLIC_URL is not an https:// address: run ./hack env"
    raw_days = os.environ.get("MOTHUSI_NOTE_LINK_DAYS", str(DEFAULT_LINK_DAYS))
    try:
        days = int(raw_days)
    except ValueError:
        return f"MOTHUSI_NOTE_LINK_DAYS must be a whole number of days, not {raw_days!r}"
    if not 1 <= days <= 30:
        return f"MOTHUSI_NOTE_LINK_DAYS must be 1 to 30, not {days}"
    return Delivery(key.encode(), url, days, store_from_environment(data_dir))
