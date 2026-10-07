"""The filter-values route: what a reader is allowed to narrow by.

**Authenticated like every other non-health route** (§28.2). The values are issuer names and
section headings drawn from filings, which is document-derived content and not public.

**Defined with ``def``, not ``async def``**, because the repository calls are synchronous; see
:mod:`finsight.api.routes.search` for the full reasoning.

This exists because a hard filter a reader has to spell correctly is a trap. §7 forbids
similarity overriding scope, so a filter is never relaxed to find more results — and a
misspelled issuer therefore returns nothing at all, which reads as an empty corpus rather than
a typo. Offering the values removes the guess.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from finsight.api.auth import require_token
from finsight.api.dependencies import SessionScope, get_session_scope
from finsight.api.schemas.facets import FacetsResponse
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.document_metadata import (
    DocumentMetadataRepository,
)

router = APIRouter(
    prefix="/v1",
    tags=["retrieval"],
    dependencies=[Depends(require_token)],
)


@router.get(
    "/facets",
    response_model=FacetsResponse,
    summary="Selectable values for the retrieval filters",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        503: {"description": "Authentication is not configured."},
    },
)
def facets(
    scope: Annotated[SessionScope, Depends(get_session_scope)],
) -> FacetsResponse:
    """Every value the §20.2 filters can usefully take.

    One session for both reads. An empty list is a real answer and means nothing has been
    ingested that records that field — not that the filter does not exist.
    """
    with scope() as session:
        values = DocumentMetadataRepository(session).filter_values()
        sections = ChunkRepository(session).top_level_sections()

    return FacetsResponse(
        issuer_names=tuple(values.get("issuer_name", ())),
        document_types=tuple(values.get("document_type", ())),
        fiscal_periods=tuple(values.get("fiscal_period", ())),
        reporting_bases=tuple(values.get("reporting_basis", ())),
        sections=tuple(sections),
    )
