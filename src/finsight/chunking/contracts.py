"""What a chunk is, before anything stores or embeds one.

A chunk is a *retrieval* representation (§14.2): text assembled so that search can
find it, carrying deterministic context so a reader can place it. It is never
evidence. §14.1 keeps that role with the source representation alone, and §14.9
requires a citation to resolve to the source regions a chunk was built from — which
is why :attr:`Chunk.source_element_ids` is not optional bookkeeping but the thing
that makes a chunk usable at all.

Nothing here imports a database, a tokenizer or a model. The chunker is a pure
function over extracted elements so that its guarantees — every block placed
exactly once, text preserved verbatim — can be asserted without infrastructure.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID


class EvidenceType(StrEnum):
    """Whether a chunk came from running text or from inside a detected table.

    §18.4 requires narrative and table-derived content to "remain
    distinguishable", and §20.5 allocates retrieval candidates per type so one
    cannot crowd out the other.

    **This marking is incomplete, and knowing why matters.** It is derived from
    overlap with a region the *detector* found, and ADR-003 records that the
    detector bounds 2 of 6 real pages correctly. Text from a table the detector
    missed is marked ``NARRATIVE`` because nothing here can tell the difference.
    So ``NARRATIVE`` means "not known to be table-derived", never "known to be
    prose".
    """

    NARRATIVE = "narrative"
    TABLE_DERIVED = "table_derived"


class ChunkRole(StrEnum):
    """§18.5: focused children for matching, broader parents for interpretation."""

    CHILD = "child"
    PARENT = "parent"


@dataclass(frozen=True, slots=True)
class SourceBlock:
    """One persisted narrative block, as the chunker consumes it.

    Not ``ExtractedElement``: that type exists *before* persistence and carries no
    identifier, while §14.7 requires every chunk to name the source regions it was
    built from. A chunk can only do that once the regions have identities, so
    chunking reads what was stored rather than what was produced.

    ``table_derived`` is decided by the caller, which is the layer that still has
    page geometry. Keeping it a flag here leaves the chunker free of coordinates.
    """

    element_id: UUID
    text: str
    page_number: int
    ordinal: int
    """Position in document order across the whole document, not within a page."""

    table_derived: bool = False


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Sizes the chunker runs with.

    **Every value here is unmeasured.** §18.12 requires child size, parent size,
    overlap, context budget and neighbour count to be "selected on development
    data", and no such comparison has been run. They are starting points chosen to
    be reasonable, recorded with a version so that a chunk can say which numbers
    produced it (§18.10), and they are not presented as chosen.

    ``version`` is stored on every chunk. Changing any number below without
    changing it would leave two incompatible chunk populations indistinguishable
    in the same index.
    """

    version: str = "1"

    child_max_tokens: int = 384
    """Upper bound for a child chunk, and the point at which a single block is
    split internally (§18.3). Unmeasured.

    **One number, deliberately.** An earlier version had a separate threshold for
    splitting an oversized block, and the two could disagree: a block larger than
    the child budget but smaller than the split threshold produced a chunk well
    over budget, which an embedding model would silently truncate. With the
    defaults equal the gap was invisible. Keeping a single bound makes the state
    unreachable rather than merely unlikely.
    """

    child_min_tokens: int = 48
    """Below this a chunk is merged forward rather than emitted alone.

    Blocks in this corpus are tiny — median 38 characters, and 52% under 40 — so
    without a floor the index would fill with fragments like "Sl. No." that match
    everything and mean nothing.
    """

    parent_max_tokens: int = 1536
    """Upper bound for a parent. Unmeasured."""

    heading_max_chars: int = 120
    """Longer than this is prose, whatever it starts with.

    The heading rule is deliberately biased to precision: missing a heading only
    makes a section longer, while inventing one splits a section in the wrong
    place and attaches a wrong heading path to everything after it.
    """

    table_overlap: float = 0.5
    """Share of a block's area inside a table region before it counts as
    table-derived. Unmeasured."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One unit of retrieval text, and the source it was built from."""

    text: str
    """The chunk as it will be embedded and lexically indexed.

    Built by joining the verbatim text of its source blocks. The chunker never
    rewrites, normalises or summarises — §14.4 forbids presenting enriched text as
    evidence, and the simplest way to honour that is to not enrich the body at all.
    Deterministic context is carried in the fields below and prepended only when a
    consumer asks for it, so the stored text and the source text stay comparable.
    """

    source_element_ids: tuple[UUID, ...]
    """The blocks this chunk was built from, in order (§14.7, §18.9).

    Empty is not a valid chunk. A chunk that cannot name its sources cannot be
    cited, and §14.9 makes a citation that resolves to nothing worse than no
    answer.
    """

    page_numbers: tuple[int, ...]
    """Pages the chunk spans, ascending. Usually one; prose crosses pages."""

    heading_path: tuple[str, ...] = ()
    """Section titles from outermost inward (§18.2), as the document wrote them.

    Empty where no heading was recognised, which the heading rule's bias toward
    precision makes common. Empty means "no heading found above this", never
    "this chunk belongs to no section".
    """

    evidence_type: EvidenceType = EvidenceType.NARRATIVE
    role: ChunkRole = ChunkRole.CHILD

    parent_index: int | None = None
    """Index of this chunk's parent within the same chunking result (§18.5).

    An index rather than an identifier because chunks have no identity until they
    are persisted, and inventing one here would mean the chunker decided something
    the database owns.
    """

    token_count: int = 0
    char_count: int = 0
    ordinal: int = 0
    """Position in document order, so reading order survives into retrieval."""

    config_version: str = "1"
    """Which configuration produced this chunk (§18.10, §14.10)."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Machine-readable facts about how this chunk was formed.

    ``split_oversize_block`` records that a single block was divided, which means
    a sentence may straddle two chunks. Recorded rather than hidden, because a
    retrieval miss on a split sentence is otherwise inexplicable.
    """


SPLIT_OVERSIZE: Final = "split_oversize_block"
"""A source block exceeded the budget and was divided internally."""

JOINED_FRAGMENTS: Final = "joined_short_blocks"
"""Several sub-minimum blocks were merged to reach a usable size."""
