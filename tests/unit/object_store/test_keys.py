"""Tests for content-addressed object keys."""

import pytest

from finsight.object_store.keys import HASH_ALGORITHM, ORIGINALS_PREFIX, original_object_key

DIGEST = "abcdef0123456789" * 4  # 64 lowercase hexadecimal characters


class TestOriginalObjectKey:
    def test_layout_is_prefix_algorithm_fanout_digest(self) -> None:
        assert original_object_key(DIGEST) == f"originals/sha256/ab/cd/{DIGEST}"

    def test_carries_the_algorithm_so_a_future_change_is_additive(self) -> None:
        key = original_object_key(DIGEST)

        assert key.startswith(f"{ORIGINALS_PREFIX}/{HASH_ALGORITHM}/")

    def test_is_deterministic(self) -> None:
        assert original_object_key(DIGEST) == original_object_key(DIGEST)

    def test_depends_on_nothing_but_the_digest(self) -> None:
        """No document, version, generation, or filename may enter the key."""
        other = "f" * 64

        assert original_object_key(DIGEST) != original_object_key(other)

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "abc",
            "A" * 64,
            "g" * 64,
            DIGEST + "0",
            f"../../{DIGEST}",
            DIGEST.upper(),
        ],
    )
    def test_rejects_anything_that_is_not_a_lowercase_sha256_digest(
        self,
        invalid: str,
    ) -> None:
        """Keys never incorporate caller-supplied text, so traversal is impossible."""
        with pytest.raises(ValueError, match="sha256"):
            original_object_key(invalid)
