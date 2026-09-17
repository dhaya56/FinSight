"""Filesystem-backed object store.

Two purposes, both current: it lets unit and contract tests exercise real storage
behaviour without a container, and it is the portability path the blueprint
requires so that object storage is never welded to one backend
(PROJECT_BLUEPRINT.md §29.3, §29.4).

Writes are atomic: content lands in a temporary file that is flushed and then
renamed into place, so a crash mid-write cannot leave a partial object visible
under a content-addressed key.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from finsight.object_store.port import (
    ObjectAlreadyExistsError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
)


class FilesystemObjectStore:
    """Store objects as files beneath a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("key resolves outside the store root")
        return candidate

    def put_if_absent(
        self,
        key: str,
        source: BinaryIO,
        *,
        size_bytes: int,
        sha256_hex: str,
    ) -> ObjectInfo:
        """Store the object unless identical content is already present."""
        del sha256_hex  # The key already encodes the digest; kept for port parity.
        path = self._path_for(key)

        if path.exists():
            stored = path.stat().st_size
            if stored != size_bytes:
                raise ObjectAlreadyExistsError(
                    f"key already holds {stored} bytes, expected {size_bytes}"
                )
            return ObjectInfo(key=key, size_bytes=stored)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
                staged_path = Path(staged.name)
                shutil.copyfileobj(source, staged)
                staged.flush()
                os.fsync(staged.fileno())
            staged_path.replace(path)
        except OSError as error:
            raise ObjectStoreUnavailableError(f"could not write object: {error}") from error

        return ObjectInfo(key=key, size_bytes=path.stat().st_size)

    @contextmanager
    def open_stream(self, key: str) -> Iterator[BinaryIO]:
        """Open the stored object for reading."""
        path = self._path_for(key)
        try:
            handle = path.open("rb")
        except FileNotFoundError as error:
            raise ObjectNotFoundError(f"no object stored at {key}") from error
        except OSError as error:
            raise ObjectStoreUnavailableError(f"could not read object: {error}") from error
        try:
            yield handle
        finally:
            handle.close()

    def exists(self, key: str) -> bool:
        """Report whether an object is stored at the key."""
        return self._path_for(key).is_file()

    def stat(self, key: str) -> ObjectInfo:
        """Return what is known about the stored object."""
        path = self._path_for(key)
        try:
            return ObjectInfo(key=key, size_bytes=path.stat().st_size)
        except FileNotFoundError as error:
            raise ObjectNotFoundError(f"no object stored at {key}") from error
        except OSError as error:
            raise ObjectStoreUnavailableError(f"could not stat object: {error}") from error
