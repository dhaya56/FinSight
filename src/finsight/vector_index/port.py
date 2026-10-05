"""The vector-index boundary: a protocol, its errors, and nothing else.

§29.2 makes this store derived and rebuildable — "Qdrant stores vectors and filter
payloads that can be rebuilt from PostgreSQL" — and §10.7 is blunter: "No
vector-store record can become financial truth." The port exists so that stays
true structurally rather than by discipline: nothing here returns text, and a
search answers with chunk identifiers and scores that the caller resolves against
PostgreSQL.

**Dense and sparse live together.** §20.4 covers dense retrieval and §20.3 the
lexical path; ADR-005 makes BM25 the primary lexical route, carried as sparse
vectors in the same collection. One collection means one set of filter payloads
and one place for §20.2's hard filters to apply, which is what stops a filter
being enforced on one retriever and forgotten on the other.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Span:
    """An inclusive numeric range for a filter value, open at either end.

    The one §20.2 shape a single value cannot express. "The last three years" is a
    range over ``fiscal_year``, and ``fiscal_period`` — the document's own words, two
    development filings sharing the string "FY2024-25" — cannot carry it.

    Separate from a plain tuple because a tuple already means "any of these", and the
    two are not interchangeable: ``(2023, 2025)`` as any-of excludes 2024, while as a
    span it includes it. Reading one as the other is a silent wrong answer, so the
    types differ.
    """

    low: int | None = None
    high: int | None = None

    def __post_init__(self) -> None:
        if self.low is None and self.high is None:
            raise ValueError("a Span needs at least one bound")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"span {self.low}..{self.high} is empty")


FilterValue = str | int | bool | Span | Sequence[str] | Sequence[int]
"""What a filter field may be matched against.

A scalar is an exact match, a sequence is *any of*, and a :class:`Span` is a range.
Every field is combined with AND — §20.2's filters are hard, and §7 forbids semantic
similarity overriding them, so there is no OR across fields.
"""


class VectorIndexError(RuntimeError):
    """Base class for failures at the vector-index boundary."""


class VectorIndexUnavailableError(VectorIndexError):
    """The index could not be reached.

    Separate because §20.12 and §10.9 require this to be survivable: dense
    retrieval degrades to the lexical path with a flag, rather than failing the
    query. A caller distinguishes "Qdrant is down" from "Qdrant rejected this".
    """


class VectorIndexShapeError(VectorIndexError):
    """The index disagrees with the shape it is being asked to store.

    Almost always a dimensionality mismatch, which means the embedding model
    changed without the collection changing. Raised rather than tolerated: the
    alternative is a collection holding two vector spaces where every distance
    between them is meaningless and nothing looks wrong.
    """


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """One chunk as the index holds it: vectors, and the fields filters use.

    Deliberately carries **no text**. §14.1 keeps citable content in the source
    representation and §10.7 forbids a vector-store record becoming truth, so a
    search returns identifiers that PostgreSQL resolves. A payload carrying text
    would make it possible to answer from the derived store without ever reading
    the authoritative one.
    """

    chunk_id: UUID
    dense: tuple[float, ...]

    sparse_indices: tuple[int, ...] = ()
    sparse_values: tuple[float, ...] = ()
    """BM25 term weights as a sparse vector (ADR-005).

    Empty when the chunk produced no indexable terms — a cell of punctuation, a
    row of symbols — which is a real outcome rather than an error. Such a chunk
    remains dense-searchable.
    """

    payload: Mapping[str, object] = field(default_factory=dict)
    """The §20.2 filter fields: generation, document version, issuer, period,
    basis, evidence type. Scalars only, because they are filtered on, not read."""


@dataclass(frozen=True, slots=True)
class Match:
    """One retrieved chunk identifier and the score that retrieved it."""

    chunk_id: UUID
    score: float


@runtime_checkable
class VectorIndex(Protocol):
    """Stores and searches chunk vectors. Structural, so adapters do not subclass."""

    @property
    def collection(self) -> str:
        """Which collection this instance writes to.

        Exposed because the name encodes the embedding model and width: mixing
        two vector spaces in one collection is the failure this names away.
        """
        ...

    def ensure_collection(self) -> None:
        """Create the collection if absent, and verify it if present.

        Verification is the point. A collection that already exists with a
        different width or a missing sparse configuration would accept writes and
        return nonsense, so a mismatch raises instead of being adopted.
        """
        ...

    def upsert(self, chunks: Sequence[IndexedChunk]) -> int:
        """Write or overwrite chunks, returning how many were sent.

        Idempotent by construction: point identifiers are derived from the chunk
        and the embedding configuration (§29.9), so replaying an outbox event
        rewrites the same point rather than creating a second one.
        """
        ...

    def search_dense(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        filters: Mapping[str, object] | None = None,
    ) -> tuple[Match, ...]:
        """Nearest neighbours under the §20.2 hard filters."""
        ...

    def search_sparse(
        self,
        indices: Sequence[int],
        values: Sequence[float],
        *,
        limit: int,
        filters: Mapping[str, object] | None = None,
    ) -> tuple[Match, ...]:
        """BM25 matches under the same filters, scored with server-side IDF."""
        ...

    def count(self, *, filters: Mapping[str, object] | None = None) -> int:
        """How many points match, for reconciliation against PostgreSQL (§29.11)."""
        ...
