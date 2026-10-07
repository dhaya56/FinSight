"""The values each §20.2 filter can usefully take.

**A filter a reader has to guess is not a usable filter.** Issuer was a free-text box, so
"Infosys" matched nothing while "Infosys Limited" matched, and the difference showed up as an
empty result that reads like "the corpus holds nothing about this" rather than "that is not how
the name is spelled". §7 makes these filters hard — they are never relaxed to find more results
— which is exactly why offering the wrong spelling is so quiet a failure.

Every value here is drawn from what the corpus actually holds, and sections from **active
generations only**: a section that exists solely in a superseded generation is unreachable, so
offering it would hand a reader a filter guaranteed to return nothing.
"""

from pydantic import BaseModel, Field

__all__ = ["FacetsResponse"]


class FacetsResponse(BaseModel):
    """Selectable values for the retrieval filters."""

    issuer_names: tuple[str, ...] = Field(
        default=(),
        description="Issuers exactly as recorded. The filter matches the full string.",
    )
    document_types: tuple[str, ...] = Field(
        default=(), description="annual_report, drhp, form_10k, as recorded."
    )
    fiscal_periods: tuple[str, ...] = Field(
        default=(),
        description="The document's own words for its period, not a normalised value.",
    )
    reporting_bases: tuple[str, ...] = Field(
        default=(), description="consolidated, standalone, both or undetermined (§16.9)."
    )
    sections: tuple[str, ...] = Field(
        default=(),
        description=(
            "Top-level headings from active generations. Bounded: a corpus with thousands "
            "of distinct headings needs a search box rather than a longer list."
        ),
    )
