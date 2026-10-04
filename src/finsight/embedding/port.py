"""The embedding boundary: a protocol, its errors, and nothing else.

No module here imports an HTTP client or a model runtime. That rule is what made
the object-store backend replaceable in six files (ADR-001) and it applies with
more force to an embedding model, which §22 explicitly leaves unselected: Nomic,
BGE-M3 and BGE-large are candidates, and §22.10 says to "select the smallest model
that provides acceptable retrieval quality and operational behavior" once there is
evidence.

**Documents and queries are separate methods, deliberately.** Nomic requires task
prefixes — ``search_document:`` when indexing, ``search_query:`` when searching —
and measured against the running model Ollama does **not** apply them: the prefixed
and unprefixed forms of one sentence differ at cosine 0.9388. A single boolean flag
would make it possible to index with one prefix and search with the other, which
degrades retrieval silently and looks exactly like a poor model. Two methods make
the asymmetry impossible to get wrong, and a model that needs no prefixes simply
implements both the same way.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

Vector = tuple[float, ...]


class EmbeddingError(RuntimeError):
    """Base class for failures at the embedding boundary."""


class EmbeddingUnavailableError(EmbeddingError):
    """The model could not be reached, or did not answer in time.

    Distinct from a shape error because the responses differ: this one is
    retryable and leaves the outbox event pending, while a shape error means the
    configuration is wrong and retrying will fail identically.
    """


class EmbeddingShapeError(EmbeddingError):
    """The model returned vectors of an unexpected size or count.

    Raised rather than tolerated. A collection is created with a fixed
    dimensionality, so a model that silently changed — a different tag behind the
    same name, a truncated output — would produce vectors the vector store either
    rejects or, worse, accepts into a differently-shaped space where every
    distance is meaningless.
    """


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. Structural, so adapters do not subclass it."""

    @property
    def model(self) -> str:
        """The model identifier, recorded per generation (§14.10).

        Without it two chunk populations embedded by different models are
        indistinguishable in one collection, and every distance between them is
        nonsense.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Vector length, which fixes the shape of the collection."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed text that is being indexed.

        Batched because the cost is dominated by the round trip, not the model.
        Returns vectors in the order given — a caller pairs them with chunk ids by
        position, so a reordering would attach every vector to the wrong chunk.

        Raises:
            EmbeddingUnavailableError: the model could not be reached.
            EmbeddingShapeError: the response did not match the expected shape.
        """
        ...

    def embed_query(self, text: str) -> Vector:
        """Embed text that is being searched with.

        Separate from :meth:`embed_documents` because the two are not
        interchangeable for an asymmetric model. See the module docstring.
        """
        ...
