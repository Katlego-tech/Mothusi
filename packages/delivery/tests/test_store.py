"""Bundle stores preserve bytes, isolate prefixes and roll failed publications back."""

from io import BytesIO
from pathlib import Path
from typing import Any, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]
import pytest
from botocore.response import StreamingBody  # pyright: ignore[reportMissingTypeStubs]
from botocore.stub import Stubber  # pyright: ignore[reportMissingTypeStubs]

from mothusi_delivery.store import BundleStore, LocalBundleStore, S3BundleStore, publish_bundle


def test_local_round_trip_missing_and_delete(tmp_path: Path) -> None:
    store: BundleStore = LocalBundleStore(tmp_path)
    assert not store.exists("assessment/bundle/")
    store.put("assessment/bundle/report.pdf", b"\x00\xff", "application/pdf")
    store.put("assessment/bundle-other/keep.md", b"keep", "text/markdown; charset=utf-8")
    assert store.get("assessment/bundle/report.pdf") == b"\x00\xff"
    assert store.exists("assessment/bundle")
    with pytest.raises(KeyError):
        store.get("missing")
    store.delete_prefix("assessment/bundle/")
    store.delete_prefix("assessment/bundle/")
    assert not store.exists("assessment/bundle")
    assert store.get("assessment/bundle-other/keep.md") == b"keep"


@pytest.mark.parametrize("key", ["..", "a/../b", "../escape", "/absolute", "a\\..\\b", ""])
def test_local_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = LocalBundleStore(tmp_path / "store")
    for operation in (
        lambda: store.put(key, b"data", "application/pdf"),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete_prefix(key),
    ):
        with pytest.raises(ValueError):
            operation()
    assert not (tmp_path / "store").exists()


def test_local_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    store = LocalBundleStore(root)
    for operation in (
        lambda: store.put("escape/report.pdf", b"data", "application/pdf"),
        lambda: store.get("escape/report.pdf"),
        lambda: store.exists("escape"),
        lambda: store.delete_prefix("escape"),
    ):
        with pytest.raises(ValueError):
            operation()
    assert list(outside.iterdir()) == []


CONTENT_TYPES = {
    "README.md": "text/markdown; charset=utf-8",
    "mark-sheet.csv": "text/csv; charset=utf-8",
    "mark-sheet.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "moderation-report.pdf": "application/pdf",
    "note-pages/L-001.json": "application/json",
    "notes.md": "text/markdown; charset=utf-8",
    "notes/L-001.md": "text/markdown; charset=utf-8",
}


class RecordingStore(LocalBundleStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.types: dict[str, str] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.types[key] = content_type
        super().put(key, data, content_type)


def source_bundle(root: Path) -> Path:
    for name in CONTENT_TYPES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    return root


def test_publish_sorted_keys_content_types_and_refusal(tmp_path: Path) -> None:
    source = source_bundle(tmp_path / "source")
    store = RecordingStore(tmp_path / "store")
    keys = publish_bundle(source, store, "assessment/bundle/")
    assert keys == tuple(sorted(f"assessment/bundle/{name}" for name in CONTENT_TYPES))
    assert store.types == {
        f"assessment/bundle/{name}": kind for name, kind in CONTENT_TYPES.items()
    }
    for key in keys:
        assert store.get(key) == (source / key.removeprefix("assessment/bundle/")).read_bytes()
    with pytest.raises(ValueError, match="exist"):
        publish_bundle(source, store, "assessment/bundle/")
    assert all(store.get(key) for key in keys)


def test_publish_failure_removes_even_the_failing_put(tmp_path: Path) -> None:
    class FailingStore(LocalBundleStore):
        def put(self, key: str, data: bytes, content_type: str) -> None:
            super().put(key, data, content_type)
            if key.endswith(".xlsx"):
                raise OSError("injected put failure after writing")

    store = FailingStore(tmp_path / "store")
    store.put("other/keep.md", b"keep", "text/markdown; charset=utf-8")
    source = source_bundle(tmp_path / "source")
    with pytest.raises(OSError, match="injected put failure"):
        publish_bundle(source, store, "assessment/bundle/")
    assert not store.exists("assessment/bundle/")
    assert store.get("other/keep.md") == b"keep"
    assert publish_bundle(source, LocalBundleStore(tmp_path / "store"), "assessment/bundle/")


def s3_client() -> Any:
    # Explicit credentials prevent credential-provider discovery; Stubber handles every call.
    return cast(Any, boto3).client(
        "s3", region_name="af-south-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )


def test_s3_round_trip_missing_and_error_propagation() -> None:
    client = s3_client()
    stream = BytesIO(b"pdf bytes")
    with cast(Any, Stubber)(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "bundles",
                "Key": "base/a/report.pdf",
                "Body": b"pdf bytes",
                "ContentType": "application/pdf",
            },
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(stream, 9)},
            {
                "Bucket": "bundles",
                "Key": "base/a/report.pdf",
            },
        )
        stub.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": "bundles", "Key": "base/missing"},
        )
        stub.add_client_error(
            "get_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": "bundles", "Key": "base/private"},
        )
        store: BundleStore = S3BundleStore("bundles", "base/", client)
        store.put("a/report.pdf", b"pdf bytes", "application/pdf")
        assert store.get("a/report.pdf") == b"pdf bytes"
        assert stream.closed
        with pytest.raises(KeyError):
            store.get("missing")
        with pytest.raises(client.exceptions.ClientError, match="AccessDenied"):
            store.get("private")
        stub.assert_no_pending_responses()


def test_s3_exists_and_paginated_delete() -> None:
    client = s3_client()
    with cast(Any, Stubber)(client) as stub:
        for contents in ([], [{"Key": "base/a/bundle/a.md"}]):
            stub.add_response(
                "list_objects_v2",
                {"Contents": contents},
                {
                    "Bucket": "bundles",
                    "Prefix": "base/a/bundle/",
                    "MaxKeys": 1,
                },
            )
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "base/a/bundle/a.md"}],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
            {"Bucket": "bundles", "Prefix": "base/a/bundle/"},
        )
        stub.add_response("delete_object", {}, {"Bucket": "bundles", "Key": "base/a/bundle/a.md"})
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "base/a/bundle/b.pdf"}],
                "IsTruncated": False,
            },
            {"Bucket": "bundles", "Prefix": "base/a/bundle/", "ContinuationToken": "next"},
        )
        stub.add_response("delete_object", {}, {"Bucket": "bundles", "Key": "base/a/bundle/b.pdf"})
        store = S3BundleStore("bundles", "base", client)
        assert not store.exists("a/bundle/")
        assert store.exists("a/bundle/")
        store.delete_prefix("a/bundle/")
        stub.assert_no_pending_responses()
