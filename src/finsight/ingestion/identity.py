"""Streaming content identity.

The source-byte hash identifies a document version (PROJECT_BLUEPRINT.md §11.5),
and it is computed while the bytes stream past rather than by loading the upload
into memory. A DRHP can exceed several hundred megabytes, and this process shares
a machine with the model runtime, so holding a whole filing in memory is an
operational fault rather than a detail.

Content spools to a temporary file that small uploads never touch: below the
rollover it stays in memory, above it spills to disk. Both the rollover and the
read chunk are safe operational constants, not measured values.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import IO, BinaryIO, Final

from finsight.domain.identifiers import ContentAddress
from finsight.ingestion.validation.structural_limits import (
    ensure_not_empty,
    ensure_within_limit,
)

READ_CHUNK_BYTES: Final = 1024 * 1024
MEMORY_SPOOL_BYTES: Final = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SpooledContent:
    """Buffered content with its size and content address."""

    handle: IO[bytes]
    """Positioned at the start and valid only inside the spooling context."""

    byte_size: int
    address: ContentAddress


@contextmanager
def spool_and_hash(source: BinaryIO, *, max_bytes: int) -> Iterator[SpooledContent]:
    """Buffer a stream while hashing it, enforcing the size limit as it arrives.

    The limit is checked per chunk, so an oversized upload stops being read at the
    moment it crosses the bound rather than after it has all arrived.

    Raises:
        DocumentRejectedError: the content is empty or exceeds the limit.
    """
    digest = hashlib.sha256()
    byte_count = 0

    with SpooledTemporaryFile(max_size=MEMORY_SPOOL_BYTES) as spool:
        while chunk := source.read(READ_CHUNK_BYTES):
            byte_count += len(chunk)
            ensure_within_limit(byte_count, max_bytes=max_bytes)
            digest.update(chunk)
            spool.write(chunk)

        ensure_not_empty(byte_count)
        spool.seek(0)

        yield SpooledContent(
            handle=spool,
            byte_size=byte_count,
            address=ContentAddress.sha256(digest.hexdigest()),
        )
