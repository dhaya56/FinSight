"""The corpus route: what has been ingested, and what of it is queryable.

**Authenticated like every other non-health route** (§28.2). Issuer names and periods are
document-derived, and the list of what an organisation holds is not public.

**Defined with ``def``, not ``async def``**, because the repository call is synchronous; see
:mod:`finsight.api.routes.search` for the full reasoning.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from finsight.api.auth import require_token
from finsight.api.dependencies import SessionScope, get_session_scope
from finsight.api.schemas.library import LibraryEntryModel, LibraryResponse
from finsight.persistence.repositories.library import LibraryEntry, LibraryRepository

router = APIRouter(
    prefix="/v1",
    tags=["corpus"],
    dependencies=[Depends(require_token)],
)


@router.get(
    "/library",
    response_model=LibraryResponse,
    summary="Ingested documents and the generation of each that is queryable",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        503: {"description": "Authentication is not configured."},
    },
)
def library(
    scope: Annotated[SessionScope, Depends(get_session_scope)],
) -> LibraryResponse:
    """Every ingested document version, newest first.

    An empty list is a real answer and means nothing has been ingested — not that the
    route is unavailable.
    """
    with scope() as session:
        entries = LibraryRepository(session).entries()

    return LibraryResponse(documents=tuple(_as_model(entry) for entry in entries))


def _as_model(entry: LibraryEntry) -> LibraryEntryModel:
    """Project one row onto its wire shape."""
    return LibraryEntryModel(
        document_version_id=entry.document_version_id,
        issuer_name=entry.issuer_name,
        document_type=entry.document_type,
        fiscal_period=entry.fiscal_period,
        reporting_basis=entry.reporting_basis,
        generation_state=entry.generation_state,
        chunking_config_version=entry.chunking_config_version,
        activated_at=entry.activated_at,
        ingested_at=entry.ingested_at,
        pages=entry.pages,
        blocks=entry.blocks,
        tables=entry.tables,
        footnotes=entry.footnotes,
        chunks=entry.chunks,
        byte_size=entry.byte_size,
        extraction_state=entry.extraction_state,
        extraction_config_version=entry.extraction_config_version,
        extraction_seconds=entry.extraction_seconds,
        unreadable_regions=entry.unreadable_regions,
        tables_accepted=entry.tables_accepted,
        tables_rejected=entry.tables_rejected,
        child_chunks=entry.child_chunks,
        parent_chunks=entry.parent_chunks,
        median_child_tokens=entry.median_child_tokens,
        sections=entry.sections,
    )
