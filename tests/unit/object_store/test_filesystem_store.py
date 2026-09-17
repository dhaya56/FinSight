"""Tests for the filesystem object store.

These also serve as the behavioural contract the S3 adapter must match; the same
expectations are asserted against a live backend in
``tests/integration/test_s3_store.py``.
"""

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from finsight.object_store.filesystem_store import FilesystemObjectStore
from finsight.object_store.keys import original_object_key
from finsight.object_store.port import ObjectAlreadyExistsError, ObjectNotFoundError

CONTENT = b"deterministic bytes for the filesystem store"


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(root=tmp_path / "objects")


@pytest.fixture
def key() -> str:
    return original_object_key(hashlib.sha256(CONTENT).hexdigest())


class TestPutIfAbsent:
    def test_stores_content_and_reports_size(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        info = store.put_if_absent(
            key,
            BytesIO(CONTENT),
            size_bytes=len(CONTENT),
            sha256_hex=hashlib.sha256(CONTENT).hexdigest(),
        )

        assert info.key == key
        assert info.size_bytes == len(CONTENT)
        assert store.exists(key) is True

    def test_is_idempotent_for_identical_content(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        digest = hashlib.sha256(CONTENT).hexdigest()
        store.put_if_absent(key, BytesIO(CONTENT), size_bytes=len(CONTENT), sha256_hex=digest)

        repeated = store.put_if_absent(
            key, BytesIO(CONTENT), size_bytes=len(CONTENT), sha256_hex=digest
        )

        assert repeated.size_bytes == len(CONTENT)
        with store.open_stream(key) as stream:
            assert stream.read() == CONTENT

    def test_existing_object_of_a_different_size_is_an_error(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        """A content-addressed key holding different content signals corruption."""
        digest = hashlib.sha256(CONTENT).hexdigest()
        store.put_if_absent(key, BytesIO(CONTENT), size_bytes=len(CONTENT), sha256_hex=digest)

        with pytest.raises(ObjectAlreadyExistsError):
            store.put_if_absent(key, BytesIO(b"short"), size_bytes=5, sha256_hex=digest)

    def test_stored_bytes_are_unchanged(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        store.put_if_absent(
            key,
            BytesIO(CONTENT),
            size_bytes=len(CONTENT),
            sha256_hex=hashlib.sha256(CONTENT).hexdigest(),
        )

        with store.open_stream(key) as stream:
            stored = stream.read()

        assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(CONTENT).hexdigest()


class TestReads:
    def test_open_stream_reports_a_missing_object(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        with pytest.raises(ObjectNotFoundError), store.open_stream(key):
            pass

    def test_stat_reports_a_missing_object(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        with pytest.raises(ObjectNotFoundError):
            store.stat(key)

    def test_exists_is_false_for_a_missing_object(
        self,
        store: FilesystemObjectStore,
        key: str,
    ) -> None:
        assert store.exists(key) is False

    def test_stat_reports_size(self, store: FilesystemObjectStore, key: str) -> None:
        store.put_if_absent(
            key,
            BytesIO(CONTENT),
            size_bytes=len(CONTENT),
            sha256_hex=hashlib.sha256(CONTENT).hexdigest(),
        )

        assert store.stat(key).size_bytes == len(CONTENT)


class TestContainment:
    @pytest.mark.parametrize("escaping", ["../outside", "a/../../outside"])
    def test_a_key_may_not_resolve_outside_the_root(
        self,
        store: FilesystemObjectStore,
        escaping: str,
    ) -> None:
        with pytest.raises(ValueError, match="outside"):
            store.exists(escaping)
