"""The object-store boundary.

Every backend implements this port, so domain and ingestion code never imports a
storage SDK. Errors are typed here for the same reason: a caller must never have
to catch ``botocore.exceptions.ClientError`` to know that a key was missing.

Writes are content-addressed and therefore idempotent: storing the same bytes
under the same key twice is a no-op, not an error. See ``keys.py``.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import IO, Protocol


class ObjectStoreError(RuntimeError):
    """Base class for object-store failures."""


class ObjectStoreUnavailableError(ObjectStoreError):
    """The backend could not be reached, timed out, or refused the request."""


class ObjectNotFoundError(ObjectStoreError):
    """No object exists at the requested key."""


class ObjectAlreadyExistsError(ObjectStoreError):
    """A content-addressed key already holds an object with different content.

    This must never happen in normal operation: the key is derived from the hash
    of the bytes, so an existing object under the same key is the same object. It
    signals corruption, a truncated earlier write, or a key-derivation defect,
    and is therefore an error rather than a silent overwrite.
    """


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """What the store knows about a stored object."""

    key: str
    size_bytes: int


class ObjectStore(Protocol):
    """Storage operations required by FinSight."""

    def put_if_absent(
        self,
        key: str,
        source: IO[bytes],
        *,
        size_bytes: int,
        sha256_hex: str,
    ) -> ObjectInfo:
        """Store ``source`` at ``key`` unless identical content is already there.

        Idempotent by design. ``size_bytes`` and ``sha256_hex`` describe bytes the
        caller has already hashed while streaming, and are used for integrity
        checks rather than recomputed here.

        Raises:
            ObjectAlreadyExistsError: the key holds an object of a different size.
            ObjectStoreUnavailableError: the backend could not be reached.
        """
        ...

    def open_stream(self, key: str) -> AbstractContextManager[IO[bytes]]:
        """Open the stored object for reading.

        Returns a context manager because the underlying stream holds a network
        connection that must be released; making that structural removes the
        chance of a caller forgetting.

        Raises:
            ObjectNotFoundError: no object exists at the key.
            ObjectStoreUnavailableError: the backend could not be reached.
        """
        ...

    def exists(self, key: str) -> bool:
        """Report whether an object is stored at the key."""
        ...

    def stat(self, key: str) -> ObjectInfo:
        """Return what is known about the stored object.

        Raises:
            ObjectNotFoundError: no object exists at the key.
        """
        ...


def read_in_chunks(stream: IO[bytes], chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Yield a stream's contents in bounded chunks.

    Used by callers that verify integrity on read without holding a whole filing
    in memory.
    """
    while chunk := stream.read(chunk_size):
        yield chunk
