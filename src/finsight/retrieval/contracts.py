"""What a retrieval request constrains, and what it returns.

**The generation filter is not optional and is not the caller's to supply.** §20.2
makes generation a hard filter and §11.13 makes "active" the only state a reader may
see, so :meth:`RetrievalFilters.as_payload` takes the active generations as a
separate argument and always writes them in. A caller cannot forget it, and a caller
cannot widen it — which matters because the index legitimately holds points from
superseded and still-building generations, and serving one would mean answering from
evidence that was replaced or never finished.

Everything else is optional, deliberately. A question about one issuer's credit risk
wants an issuer filter; a question about how three filings describe the same risk
wants none. §20.2 requires filters to be *enforced*, not to be mandatory.

**No field here is a relevance signal.** A filter decides what may be returned, never
what ranks higher — §7 forbids similarity overriding scope, and mixing the two is how
a close vector escapes its filter.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from finsight.vector_index.port import FilterValue, Span

__all__ = [
    "DEGRADED_DENSE_UNAVAILABLE",
    "DEGRADED_LEXICAL_FALLBACK",
    "Candidate",
    "LexicalResult",
    "RetrievalFilters",
    "Retriever",
]

GENERATION_FIELD: Final = "generation_id"

DEGRADED_LEXICAL_FALLBACK: Final = "lexical_fallback_postgres_fts"
"""BM25 was unreachable and PostgreSQL full-text search answered instead.

§20.12 requires degradation to be *flagged* rather than silent. The two retrievers
do not score the same way — FTS has no term-frequency saturation, no length
normalisation and no IDF — so a result carrying this flag is not comparable with one
that does not, and nothing downstream may treat them as interchangeable.
"""

DEGRADED_DENSE_UNAVAILABLE: Final = "dense_unavailable"
"""The dense retriever could not be reached. Set by the fusion stage, defined here
so the two stages cannot disagree on the spelling."""


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """The §20.2 dimensions a search may be narrowed to. All optional.

    ``fiscal_year`` accepts an ``int`` or a :class:`Span`, which is the whole reason
    the field exists: ``fiscal_period`` carries the document's own words and cannot
    express "since 2023".
    """

    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    fiscal_year: int | Span | None = None
    reporting_basis: str | None = None
    evidence_type: str | None = None
    section: str | None = None
    document_version_id: UUID | None = None

    def as_payload(
        self, *, generations: Sequence[UUID]
    ) -> Mapping[str, FilterValue]:
        """The filter mapping for the index, with the generation bound added.

        ``generations`` is passed as a sequence even when it holds one member, so the
        value shape says "any of these" and an empty set says "nothing" rather than
        "everything". That distinction is the point: with no active generation there
        is nothing a reader is permitted to see.
        """
        payload: dict[str, FilterValue] = {
            GENERATION_FIELD: [str(generation) for generation in generations]
        }
        for field, value in (
            ("issuer_name", self.issuer_name),
            ("document_type", self.document_type),
            ("fiscal_period", self.fiscal_period),
            ("reporting_basis", self.reporting_basis),
            ("evidence_type", self.evidence_type),
            ("section", self.section),
        ):
            if value is not None:
                payload[field] = value
        if self.document_version_id is not None:
            payload["document_version_id"] = str(self.document_version_id)
        if self.fiscal_year is not None:
            payload["fiscal_year"] = self.fiscal_year
        return payload


@dataclass(frozen=True, slots=True)
class Candidate:
    """One retrieved chunk, with where it came from and how it ranked.

    Carries **no text**. §10.7 forbids a vector-store record becoming truth, so a
    candidate is an identifier that PostgreSQL resolves. The rank is kept beside the
    score because §20.6's fusion works on ranks, and recomputing a rank from scores
    after the fact loses ties.
    """

    chunk_id: UUID
    score: float
    rank: int
    retriever: str


@dataclass(frozen=True, slots=True)
class LexicalResult:
    """Candidates from the lexical path, and whether it was the intended one."""

    candidates: tuple[Candidate, ...]
    retriever: str
    degraded: tuple[str, ...] = ()

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)


class Retriever:
    """Names for the retrievers, so a flag and a candidate agree on spelling."""

    BM25: Final = "bm25"
    POSTGRES_FTS: Final = "postgres_fts"
    DENSE: Final = "dense"
