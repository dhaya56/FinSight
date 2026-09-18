"""Tests for domain document identity."""

import hashlib
from uuid import uuid4

import pytest

from finsight.domain.documents import ReceivedDocument, RecordedVersion
from finsight.domain.errors import DocumentRejectedError, DomainError, RejectionReason
from finsight.domain.identifiers import HASH_ALGORITHM, ContentAddress

DIGEST = hashlib.sha256(b"content").hexdigest()


class TestContentAddress:
    def test_accepts_a_lowercase_sha256_digest(self) -> None:
        address = ContentAddress.sha256(DIGEST)

        assert address.algorithm == HASH_ALGORITHM
        assert address.hex_digest == DIGEST

    @pytest.mark.parametrize(
        "invalid",
        ["", "abc", DIGEST.upper(), DIGEST + "0", "g" * 64, f"../{DIGEST}"],
    )
    def test_rejects_anything_that_is_not_a_sha256_digest(self, invalid: str) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ContentAddress.sha256(invalid)

    def test_rejects_an_unsupported_algorithm(self) -> None:
        with pytest.raises(ValueError, match="algorithm"):
            ContentAddress(algorithm="md5", hex_digest=DIGEST)

    def test_is_immutable(self) -> None:
        address = ContentAddress.sha256(DIGEST)

        with pytest.raises(AttributeError):
            address.hex_digest = DIGEST  # type: ignore[misc]


class TestRejection:
    def test_carries_a_stable_reason_code(self) -> None:
        error = DocumentRejectedError(RejectionReason.TOO_LARGE, "exceeds 10 bytes")

        assert error.reason is RejectionReason.TOO_LARGE
        assert "too_large" in str(error)

    def test_is_a_domain_error(self) -> None:
        assert isinstance(DocumentRejectedError(RejectionReason.EMPTY), DomainError)

    def test_reason_codes_are_stable_strings(self) -> None:
        """Later phases record and count these, so the values are a contract."""
        assert RejectionReason.EMPTY.value == "empty"
        assert RejectionReason.UNSUPPORTED_CONTENT_TYPE.value == "unsupported_content_type"
        assert RejectionReason.DECLARED_TYPE_MISMATCH.value == "declared_type_mismatch"


class TestRecordedVersion:
    def test_reports_whether_the_bytes_were_already_known(self) -> None:
        recorded = RecordedVersion(
            document_id=uuid4(), version_id=uuid4(), already_existed=True
        )

        assert recorded.already_existed is True


class TestReceivedDocument:
    def test_defaults_to_a_new_document(self) -> None:
        received = ReceivedDocument(
            document_id=uuid4(),
            version_id=uuid4(),
            address=ContentAddress.sha256(DIGEST),
            byte_size=7,
            detected_content_type="application/pdf",
            object_key=f"originals/sha256/{DIGEST[:2]}/{DIGEST[2:4]}/{DIGEST}",
        )

        assert received.already_existed is False
        assert received.declared_content_type is None
        assert received.original_filename is None
