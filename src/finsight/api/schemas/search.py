"""Request and response contracts for the retrieval route.

**These mirror the pipeline's dataclasses rather than reusing them.** The domain
types are frozen dataclasses deliberately kept free of FastAPI (CLAUDE.md §11), and
a wire contract has a different job from an internal one: it is versioned, it is
public, and adding a field to it is a compatibility event. Mapping between them
once, here, is what keeps the pipeline from acquiring a serialization concern.

**Every score the pipeline produced is carried through** (§20.13). A response that
holds only the final order cannot answer "why is this first", which is the question
the trace exists to answer and the question a reviewer asks.

**No field here is a correctness signal.** ``rerank_score`` is a cross-encoder logit
and ``fused_score`` is a sum of reciprocal ranks; §27 forbids presenting either as a
probability that the passage is right, and neither is one.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CitationModel",
    "RetrievedChunkModel",
    "SearchRequest",
    "SearchResponse",
]

_FORBID_EXTRA = ConfigDict(extra="forbid")
"""Reject unknown request fields rather than ignoring them.

A misspelled filter that is silently dropped widens the search instead of narrowing
it, and returns plausible results from outside the scope the caller asked for — a
§20.2 violation that looks exactly like a working query.
"""


class CitationModel(BaseModel):
    """One source region a passage was built from (§14.7, §14.9)."""

    source_element_id: UUID = Field(
        description="The source element in PostgreSQL, which remains authoritative."
    )
    locator: str = Field(description="Human-readable position, such as a page.")
    position: int = Field(description="Order of this region within the passage.")


class RetrievedChunkModel(BaseModel):
    """One ranked passage, with its provenance and every score behind it."""

    chunk_id: UUID
    rank: int = Field(ge=1, description="Final position, dense and starting at one.")
    text: str = Field(
        description=(
            "The chunk body verbatim as extracted. Not a summary and not enriched "
            "retrieval text: §7 forbids citing either as source evidence."
        )
    )
    heading_path: tuple[str, ...] = Field(
        description="Section headings above this passage, outermost first."
    )
    page_numbers: tuple[int, ...] = Field(
        description="Pages the passage covers, as printed in the source document."
    )
    evidence_type: str
    issuer_name: str | None = Field(
        description="Null where document metadata extraction has not run."
    )
    fiscal_period: str | None = Field(
        description="The document's own words for the period, not a normalised value."
    )
    fused_score: float = Field(description="Reciprocal-rank-fusion score (§20.6).")
    rerank_score: float | None = Field(
        default=None,
        description=(
            "Cross-encoder logit. Null when the reranker did not run. Unbounded and "
            "frequently negative, and not a probability (§27)."
        ),
    )
    contributions: dict[str, int] = Field(
        default_factory=dict,
        description="Retriever name to the rank it placed this chunk at.",
    )
    citations: tuple[CitationModel, ...] = Field(
        default=(),
        description="Source regions behind the passage. Empty means uncitable.",
    )


class SearchRequest(BaseModel):
    """A question and the §20.2 filters it is scoped by."""

    model_config = _FORBID_EXTRA

    query: Annotated[str, Field(min_length=1, max_length=2000)] = Field(
        description="The question, as typed."
    )
    limit: Annotated[int, Field(ge=1, le=50)] = Field(
        default=5, description="Passages to return. Bounded; depth is configured."
    )

    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    fiscal_year: int | None = None
    reporting_basis: str | None = None
    evidence_type: str | None = None
    section: str | None = None

    rerank: bool = Field(
        default=True,
        description=(
            "False returns the fused order instead. Measured on this host: a "
            "reranked query is about 2,273 ms against 175 ms without, so reranking "
            "is roughly 93% of the latency. Switching it off trades ordering quality "
            "— itself unmeasured, there being no golden set — for interactivity. "
            "True does not force reranking on when the server has it disabled."
        ),
    )


class SearchResponse(BaseModel):
    """Ranked passages and the honest state of the pipeline that produced them."""

    query: str = Field(description="Echoed back, so a stored response is self-contained.")
    candidates: tuple[RetrievedChunkModel, ...]

    degraded: tuple[str, ...] = Field(
        default=(),
        description=(
            "Degradation flags (§20.12, §23.8). Non-empty means this result is not "
            "comparable with an undegraded one and must not be presented as though "
            "it were."
        ),
    )
    depth: int = Field(description="Candidates retrieved before narrowing (§23.5).")
    reranked: bool
    reranker_model: str | None = None
    lexical_retriever: str = Field(
        description="Which lexical path answered: bm25, or postgres_fts on fallback."
    )
    dense_used: bool
    fusion_version: str
    collapsed_count: int = Field(
        default=0,
        description=(
            "Candidates removed as duplicate evidence (§20.9). Reported so that "
            "'why are there four results when I asked for five' is answerable."
        ),
    )
    elapsed_ms: int = Field(
        description="Server-side wall time for the whole pipeline, measured per call."
    )
    timings_ms: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Wall time per stage — lexical, dense, fusion, resolve, rerank — "
            "**measured, not apportioned**. The trace surface previously split one total "
            "across stages using ratios recorded in ENV-010, which were real on the query "
            "they came from and a guess on every other."
        ),
    )

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)
