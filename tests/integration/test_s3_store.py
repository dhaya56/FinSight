"""Integration tests for the S3-compatible object store.

These run against the object-store container from ``compose.yaml`` and resolve the real
configuration from ``.env``. They are excluded from the default test run and
requested explicitly with ``python -m pytest -m integration``. When the backend is
unreachable they fail rather than skip.

The expectations mirror ``tests/unit/object_store/test_filesystem_store.py`` on
purpose: both adapters implement one port, so both must behave the same way.
"""

import hashlib
from collections.abc import Iterator
from io import BytesIO
from uuid import uuid4

import pytest

from finsight.object_store.port import ObjectAlreadyExistsError, ObjectNotFoundError
from finsight.object_store.s3_store import (
    S3ObjectStore,
    build_s3_object_store,
    dispose_s3_client,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> Iterator[S3ObjectStore]:
    built = build_s3_object_store()
    built.ensure_bucket()
    yield built
    dispose_s3_client()


@pytest.fixture
def content() -> bytes:
    """Unique per test so parallel or repeated runs never collide."""
    return f"integration probe {uuid4().hex}".encode()


@pytest.fixture
def key(content: bytes) -> str:
    """A probe key outside the originals prefix, so tests never touch real objects."""
    return f"tests/{hashlib.sha256(content).hexdigest()}"


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_ensure_bucket_is_idempotent(store: S3ObjectStore) -> None:
    store.ensure_bucket()
    store.ensure_bucket()


def test_stores_content_and_reports_size(
    store: S3ObjectStore,
    key: str,
    content: bytes,
) -> None:
    info = store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    assert info.key == key
    assert info.size_bytes == len(content)
    assert store.exists(key) is True


def test_round_trip_preserves_bytes_exactly(
    store: S3ObjectStore,
    key: str,
    content: bytes,
) -> None:
    """Verify on read: the stored object must still hash to its content address."""
    store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    with store.open_stream(key) as stream:
        retrieved = stream.read()

    assert _digest(retrieved) == _digest(content)


def test_is_idempotent_for_identical_content(
    store: S3ObjectStore,
    key: str,
    content: bytes,
) -> None:
    store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    repeated = store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    assert repeated.size_bytes == len(content)


def test_existing_object_of_a_different_size_is_an_error(
    store: S3ObjectStore,
    key: str,
    content: bytes,
) -> None:
    store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    with pytest.raises(ObjectAlreadyExistsError):
        store.put_if_absent(key, BytesIO(b"short"), size_bytes=5, sha256_hex=_digest(content))


def test_stat_reports_size(store: S3ObjectStore, key: str, content: bytes) -> None:
    store.put_if_absent(
        key, BytesIO(content), size_bytes=len(content), sha256_hex=_digest(content)
    )

    assert store.stat(key).size_bytes == len(content)


def test_missing_object_is_reported(store: S3ObjectStore, key: str) -> None:
    assert store.exists(key) is False

    with pytest.raises(ObjectNotFoundError):
        store.stat(key)

    with pytest.raises(ObjectNotFoundError), store.open_stream(key):
        pass


def test_multipart_upload_round_trips(store: S3ObjectStore, key: str) -> None:
    """Exercise the transfer manager's chunked path, not just a single PUT."""
    large = b"x" * (6 * 1024 * 1024)
    store.put_if_absent(key, BytesIO(large), size_bytes=len(large), sha256_hex=_digest(large))

    with store.open_stream(key) as stream:
        assert _digest(stream.read()) == _digest(large)
