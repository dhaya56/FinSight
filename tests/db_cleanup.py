"""Removing the rows an end-to-end test created, in dependency order.

Three integration modules needed this and each kept its own copy, which drifted:
when Phase 6 added ``source_tables`` the extraction pipeline's cleanup learned
about it and the corpus pipeline's did not. The failure mode is not a failing
assertion — it is a *teardown* error on an unrelated test, several commits after
the change that caused it.

So the order lives here once. Adding a table that references ``document_versions``
means adding one line to :data:`CLEANUP`, and every suite that uses it stays
correct.

**Pointers are cleared first.** ``document_versions`` references
``extraction_runs`` and ``generations``, and both reference ``document_versions``
back, so nothing can be deleted while a pointer still stands.

Stored objects are deliberately *not* removed. The object-store port has no
delete, and content-addressed objects converge rather than accumulate.
"""

from typing import Final

CLEANUP: Final = """
    UPDATE document_versions
    SET current_extraction_run_id = NULL, active_generation_id = NULL
    WHERE content_hash = ANY(:hashes);

    DELETE FROM source_tables WHERE source_element_id IN (
        SELECT id FROM source_elements WHERE extraction_run_id IN (
            SELECT id FROM extraction_runs WHERE document_version_id IN (
                SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
            )
        )
    );

    DELETE FROM source_table_cells WHERE source_element_id IN (
        SELECT id FROM source_elements WHERE extraction_run_id IN (
            SELECT id FROM extraction_runs WHERE document_version_id IN (
                SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
            )
        )
    );

    DELETE FROM source_elements WHERE extraction_run_id IN (
        SELECT id FROM extraction_runs WHERE document_version_id IN (
            SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
        )
    );

    DELETE FROM generations WHERE document_version_id IN (
        SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
    );

    DELETE FROM extraction_runs WHERE document_version_id IN (
        SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
    );

    DELETE FROM document_metadata WHERE document_version_id IN (
        SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
    );

    DELETE FROM document_versions WHERE content_hash = ANY(:hashes);

    DELETE FROM documents
    WHERE id NOT IN (SELECT document_id FROM document_versions);
"""


def statements() -> list[str]:
    """The cleanup as individual statements, since psycopg sends one at a time."""
    return [line.strip() for line in CLEANUP.split(";") if line.strip()]
