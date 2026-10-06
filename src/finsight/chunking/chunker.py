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

_WORD: Final = re.compile(r"\S+")
"""One run of non-whitespace, matched for its span so the original can be sliced."""

_JOIN: Final = "\n"
"""How blocks are joined. A newline, because that is the boundary the document drew
and collapsing it to a space would merge a heading into the line beneath it."""

_LEADER_RUN: Final = re.compile(r"[._]{10,}")
"""Ten or more consecutive dots or underscores: a typographic leader, not language.

Ten rather than three, because an ellipsis is three and a decimal run is none. The
shortest real leader measured in this corpus is far longer than ten.
"""

_LEADER_CHARS: Final = frozenset("._")


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

    **Typographic leader lines are also dropped**, which is a narrower filter and a
    larger decision: it withholds real text from the index rather than discarding
    whitespace. 129 blocks across the three filings, 29,838 characters of 4,723,181.
    See :func:`_is_retrievable` for the measurement and ADR-007 for the §7 deviation.

    Every block is accounted for: of 40,476 blocks carrying text, 1,557 are whitespace,
    129 are leader lines, and 38,790 reach a chunk. The three sum exactly, which is the
    check that matters — a reduction in chunk count is expected here, but a block that
    stops reaching any chunk would not be.
    """
    settings = config or ChunkingConfig()
    usable = [block for block in blocks if _is_retrievable(block.text, settings)]
    if not usable:
        return ()
    _require_document_order(usable)

    chunks = _assemble(usable, settings, count_tokens)
    verify_coverage(usable, chunks)
    verify_parents(chunks)
    return chunks


def _is_retrievable(text: str, config: ChunkingConfig) -> bool:
    """Whether a block should become retrieval text at all.

    Two exclusions, both of which discard the block from the *index* while leaving it
    untouched in the source representation — verbatim, citable, with its coordinates.

    **Whitespace.** Nothing to retrieve, and a chunk of it violates ``text_not_blank``.

    **Typographic leaders.** A table-of-contents line is a leader run with a section
    name attached, and indexing one is measurably harmful rather than merely untidy.
    Measured on the development corpus, 78 of 4,969 children and 13 of 790 parents are
    over 80% dots — the worst a 1,515-token parent at 91% — and they carry just enough
    lexemes to compete: 5 on average against 60 for a normal child. Because BM25
    normalises by document length and these are very short in indexed terms, they are
    *advantaged*. Probed with six section-name queries, two returned a contents line at
    **rank 1**, pushing the section it points at to ranks 3 and below:

        'basis of preparation of financial statements'
          1. 1.2 Basis of preparation of financial statemen......  (84% dots)
          3. 1.2 Basis of preparation of financial statements ...   (the actual section)

    A contents line is an index *of* the document, so it matches a section-name query
    almost perfectly while containing none of the answer. That is the failure: not noise,
    but a near-perfect match that is never the answer.

    **This deviates from §7's "keep all extracted content searchable", and ADR-007
    records it.** What is lost is the section-to-page mapping, for 129 blocks across
    three filings. What is not lost: the sections themselves stay findable — in both
    displacement cases above the real content was already being retrieved behind the
    contents line, so excluding it promotes rather than hides. §7's preservation
    requirements are untouched; only the retrieval clause is.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _LEADER_RUN.search(stripped) is None:
        return True
    return _leader_share(stripped) < config.leader_share_floor


def _leader_share(text: str) -> float:
    """Share of non-whitespace characters that are leader characters.

    Over non-whitespace rather than the whole string, so the indentation a contents line
    carries cannot dilute its own measurement.
    """
    solid = [character for character in text if not character.isspace()]
    if not solid:
        return 0.0
    leaders = sum(1 for character in solid if character in _LEADER_CHARS)
    return leaders / len(solid)


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


def region_containing(
    box: tuple[float, float, float, float],
    regions: Sequence[tuple[UUID, tuple[float, float, float, float]]],
    *,
    threshold: float,
) -> UUID | None:
    """Which detected table region a block lies inside, or ``None``.

    Separate from the chunker because it needs page geometry, which the chunker
    deliberately does not see. Measured on a development filing, 6% of blocks fall
    inside a region the detector found — and the CSR committee table on page 60
    does not, because the detector missed it entirely. This reports what was
    *detected*, never what is a table.

    **The region with the greatest overlap wins, not the first one found.** Regions can
    overlap each other — a detector proposing a table and a nested sub-table produces
    two boxes over the same ink — and the previous ``any(...)`` form took whichever came
    first in page order. That made a block's region depend on the detector's emission
    order rather than on geometry, so the same page could classify differently after an
    unrelated detector change, silently moving chunk boundaries.

    **A tie goes to the smaller region**, which matters for the nested case: a block
    inside a sub-table lies equally inside its parent table, both at an overlap of 1.0,
    and the sub-table is the more specific answer. The region identifier breaks a
    remaining tie so the result is fully deterministic — reachable when two boxes have
    identical area over identical ink, and required because chunking must reproduce for
    the idempotency index to mean anything.
    """
    candidates = [
        (_overlap(box, region), -_area(region), region_id)
        for region_id, region in regions
    ]
    qualifying = [candidate for candidate in candidates if candidate[0] > threshold]
    if not qualifying:
        return None
    # Greatest overlap, then smallest area (negated above), then the identifier.
    return max(qualifying)[2]


def _area(region: tuple[float, float, float, float]) -> float:
    return max(region[2] - region[0], 0.0) * max(region[3] - region[1], 0.0)


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
    """Split a section into maximal runs of consecutive same-type blocks.

    **Runs are grouped by type and not by table, and two attempts to change that were
    measured and rejected.** ``SourceBlock.region_id`` makes grouping per table possible,
    and the recorded defect — 19% of detected tables have text split across chunks —
    makes it look obviously worth doing. Both forms were measured end to end against the
    stored corpus, with the region mapping held identical on both sides:

    | | Baseline | Run per table | Child boundary per table |
    |---|---|---|---|
    | Children under the 48-token floor | 1,212 | **1,399** | 1,210 |
    | Regions split across chunks | 165 | **184** | 163 |
    | Regions split across parents | 11 | 11 | 11 |
    | Chunks holding two or more tables | 50 | — | 39 |

    A run per table is clearly harmful: 803 regions become 803 runs, each taking its own
    parent window, and because merging across runs is refused (see ``child_min_tokens``) a
    small table becomes a fragment that can never grow. It also breaks a property §20.8
    depends on — a table interleaved with prose lands under two parents, so expanding from
    one recovers half the table.

    A child boundary per table is harmless but not useful: 2 fewer split regions and 11
    fewer mixed chunks out of 1,254 that hold table text. Its sign is not even consistent
    across documents — one of the three filings got worse on both counts — so at this
    corpus size it is noise, and a production path does not carry a threshold interaction
    for noise.

    The 128 regions that genuinely exceed the child budget (median 734 tokens, max 3,801)
    must divide whatever the grouping, and no grouping reunites them. Recorded in the
    limitation register rather than fixed here.
    """
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
    """Emit a run as parent-sized windows, so a parent holds all of its children.

    **One parent per run was wrong, and measurably so.** A parent was capped at
    ``parent_max_tokens`` while its children were not collectively bounded, so a
    long run produced a parent that was a *prefix* of its children. Measured on the
    development corpus: 143 of 524 parents were truncated, and **1,206 of 2,790
    parented children were not contained in their own parent** — the worst a parent
    of 1,535 tokens standing for 50 children totalling 17,498.

    That breaks the one thing §18.5 keeps parents for and §20.8 expands to them for.
    Expansion would have returned the opening of a section as "context" for a
    passage from its middle: text that looks like context, reads like context, and
    does not contain the passage. Worse than returning nothing, because nothing is
    visibly nothing.

    So the run is partitioned into windows that a parent can hold whole, and each
    window gets its own parent. A wide section now yields several parents instead of
    one misleading one, which is the honest shape of a wide section — the same
    reasoning that made ``_emit_section`` split a mixed section into runs.
    """
    for window in _parent_windows(units, config, count):
        _emit_window(window, path, kind, config, count, out)


def _parent_windows(
    units: Sequence[SourceBlock], config: ChunkingConfig, count: TokenCounter
) -> list[list[SourceBlock]]:
    """Partition a run into consecutive windows a parent can contain.

    Budgeted on the sum of the units' own token counts, which is not exactly the
    token count of their joined text — a tokenizer is not quite additive across a
    newline. The error is a token or two per join and always makes the window
    *slightly* larger than budgeted, never smaller, so ``parent_max_tokens`` is a
    windowing target rather than a hard ceiling. Measured overshoot is recorded with
    the configuration value.

    A single unit larger than the whole parent budget becomes its own window. Its
    parent is then that one block, over budget, and the children are the pieces
    ``_split_block`` cut from it — so containment still holds, which is the property
    worth protecting. Truncating instead is what produced the defect this replaces.
    """
    windows: list[list[SourceBlock]] = []
    current: list[SourceBlock] = []
    total = 0
    for unit in units:
        tokens = count(unit.text)
        if current and total + tokens > config.parent_max_tokens:
            windows.append(current)
            current = []
            total = 0
        current.append(unit)
        total += tokens
    if current:
        windows.append(current)
    return windows


def _emit_window(
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

    # No cap. The window was built to fit, and truncating here is exactly the
    # defect ``_emit_group`` documents: a parent that does not contain its children
    # is not context.
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

    **A chunk cut out of an oversized block is never merged.** The merge joins two
    chunks with a newline, which reproduces the source only when the two were whole
    blocks — blocks are what the parent joins with a newline. A piece of a split
    block sits inside its block separated by whatever whitespace the document used,
    so joining it with a newline produces text that appears nowhere in the source,
    and the chunk stops being contained in its own parent. Measured on a real
    filing before the guard: 2 chunks of 2,337, both carrying ``split_oversize_block``
    and ``joined_short_blocks`` together. Caught by ``verify_parents``, which is why
    that check runs on every call and not only in tests.
    """
    if len(out) - first < 2:
        return
    tail = out[-1]
    if tail.role is not ChunkRole.CHILD or tail.token_count >= config.child_min_tokens:
        return

    previous = out[-2]
    if SPLIT_OVERSIZE in tail.notes or SPLIT_OVERSIZE in previous.notes:
        return
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
) -> Chunk:
    text = _JOIN.join(unit.text for unit in units)
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

    **Pieces are slices of the block, never rejoined text.** An earlier version
    split on the boundary and reassembled with ``" ".join(...)``, which collapsed
    every newline and run of spaces inside the piece. The module promises text is
    preserved verbatim and that promise was false for exactly these chunks —
    invisibly, because the collapsed form reads identically. ``verify_parents``
    found it: a rejoined piece is not a substring of its own parent.
    """
    spans = _sentence_spans(unit.text)
    if len(spans) == 1:
        return _hard_split(unit, config, count)

    pieces: list[tuple[SourceBlock, bool]] = []
    begin: int | None = None
    finish = 0
    for start, end in spans:
        if begin is not None and count(unit.text[begin:end]) > config.child_max_tokens:
            pieces.append((_piece(unit, unit.text[begin:finish]), True))
            begin, finish = start, end
        else:
            begin = start if begin is None else begin
            finish = end
    if begin is not None:
        pieces.append((_piece(unit, unit.text[begin:finish]), True))

    # A single sentence longer than the budget still needs dividing.
    divided: list[tuple[SourceBlock, bool]] = []
    for piece, _ in pieces:
        if count(piece.text) > config.child_max_tokens:
            divided.extend(_hard_split(piece, config, count))
        else:
            divided.append((piece, True))
    return divided


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of the sentences in ``text``, as ``_SENTENCE`` divides them.

    Spans rather than substrings so a caller can slice the original and keep its
    whitespace. The separator the pattern matched is excluded from both neighbours,
    and a slice spanning several sentences therefore carries the real separators
    back with it.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    return spans


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

    Like the sentence path, pieces are **slices**. Rejoining words with single
    spaces is what made a split piece diverge from its source text.
    """
    spans = [(match.start(), match.end()) for match in _WORD.finditer(unit.text)]
    if not spans:
        return [(unit, False)]

    pieces: list[tuple[SourceBlock, bool]] = []
    begin: int | None = None
    finish = 0
    for start, end in spans:
        if count(unit.text[start:end]) > config.child_max_tokens:
            if begin is not None:
                pieces.append((_piece(unit, unit.text[begin:finish]), True))
                begin = None
            pieces.extend(
                _split_word(unit, unit.text[start:end], config, count)
            )
            continue
        if begin is not None and count(unit.text[begin:end]) > config.child_max_tokens:
            pieces.append((_piece(unit, unit.text[begin:finish]), True))
            begin, finish = start, end
        else:
            begin = start if begin is None else begin
            finish = end
    if begin is not None:
        pieces.append((_piece(unit, unit.text[begin:finish]), True))
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
    """A slice of one block, keeping its identity, position and table.

    ``region_id`` is carried so every piece of a split table block stays in the same run
    as its siblings. Dropping it would make the second piece look like prose and split
    the table at the one point the split was meant to be invisible.
    """
    return SourceBlock(
        element_id=unit.element_id,
        text=text,
        page_number=unit.page_number,
        region_id=unit.region_id,
        ordinal=unit.ordinal,
    )


def verify_parents(chunks: Sequence[Chunk]) -> None:
    """Confirm every parent's text contains every one of its children.

    Checked on every run, for the same reason as :func:`verify_coverage`: this
    invariant broke silently and stayed broken. A parent was capped while its
    children were not collectively bounded, so 1,206 of 2,790 parented children on
    the development corpus were absent from their own parent, and nothing noticed
    because the parent was still a valid chunk of real text from the right section.

    Substring containment rather than a token-count comparison, because containment
    is what §20.8's expansion actually needs: a count that merely *fits* says
    nothing about whether the child is in there.

    Raises:
        CoverageError: a child is not contained in its parent, or points at a chunk
            that is not a parent.
    """
    for index, chunk in enumerate(chunks):
        if chunk.parent_index is None:
            continue
        if not 0 <= chunk.parent_index < len(chunks):
            raise CoverageError(
                f"chunk {index} points at parent {chunk.parent_index}, "
                f"which is outside the {len(chunks)} chunk(s) produced"
            )
        parent = chunks[chunk.parent_index]
        if parent.role is not ChunkRole.PARENT:
            raise CoverageError(
                f"chunk {index} points at chunk {chunk.parent_index}, "
                f"which is a {parent.role.value} rather than a parent"
            )
        if chunk.text not in parent.text:
            raise CoverageError(
                f"chunk {index} ({chunk.token_count} tokens) is not contained in "
                f"its parent ({parent.token_count} tokens); expanding to that "
                "parent would return context that omits the passage"
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
