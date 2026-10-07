"""The corpus listing: what has been ingested, and what of it is queryable.

**The two are not the same, and that is what this contract exists to say.** §11.12 draws the
line: a document version records what *exists*, a generation records what is *queryable*. A
version can be fully extracted and still unreachable by any query, which is the correct state
while its chunks are being built — so ``generation_state`` is a first-class field and a client
that reads only the document rows is reading the wrong half.

**No filename, no object key, no hash.** §10 keeps storage locations out of anything a reader
sees. A filing is identified by issuer, type and period, which is what a reader recognises.
"""

import datetime
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = ["LibraryEntryModel", "LibraryResponse"]


class LibraryEntryModel(BaseModel):
    """One ingested document version and the state of what was derived from it."""

    document_version_id: UUID
    issuer_name: str | None = Field(
        default=None, description="Null where document metadata was never recorded."
    )
    document_type: str | None = None
    fiscal_period: str | None = Field(
        default=None,
        description="The document's own words for the period, not a normalised value.",
    )
    reporting_basis: str | None = None

    generation_state: str | None = Field(
        default=None,
        description=(
            "active, shadow, superseded or failed. **Null means no generation exists** — "
            "the version is extracted but was never chunked, so nothing about it is "
            "queryable. Retrieval is bound to active generations alone (§20.2)."
        ),
    )
    chunking_config_version: str | None = Field(
        default=None,
        description="Which chunking configuration produced the queryable passages.",
    )
    activated_at: datetime.datetime | None = Field(
        default=None, description="When this generation became queryable (§11.13)."
    )
    ingested_at: datetime.datetime

    pages: int = 0
    blocks: int = 0
    tables: int = Field(
        default=0,
        description=(
            "Tables found, not admitted. No detector passes the bar yet, so table cells "
            "are excluded from retrieval (ADR-003)."
        ),
    )
    footnotes: int = 0
    chunks: int = Field(
        default=0,
        description="Passages in the active generation, so this is what a query can reach.",
    )
    byte_size: int

    extraction_state: str | None = Field(
        default=None,
        description=(
            "succeeded or partial. **Partial is the common case and not a failure**: a "
            "region that could not be read is recorded as a gap rather than silently "
            "dropped, so the count below is knowable at all."
        ),
    )
    extraction_config_version: str | None = None
    extraction_seconds: float | None = None

    unreadable_regions: int = Field(
        default=0,
        description=(
            "Elements carrying a failure reason instead of text. The honest counterpart "
            "to the page count: a document can be fully paginated and still hold regions "
            "nothing could read, and those are not in the index."
        ),
    )

    tables_accepted: int = 0
    tables_rejected: int = Field(
        default=0,
        description=(
            "A region the page's own ruling lines do not support. Neither this nor "
            "tables_accepted counts *retrievable* tables: ADR-003 admits no detector, so "
            "table cells are excluded from retrieval whatever the verdict."
        ),
    )

    child_chunks: int = 0
    parent_chunks: int = Field(
        default=0,
        description="§18.5's two unit sizes: children for precision, parents for context.",
    )
    median_child_tokens: int = 0

    sections: tuple[tuple[str, int], ...] = Field(
        default=(),
        description=(
            "Top-level sections with their retrievable passage counts, largest first. "
            "Counted over retrieval children in the active generation, so a section listed "
            "here is one a question can actually reach."
        ),
    )


class LibraryResponse(BaseModel):
    """Everything ingested, newest first."""

    documents: tuple[LibraryEntryModel, ...] = ()

    @property
    def queryable(self) -> int:
        """How many documents a question can currently reach."""
        return sum(1 for entry in self.documents if entry.generation_state == "active")
