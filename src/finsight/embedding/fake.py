"""A deterministic embedder, for tests and for CI where Ollama does not exist.

Not a mock. It produces real unit vectors from a hash of the text, so the same
text always embeds identically and different texts almost never collide. That is
enough to exercise everything around the model — batching, pairing vectors with
chunks, outbox progress, collection shape — without a model.

**It carries no semantics, and that is the point.** Similar sentences do not get
similar vectors, so no test using it can accidentally assert that retrieval is
*good*. Quality belongs to §22's recorded comparison against real queries, and a
fake that looked semantic would invite exactly the claim this project must not
make.
"""

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Final

from finsight.embedding.port import Vector

MODEL: Final = "deterministic-fake"


class FakeEmbedder:
    """Hash-derived unit vectors of a fixed width."""

    def __init__(self, *, dimensions: int = 768) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return MODEL

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return tuple(self._vector(f"document:{text}") for text in texts)

    def embed_query(self, text: str) -> Vector:
        return self._vector(f"query:{text}")

    def _vector(self, text: str) -> Vector:
        """Expand a digest to the required width, then scale to unit length.

        SHA-256 gives 32 bytes and a vector needs far more, so the digest is
        re-hashed with a counter until enough bytes exist. Deterministic across
        processes and platforms, which a hash of the string object would not be.
        """
        needed = self._dimensions * 4
        material = b""
        counter = 0
        seed = text.encode("utf-8")
        while len(material) < needed:
            material += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            counter += 1

        values = [
            struct.unpack_from(">i", material, index * 4)[0] / 2_147_483_648.0
            for index in range(self._dimensions)
        ]
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:
            return tuple(values)
        return tuple(value / norm for value in values)
