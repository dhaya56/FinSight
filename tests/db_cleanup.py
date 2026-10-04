"""Removing the rows an end-to-end test created, in dependency order.

Three integration modules needed this and each kept its own copy, which drifted:
when Phase 6 added ``source_tables`` the extraction pipeline's cleanup learned
about it and the corpus pipeline's did not. The failure mode is not a failing
assertion — it is a *teardown* error on an unrelated test, several commits after
the change that caused it.

So the order lives here once. Adding a table that references ``document_versions``
means adding one block, and every suite that uses it stays correct.

**The order is a topological sort of the foreign keys, not a narrative one.**
Deleting the referencing row before the referenced one is the whole requirement,
and getting it wrong is easy: ``chunk_sources`` points at both ``chunks`` *and*
``source_elements``, so it has to go before either. An earlier arrangement here put
it after ``source_elements`` and would have failed the first time a test both
chunked a document and cleaned up by content hash.

**Pointers are cleared first.** ``document_versions`` references
``extraction_runs`` and ``generations``, and both reference ``document_versions``
back, so nothing can be deleted while a pointer still stands. ``chunks.parent_id``
is self-referential for the same reason.

Stored objects are deliberately *not* removed. The object-store port has no
delete, and content-addressed objects converge rather than accumulate.
"""

from typing import Final

_VERSIONS: Final = (
    "SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)"
)
_CHUNKS: Final = f"SELECT id FROM chunks WHERE document_version_id IN ({_VERSIONS})"
_RUNS: Final = (
    f"SELECT id FROM extraction_runs WHERE document_version_id IN ({_VERSIONS})"
)
_ELEMENTS: Final = f"SELECT id FROM source_elements WHERE extraction_run_id IN ({_RUNS})"

CLEANUP: Final = f"""
    UPDATE document_versions
    SET current_extraction_run_id = NULL, active_generation_id = NULL
    WHERE content_hash = ANY(:hashes);

    UPDATE chunks SET parent_id = NULL WHERE id IN ({_CHUNKS});

    DELETE FROM index_outbox WHERE chunk_id IN ({_CHUNKS});

    DELETE FROM chunk_sources WHERE chunk_id IN ({_CHUNKS});

    DELETE FROM chunks WHERE document_version_id IN ({_VERSIONS});

    DELETE FROM source_tables WHERE source_element_id IN ({_ELEMENTS});

    DELETE FROM source_table_cells WHERE source_element_id IN ({_ELEMENTS});

    DELETE FROM source_elements WHERE extraction_run_id IN ({_RUNS});

    DELETE FROM generations WHERE document_version_id IN ({_VERSIONS});

    DELETE FROM extraction_runs WHERE document_version_id IN ({_VERSIONS});

    DELETE FROM document_metadata WHERE document_version_id IN ({_VERSIONS});

    DELETE FROM document_versions WHERE content_hash = ANY(:hashes);

    DELETE FROM documents
    WHERE id NOT IN (SELECT document_id FROM document_versions);
"""


def statements() -> list[str]:
    """The cleanup as individual statements, since psycopg sends one at a time."""
    return [line.strip() for line in CLEANUP.split(";") if line.strip()]
