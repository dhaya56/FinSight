"""Content addressing.

The source-byte hash identifies a document version (PROJECT_BLUEPRINT.md §11.5).
This module owns the algorithm and the shape of a digest, so that storage keys,
database rows, and provenance all agree on one definition rather than repeating
a literal in three places.
"""

import re
from dataclasses import dataclass
from typing import Final

HASH_ALGORITHM: Final = "sha256"
"""The digest used for content addressing.

Recorded alongside every stored version so a future change is additive: rows and
keys both carry the algorithm, so a second algorithm can coexist with the first
rather than requiring a rewrite.
"""

_SHA256_HEX: Final = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ContentAddress:
    """A validated digest of a byte sequence."""

    algorithm: str
    hex_digest: str

    def __post_init__(self) -> None:
        if self.algorithm != HASH_ALGORITHM:
            raise ValueError(f"unsupported hash algorithm: {self.algorithm}")
        if not _SHA256_HEX.match(self.hex_digest):
            raise ValueError("expected a 64-character lowercase hexadecimal sha256 digest")

    @classmethod
    def sha256(cls, hex_digest: str) -> "ContentAddress":
        """Build an address from a hexadecimal SHA-256 digest."""
        return cls(algorithm=HASH_ALGORITHM, hex_digest=hex_digest)
