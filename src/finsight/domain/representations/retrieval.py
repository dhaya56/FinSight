"""The retrieval representation as the domain sees it (§14.2).

Separate from the source representation on purpose. §14.4 requires source,
retrieval and canonical-fact representations to be "independently stored and
addressable", and §14.1 keeps citability with the source alone — a chunk is how
search *finds* evidence, never the evidence itself.

These two enums live here rather than beside the chunker because three layers must
agree on them: the chunker that assigns them, the CHECK constraints that store
them, and the retrieval filters that select on them. ``ElementType`` is in the
domain for the same reason.
"""

from enum import StrEnum


class EvidenceType(StrEnum):
    """Whether a chunk came from running text or from inside a detected table.

    §18.4 requires narrative and table-derived content to "remain
    distinguishable", and §20.5 allocates retrieval candidates per type so that
    one cannot crowd out the other.

    **``NARRATIVE`` means "not known to be table-derived", never "known to be
    prose".** The marking is derived from overlap with a region the *detector*
    found, and ADR-003 records that detector bounding 2 of 6 real pages correctly.
    Text from a table it missed arrives here as narrative, because nothing at this
    layer can tell the difference.
    """

    NARRATIVE = "narrative"
    TABLE_DERIVED = "table_derived"


class ChunkRole(StrEnum):
    """§18.5: focused children for matching, broader parents for interpretation."""

    CHILD = "child"
    PARENT = "parent"
