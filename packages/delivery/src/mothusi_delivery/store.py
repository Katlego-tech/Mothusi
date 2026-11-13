"""Local and injected S3 storage for approved bundles (§6.2 of the delivery design)."""

import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

# boto3's generated clients have no runtime S3Client class or bundled type stubs.
S3Client = Any


class BundleStore(Protocol):
    """Store bytes by relative key; prefixes address a bundle's directory namespace."""

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write the supplied bytes and media type."""
        ...

    def get(self, key: str) -> bytes:
        """Read bytes, raising KeyError for an absent key."""
        ...

    def exists(self, prefix: str) -> bool:
        """Return whether a bundle prefix is already occupied."""
        ...

    def delete_prefix(self, prefix: str) -> None:
        """Remove the contents of a bundle prefix, including partial publications."""
        ...


def _relative_key(key: str) -> str:
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or "\x00" in key
        or any(part in (".", "..", "") for part in key.rstrip("/").split("/"))
    ):
        raise ValueError(f"invalid relative store key: {key!r}")
    return PurePosixPath(key).as_posix()


class LocalBundleStore:
    """Keep bundle keys under a local root, refusing traversal and escaping symlinks."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        path = self._root / _relative_key(key)
        if not path.resolve().is_relative_to(self._root):
            raise ValueError(f"store key escapes root: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as error:
            raise KeyError(key) from error

    def exists(self, prefix: str) -> bool:
        path = self._path(prefix)
        return path.exists() and (not path.is_dir() or any(path.rglob("*")))

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


class S3BundleStore:
    """Use an injected S3 client; encryption is configured by the bucket's default."""

    def __init__(self, bucket: str, prefix: str, client: S3Client) -> None:
        self._bucket = bucket
        self._prefix = _relative_key(prefix) + "/" if prefix else ""
        self._client = client

    def _key(self, key: str) -> str:
        return self._prefix + _relative_key(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=self._key(key), Body=data, ContentType=content_type
        )

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(key))
        except self._client.exceptions.NoSuchKey as error:
            raise KeyError(key) from error
        with response["Body"] as body:
            return body.read()

    def exists(self, prefix: str) -> bool:
        response = self._client.list_objects_v2(
            Bucket=self._bucket, Prefix=self._key(prefix) + "/", MaxKeys=1
        )
        return bool(response.get("Contents"))

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._key(prefix) + "/"):
            for item in page.get("Contents", ()):
                self._client.delete_object(Bucket=self._bucket, Key=item["Key"])


_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


def publish_bundle(root: Path, store: BundleStore, prefix: str) -> tuple[str, ...]:
    """Publish sorted files once; remove a partial publication if any put fails.

    One publisher per prefix: the Executor publishes an assessment's bundle once, after its single
    approval. `exists` then `put` is not atomic, so two publishers racing on one prefix are not
    supported (the loser's cleanup could remove the winner's files).
    """
    namespace = _relative_key(prefix)
    if store.exists(prefix):
        raise ValueError(f"bundle prefix already exists: {prefix}")
    if not root.is_dir():
        raise ValueError(f"bundle root is not a directory: {root}")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"bundle root holds no files: {root}")
    types: dict[Path, str] = {}
    for path in files:  # every type is known before anything is put
        kind = _CONTENT_TYPES.get(path.suffix.lower())
        if kind is None:
            raise ValueError(f"no content type for {path.name}")
        types[path] = kind
    keys: list[str] = []
    try:
        for path in files:
            key = f"{namespace}/{path.relative_to(root).as_posix()}"
            store.put(key, path.read_bytes(), types[path])
            keys.append(key)
    except BaseException as error:
        try:  # a failed cleanup must not hide why the publish failed
            store.delete_prefix(prefix)
        except Exception as cleanup:
            error.add_note(f"cleanup of {prefix!r} also failed: {cleanup}")
        raise
    return tuple(sorted(keys))
