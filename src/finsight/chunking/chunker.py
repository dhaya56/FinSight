"""Assembling blocks into chunks, with nothing lost and nothing duplicated.

**Blocks are joined, not split.** That is the opposite of what most chunking
descriptions assume, and it follows from measurement: across a 369-page
development filing the median block is 38 characters and 52% are under 40. A block
is a line fragment, not a paragraph. So the dominant operation is accumulation, and
splitting is the rare path for the handful of blocks that run to thousands of
characters.

**The two guarantees this module exists to provide.** Both are asserted by
:func:`verify_coverage` and by tests over the real corpus, because a chunker that
loses a paragraph fails silently — retrieval simply never returns it, and nothing
distinguishes "no such text" from "the text was dropped":

1. **Every block holding text is placed exactly once.** No block disappears and
   none is duplicated into two chunks. Blank blocks are dropped first and are
   outside the guarantee — see :func:`chunk_blocks`, which measures them.
2. **Text is preserved verbatim.** A chunk's text contains its blocks' text
   unmodified; the chunker never rewrites, normalises or summarises (§14.4).

**Boilerplate demotion is deliberately not implemented.** §18.8 permits excluding
page furniture "when detection is reliable". Measured on the development corpus,
the most repeated block texts are not furniture at all: ``(In ₹ crore)`` appears
159 times, ``2025 2024`` 152 times, ``Particulars As at March 31,`` 58 times. The
first of those is the *units declaration*, and demoting it would hide the one value
whose loss puts every figure in the document out by a factor of ten million.
Detection is not reliable here, so nothing is demoted.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from itertools import pairwise
from typing import Final
from uuid import UUID

from finsight.chunking.contracts import (
    JOINED_FRAGMENTS,
    SPLIT_OVERSIZE,
    Chunk,
    ChunkingConfig,
    ChunkRole,
    EvidenceType,
    SourceBlock,
)
from finsight.chunking.headings import (
    HeadingStack,
    heading_level,
    suppress_list_runs,
)

TokenCounter = Callable[[str], int]

_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+(?=[A-Z(₹$])")
"""A sentence boundary for §18.3, used only when a single block is oversized.

Requires the next sentence to begin with a capital, an opening bracket or a
currency symbol, so ``Rs. 5 crore`` and ``No. 12`` do not split mid-figure. Blunt,
and only ever applied inside one block, so a mistake costs a boundary rather than
a document.
"""

_JOIN: Final = "\n"
"""How blocks are joined. A newline, because that is the boundary the document drew
and collapsing it to a space would merge a heading into the line beneath it."""


class CoverageError(RuntimeError):
    """A block was lost or duplicated. Raised rather than returned.

    There is no sensible partial result: a chunk set that silently dropped a
    paragraph would be indexed, searched and trusted.
    """


def chunk_blocks(
    blocks: Sequence[SourceBlock],
    *,
    config: ChunkingConfig | None = None,
    count_tokens: TokenCounter,
) -> tuple[Chunk, ...]:
    """Turn a document's narrative blocks into child and parent chunks.

    ``blocks`` must already be in document order and must contain only blocks —
    cells are excluded because ADR-003 refuses to index them while no detector
    bounds a table correctly, and footnotes belong to the table they annotate.

    ``count_tokens`` is injected rather than imported so the chunker stays free of
    a model dependency and can be tested with an exact, trivial counter instead of
    a tokenizer's approximations.

    **Blank blocks are dropped, and the coverage guarantee is over the rest.** A
    block whose text is only whitespace has nothing to retrieve and would produce a
    chunk the ``text_not_blank`` constraint refuses. Across the three development
    filings this discards 1,557 blocks of 40,476 — 3.8% — carrying 4,455 characters
    of 4.72 million, so an average of 2.9 whitespace characters each. Verified
    against the stored corpus: of those 1,557, **zero** hold a non-whitespace
    character.

    The distinction matters because it is the difference between a filter and a
    leak, and the test has to be this one: PostgreSQL's ``btrim`` defaults to
    trimming spaces only, so a SQL check for lost content calls a block of newlines
    non-empty and reports 1,557 losses that do not exist.
    """
    settings = config or ChunkingConfig()
    usable = [block for block in blocks if block.text.strip()]
    if not usable:
        return ()
    _require_document_order(usable)

    chunks = _assemble(usable, settings, count_tokens)
    verify_coverage(usable, chunks)
    return chunks


def _require_document_order(blocks: Sequence[SourceBlock]) -> None:
    """Refuse input that is not in document order.

    The contract was documented and unchecked, and unchecked it fails quietly: a
    caller that reads blocks without an ORDER BY gets chunks whose text is
    assembled in whatever order the database returned rows, which reads as prose
    and is not. Raised rather than sorted here, because silently repairing a
    caller's bug hides it.

    Raises:
        CoverageError: the blocks are not ascending by ordinal.
    """
    ordinals = [block.ordinal for block in blocks]
    out_of_order = next(
        (later for earlier, later in pairwise(ordinals) if later < earlier), None
    )
    if out_of_order is not None:
        raise CoverageError(
            "blocks must be supplied in document order; "
            f"ordinal {out_of_order} follows a larger one"
        )


def mark_table_derived(
    box: tuple[float, float, float, float],
    regions: Sequence[tuple[float, float, float, float]],
    *,
    threshold: float,
) -> bool:
    """Whether a block lies inside a detected table region.

    Separate from the chunker because it needs page geometry, which the chunker
    deliberately does not see. Measured on a development filing, 6% of blocks fall
    inside a region the detector found — and the CSR committee table on page 60
    does not, because the detector missed it entirely. The flag therefore marks
    what was *detected*, never what is a table.
    """
    return any(_overlap(box, region) > threshold for region in regions)


def _overlap(box: tuple[float, ...], region: tuple[float, ...]) -> float:
    """Share of ``box``'s area that lies inside ``region``."""
    wide = min(box[2], region[2]) - max(box[0], region[0])
    tall = min(box[3], region[3]) - max(box[1], region[1])
    if wide <= 0 or tall <= 0:
        return 0.0
    area = (box[2] - box[0]) * (box[3] - box[1])
    return (wide * tall) / area if area > 0 else 0.0


def _assemble(
    units: Sequence[SourceBlock], config: ChunkingConfig, count: TokenCounter
) -> tuple[Chunk, ...]:
    """Walk the blocks, maintaining sections, emitting children and parents."""
    stack = HeadingStack()
    chunks: list[Chunk] = []
    section: list[SourceBlock] = []
    section_path: tuple[str, ...] = ()

    def close_section() -> None:
        nonlocal section
        if section:
            _emit_section(section, section_path, config, count, chunks)
            section = []

    # Levels are resolved for the whole document first, because deciding whether a
    # numbered line is a heading or a list item needs its neighbours.
    levels = suppress_list_runs(
        [
            None
            if unit.table_derived
            else heading_level(unit.text, max_chars=config.heading_max_chars)
            for unit in units
        ]
    )

    for unit, level in zip(units, levels, strict=True):
        if level is not None:
            # A heading ends the section before it and opens the next.
            close_section()
            stack.push(level, unit.text)
            section_path = stack.path
            section = [unit]
            continue
        section.append(unit)

    close_section()
    return tuple(chunks)


def _emit_section(
    section: Sequence[SourceBlock],
    path: tuple[str, ...],
    config: ChunkingConfig,
    count: TokenCounter,
    out: list[Chunk],
) -> None:
    """Emit one section as contiguous runs, each with its own parent.

    §18.4 requires narrative and table-derived content to stay distinguishable, so
    the two are never mixed inside a chunk. The obvious way to achieve that — group
    the whole section by type — is wrong, and measurably so: a section reading
    paragraph, table row, paragraph produced a chunk containing both paragraphs
    joined by a newline, as though they were consecutive sentences.

    **That fabricates adjacency.** The text never appeared contiguously in the
    document, and a reader given it has no way to know a table stood between.
    §14.4 forbids presenting enriched text as evidence, and inventing contiguity is
    a worse form of the same thing.

    So the section is split into *runs* of consecutive blocks of one type. Order is
    preserved, each chunk's text appears in the document exactly as assembled, and
    a section that alternates simply yields more parents — which is the honest
    shape of a section that alternates.
    """
    for kind, run in _runs(section):
        _emit_group(run, path, kind, config, count, out)


def _runs(
    section: Sequence[SourceBlock],
) -> list[tuple[EvidenceType, list[SourceBlock]]]:
    """Split a section into maximal runs of consecutive same-type blocks."""
    grouped: list[tuple[EvidenceType, list[SourceBlock]]] = []
    for unit in section:
        kind = (
            EvidenceType.TABLE_DERIVED
            if unit.table_derived
            else EvidenceType.NARRATIVE
        )
        if grouped and grouped[-1][0] is kind:
            grouped[-1][1].append(unit)
        else:
            grouped.append((kind, [unit]))
    return grouped


def _emit_group(
    units: Sequence[SourceBlock],
    path: tuple[str, ...],
    kind: EvidenceType,
    config: ChunkingConfig,
    count: TokenCounter,
    out: list[Chunk],
) -> None:
    parent_index = len(out)
    out.append(_placeholder_parent(path, kind, config))

    pending: list[SourceBlock] = []
    pending_tokens = 0
    children = 0

    def flush(notes: tuple[str, ...] = ()) -> None:
        nonlocal pending, pending_tokens, children
        if not pending:
            return
        out.append(
            _build(
                pending,
                path,
                kind,
                config,
                count,
                role=ChunkRole.CHILD,
                parent_index=parent_index,
                ordinal=len(out),
                notes=notes + ((JOINED_FRAGMENTS,) if len(pending) > 1 else ()),
            )
        )
        children += 1
        pending = []
        pending_tokens = 0

    for unit in units:
        tokens = count(unit.text)
        if tokens > config.child_max_tokens:
            flush()
            for piece, is_split in _split_block(unit, config, count):
                out.append(
                    _build(
                        [piece],
                        path,
                        kind,
                        config,
                        count,
                        role=ChunkRole.CHILD,
                        parent_index=parent_index,
                        ordinal=len(out),
                        notes=(SPLIT_OVERSIZE,) if is_split else (),
                    )
                )
                children += 1
            continue

        if pending_tokens + tokens > config.child_max_tokens and pending_tokens:
            flush()
        pending.append(unit)
        pending_tokens += tokens

    flush()
    _absorb_short_tail(out, parent_index + 1, config, count)

    parent = _build(
        units,
        path,
        kind,
        config,
        count,
        role=ChunkRole.PARENT,
        parent_index=None,
        ordinal=parent_index,
        notes=(),
        cap=config.parent_max_tokens,
    )

    if children == 1 and out[parent_index + 1].text == parent.text:
        # A parent identical to its only child is pure duplication: §20.8 would
        # expand from that child and return the same text. Measured on a
        # development filing, 527 of 680 parents were byte-identical to their only
        # child. Drop the slot and re-point the child at nothing.
        only_child = out[parent_index + 1]
        out[parent_index : parent_index + 2] = [
            replace(only_child, parent_index=None, ordinal=parent_index)
        ]
        return

    out[parent_index] = parent


def _absorb_short_tail(
    out: list[Chunk], first: int, config: ChunkingConfig, count: TokenCounter
) -> None:
    """Merge a sub-minimum final chunk into the one before it, where it fits.

    ``child_min_tokens`` was declared and never read: the accumulator merged while
    filling, but whatever remained at the end was emitted regardless of size. On a
    real filing that left 57 children under the stated 48-token floor and 108 under
    100 — fragments whose embeddings are ambiguous out of context, which is the
    failure a minimum exists to prevent.

    Merging backwards rather than dropping, because dropping loses the blocks.
    When the merge would exceed ``child_max_tokens`` the short chunk stays: an
    over-budget chunk is truncated by the embedding model, which is worse than a
    short one.

    **This reaches the tail of one run and no further, which is a smaller guarantee
    than the floor sounds like.** ``first`` bounds it to the run being emitted, so a
    run whose entire content is under the floor has nothing to merge into and its
    single chunk is emitted short. Measured across the development corpus that is
    1,086 of the 1,164 under-floor children — the dominant case, not the residue.
    Reaching across runs is refused rather than unimplemented: see
    ``child_min_tokens`` for why, and §18 of the limitation register for the
    measurement.
    """
    if len(out) - first < 2:
        return
    tail = out[-1]
    if tail.role is not ChunkRole.CHILD or tail.token_count >= config.child_min_tokens:
        return

    previous = out[-2]
    if previous.role is not ChunkRole.CHILD:
        return
    merged_text = _JOIN.join([previous.text, tail.text])
    if count(merged_text) > config.child_max_tokens:
        return

    out[-2:] = [
        replace(
            previous,
            text=merged_text,
            source_element_ids=(
                *previous.source_element_ids,
                *(
                    element_id
                    for element_id in tail.source_element_ids
                    if element_id not in previous.source_element_ids
                ),
            ),
            page_numbers=tuple(
                sorted({*previous.page_numbers, *tail.page_numbers})
            ),
            token_count=count(merged_text),
            char_count=len(merged_text),
            notes=tuple({*previous.notes, *tail.notes, JOINED_FRAGMENTS}),
        )
    ]


def _placeholder_parent(
    path: tuple[str, ...], kind: EvidenceType, config: ChunkingConfig
) -> Chunk:
    """Reserve the parent's slot so children can reference it before it is built.

    The parent's text is the whole section, which is not known until its children
    have been walked; reserving the index keeps document order intact instead of
    appending parents after the fact.
    """
    return Chunk(
        text="",
        source_element_ids=(),
        page_numbers=(),
        heading_path=path,
        evidence_type=kind,
        role=ChunkRole.PARENT,
        config_version=config.version,
    )


def _build(
    units: Sequence[SourceBlock],
    path: tuple[str, ...],
    kind: EvidenceType,
    config: ChunkingConfig,
    count: TokenCounter,
    *,
    role: ChunkRole,
    parent_index: int | None,
    ordinal: int,
    notes: tuple[str, ...],
    cap: int | None = None,
) -> Chunk:
    text = _JOIN.join(unit.text for unit in units)
    if cap is not None and count(text) > cap:
        text = _truncate(text, cap, count)
        notes = (*notes, "parent_truncated")
    return Chunk(
        text=text,
        source_element_ids=tuple(unit.element_id for unit in units),
        page_numbers=tuple(sorted({unit.page_number for unit in units})),
        heading_path=path,
        evidence_type=kind,
        role=role,
        parent_index=parent_index,
        token_count=count(text),
        char_count=len(text),
        ordinal=ordinal,
        config_version=config.version,
        notes=notes,
    )


def _truncate(text: str, cap: int, count: TokenCounter) -> str:
    """Shorten a parent to its budget, on a line boundary where one exists.

    Parents are context, not evidence — §14.9 resolves citations to source regions
    and a child always carries the exact blocks — so losing the tail of a very long
    section costs interpretation, not provenance.

    **The first line may itself exceed the cap**, which an earlier version silently
    allowed: it kept the first line unconditionally and returned a parent fifty
    tokens over a cap of twenty. A section whose opening block is one long
    paragraph is ordinary, so that is the common case, not a corner. When it
    happens the line is cut on a word boundary rather than kept whole.
    """
    lines = text.split(_JOIN)
    kept: list[str] = []
    for line in lines:
        candidate = _JOIN.join([*kept, line])
        if kept and count(candidate) > cap:
            break
        kept.append(line)

    joined = _JOIN.join(kept)
    if count(joined) <= cap:
        return joined

    words = joined.split()
    trimmed: list[str] = []
    for word in words:
        if trimmed and count(" ".join([*trimmed, word])) > cap:
            break
        trimmed.append(word)
    return " ".join(trimmed)


def _split_block(
    unit: SourceBlock, config: ChunkingConfig, count: TokenCounter
) -> list[tuple[SourceBlock, bool]]:
    """Divide one oversized block at sentence boundaries (§18.3).

    Returns the pieces with a flag saying whether splitting happened, so a chunk
    built from a piece can record that a sentence may straddle it.

    Every piece keeps the originating block's element id. That is deliberate: a
    citation must resolve to the source region, and the region is the whole block —
    claiming otherwise would invent a sub-block address the source representation
    does not have.
    """
    sentences = _SENTENCE.split(unit.text)
    if len(sentences) == 1:
        return _hard_split(unit, config, count)

    pieces: list[tuple[SourceBlock, bool]] = []
    buffer: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*buffer, sentence])
        if buffer and count(candidate) > config.child_max_tokens:
            pieces.append((_piece(unit, " ".join(buffer)), True))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        pieces.append((_piece(unit, " ".join(buffer)), True))

    # A single sentence longer than the budget still needs dividing.
    divided: list[tuple[SourceBlock, bool]] = []
    for piece, _ in pieces:
        if count(piece.text) > config.child_max_tokens:
            divided.extend(_hard_split(piece, config, count))
        else:
            divided.append((piece, True))
    return divided


def _hard_split(
    unit: SourceBlock, config: ChunkingConfig, count: TokenCounter
) -> list[tuple[SourceBlock, bool]]:
    """Last resort: divide on whitespace when no sentence boundary exists.

    Reached by a block that is one enormous sentence, or a run of text with no
    punctuation at all — a flattened table row, typically. Splits between words so
    a number is never cut in half.

    **A single word can still exceed the budget**: an unbroken identifier, a URL, a
    long run of digits with no separators. Emitting it whole breaks the budget
    invariant and the embedding model truncates it silently, losing the tail. So a
    word that cannot fit alone is divided by characters — which is ugly, and is the
    only option that keeps every character in the index.
    """
    words = unit.text.split()
    if not words:
        return [(unit, False)]

    pieces: list[tuple[SourceBlock, bool]] = []
    buffer: list[str] = []
    for word in words:
        if count(word) > config.child_max_tokens:
            if buffer:
                pieces.append((_piece(unit, " ".join(buffer)), True))
                buffer = []
            pieces.extend(_split_word(unit, word, config, count))
            continue
        candidate = " ".join([*buffer, word])
        if buffer and count(candidate) > config.child_max_tokens:
            pieces.append((_piece(unit, " ".join(buffer)), True))
            buffer = [word]
        else:
            buffer.append(word)
    if buffer:
        pieces.append((_piece(unit, " ".join(buffer)), True))
    return pieces


def _split_word(
    unit: SourceBlock, word: str, config: ChunkingConfig, count: TokenCounter
) -> list[tuple[SourceBlock, bool]]:
    """Divide a single word that cannot fit the budget, by characters.

    Pathological input only. Grows the piece one character at a time rather than
    guessing a character-per-token ratio, because the counter is injected and its
    ratio is not knowable here.
    """
    pieces: list[tuple[SourceBlock, bool]] = []
    buffer = ""
    for character in word:
        if buffer and count(buffer + character) > config.child_max_tokens:
            pieces.append((_piece(unit, buffer), True))
            buffer = character
        else:
            buffer += character
    if buffer:
        pieces.append((_piece(unit, buffer), True))
    return pieces


def _piece(unit: SourceBlock, text: str) -> SourceBlock:
    return SourceBlock(
        element_id=unit.element_id,
        text=text,
        page_number=unit.page_number,
        table_derived=unit.table_derived,
        ordinal=unit.ordinal,
    )


def verify_coverage(units: Sequence[SourceBlock], chunks: Sequence[Chunk]) -> None:
    """Confirm every block reached exactly one child chunk.

    Checked on every run rather than only in tests. A chunker that drops a
    paragraph produces an index that is simply missing it, and no downstream
    assertion can tell that apart from a document that never contained it.

    Parents are excluded from the count because they deliberately repeat their
    children's content — they are the same text at a different granularity, not a
    second copy of the evidence.

    Raises:
        CoverageError: a block was lost or placed more than once.
    """
    expected = {unit.element_id for unit in units}
    placed: list[UUID] = [
        element_id
        for chunk in chunks
        if chunk.role is ChunkRole.CHILD
        for element_id in chunk.source_element_ids
    ]

    missing = expected - set(placed)
    if missing:
        raise CoverageError(
            f"{len(missing)} block(s) reached no chunk; "
            f"first is {sorted(str(m) for m in missing)[0]}"
        )

    # A split block legitimately appears in several chunks; anything else does not.
    split_ids = {
        element_id
        for chunk in chunks
        if SPLIT_OVERSIZE in chunk.notes
        for element_id in chunk.source_element_ids
    }
    counted: dict[UUID, int] = {}
    for element_id in placed:
        counted[element_id] = counted.get(element_id, 0) + 1
    duplicated = sorted(
        str(element_id)
        for element_id, times in counted.items()
        if times > 1 and element_id not in split_ids
    )
    if duplicated:
        raise CoverageError(
            f"{len(duplicated)} block(s) placed in more than one chunk; "
            f"first is {duplicated[0]}"
        )
