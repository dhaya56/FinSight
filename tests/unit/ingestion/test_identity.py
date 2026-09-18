"""Tests for streaming content identity."""

import hashlib
from io import BytesIO
from typing import IO

import pytest

from finsight.domain.errors import DocumentRejectedError, RejectionReason
from finsight.ingestion.identity import spool_and_hash

CONTENT = b"a filing's worth of bytes, in miniature"


class CountingStream(BytesIO):
    """Records how many bytes were actually read from the source."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class TestSpoolAndHash:
    def test_computes_the_content_address(self) -> None:
        with spool_and_hash(BytesIO(CONTENT), max_bytes=1024) as content:
            assert content.address.hex_digest == hashlib.sha256(CONTENT).hexdigest()

    def test_reports_the_byte_size(self) -> None:
        with spool_and_hash(BytesIO(CONTENT), max_bytes=1024) as content:
            assert content.byte_size == len(CONTENT)

    def test_hands_back_a_readable_handle_positioned_at_the_start(self) -> None:
        with spool_and_hash(BytesIO(CONTENT), max_bytes=1024) as content:
            assert content.handle.read() == CONTENT

    def test_survives_content_larger_than_one_read_chunk(self) -> None:
        large = b"x" * (3 * 1024 * 1024)

        with spool_and_hash(BytesIO(large), max_bytes=8 * 1024 * 1024) as content:
            assert content.byte_size == len(large)
            assert content.address.hex_digest == hashlib.sha256(large).hexdigest()

    def test_releases_the_buffer_on_exit(self) -> None:
        with spool_and_hash(BytesIO(CONTENT), max_bytes=1024) as content:
            handle: IO[bytes] = content.handle

        assert handle.closed is True

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(DocumentRejectedError) as error, spool_and_hash(
            BytesIO(b""), max_bytes=1024
        ):
            pass

        assert error.value.reason is RejectionReason.EMPTY

    def test_rejects_content_over_the_limit(self) -> None:
        with pytest.raises(DocumentRejectedError) as error, spool_and_hash(
            BytesIO(CONTENT), max_bytes=5
        ):
            pass

        assert error.value.reason is RejectionReason.TOO_LARGE

    def test_stops_reading_once_the_limit_is_crossed(self) -> None:
        """A limit checked after the fact is not a limit."""
        source = CountingStream(b"y" * (4 * 1024 * 1024))

        with pytest.raises(DocumentRejectedError), spool_and_hash(source, max_bytes=1024):
            pass

        assert source.bytes_read < 4 * 1024 * 1024
