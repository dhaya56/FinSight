"""Content-addressed object keys.

A key is derived from the hash of the bytes it stores and from nothing else — no
document identifier, version, generation, or filename. That is what keeps the
scheme permanent: re-processing, re-chunking, and re-indexing never move an
object, and migrating to another backend is a byte-for-byte copy.

The algorithm is part of the key so a future hash change is additive rather than
a rewrite of every stored path, and the two-level fan-out keeps directory sizes
sane on filesystem-backed stores.

Layout::

    originals/sha256/ab/cd/abcd...ef

Keys never incorporate caller-supplied text, so no key can traverse or escape a
prefix (PROJECT_BLUEPRINT.md §30.9).
"""

import re
from typing import Final

HASH_ALGORITHM: Final = "sha256"
"""The digest used for content addressing. Recorded alongside every stored key."""

ORIGINALS_PREFIX: Final = "originals"
"""Prefix for immutable source objects. Other object classes get their own."""

_SHA256_HEX: Final = re.compile(r"\A[0-9a-f]{64}\Z")


def original_object_key(sha256_hex: str) -> str:
    """Return the key under which an original document version is stored.

    Raises:
        ValueError: the digest is not 64 lowercase hexadecimal characters.
    """
    if not _SHA256_HEX.match(sha256_hex):
        raise ValueError("expected a 64-character lowercase hexadecimal sha256 digest")
    return f"{ORIGINALS_PREFIX}/{HASH_ALGORITHM}/{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}"
