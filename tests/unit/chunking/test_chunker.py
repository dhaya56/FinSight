"""Tests for assembling blocks into chunks.

The failure this guards against is silent by construction: a chunker that drops a
paragraph produces an index that simply does not contain it, and no downstream
assertion can tell that apart from a document that never said it. So the tests that
matter most are not about chunk sizes — they are
:class:`TestCoverage`, which asserts every block is placed exactly once, and
:class:`TestVerbatimText`, which asserts the chunker never rewrote what it was
given.

The token counter is a word count. Exact, obvious, and independent of any model,
so a failure here is a chunking defect rather than a tokenizer's rounding.
"""

import uuid

import pytest

from finsight.chunking.chunker import (
    CoverageError,
    chunk_blocks,
    mark_table_derived,
    verify_coverage,
)
from finsight.chunking.contracts import (
    SPLIT_OVERSIZE,
    Chunk,
    ChunkingConfig,
    ChunkRole,
    EvidenceType,
    SourceBlock,
)


def words(text: str) -> int:
    return len(text.split())


def block(
    text: str, *, page: int = 1, ordinal: int = 0, table: bool = False
) -> SourceBlock:
    return SourceBlock(
        element_id=uuid.uuid4(),
        text=text,
        page_number=page,
        ordinal=ordinal,
        table_derived=table,
    )


def blocks(*texts: str, page: int = 1) -> list[SourceBlock]:
    return [block(text, page=page, ordinal=i) for i, text in enumerate(texts)]


def children(chunks: tuple[Chunk, ...]) -> list[Chunk]:
    return [chunk for chunk in chunks if chunk.role is ChunkRole.CHILD]


def parents(chunks: tuple[Chunk, ...]) -> list[Chunk]:
    return [chunk for chunk in chunks if chunk.role is ChunkRole.PARENT]


SMALL = ChunkingConfig(child_max_tokens=10, child_min_tokens=2, parent_max_tokens=100)


class TestCoverage:
    """Every block reaches exactly one child chunk. Nothing lost, nothing doubled."""

    def test_every_block_is_placed(self) -> None:
        source = blocks("alpha beta", "gamma delta", "epsilon zeta")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        placed = {
            element_id
            for chunk in children(result)
            for element_id in chunk.source_element_ids
        }
        assert placed == {b.element_id for b in source}

    def test_no_block_is_placed_twice(self) -> None:
        source = blocks(*[f"word{n} filler" for n in range(40)])

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        placed = [
            element_id
            for chunk in children(result)
            for element_id in chunk.source_element_ids
        ]
        assert len(placed) == len(set(placed))

    def test_coverage_is_checked_on_every_run_not_only_in_tests(self) -> None:
        """The guarantee is enforced in the chunker, not asserted about it."""
        source = blocks("alpha beta")
        missing = block("never placed")

        with pytest.raises(CoverageError, match="reached no chunk"):
            verify_coverage(
                [*source, missing],
                chunk_blocks(source, config=SMALL, count_tokens=words),
            )

    def test_a_short_tail_is_emitted_rather_than_dropped(self) -> None:
        """A trailing fragment under the minimum must not be discarded.

        Dropping it is the obvious way to keep chunks tidy and the obvious way to
        lose the last sentence of a section.
        """
        source = blocks("alpha beta gamma delta epsilon zeta eta theta iota", "tail")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert "tail" in " ".join(chunk.text for chunk in children(result))

    def test_blank_blocks_are_ignored_without_breaking_coverage(self) -> None:
        source = [block("alpha beta", ordinal=0), block("   ", ordinal=1)]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        placed = {
            element_id
            for chunk in children(result)
            for element_id in chunk.source_element_ids
        }
        assert placed == {source[0].element_id}

    def test_no_blocks_yields_no_chunks(self) -> None:
        assert chunk_blocks([], config=SMALL, count_tokens=words) == ()


class TestVerbatimText:
    """§14.4: the chunker never rewrites what it was given."""

    def test_block_text_survives_unmodified(self) -> None:
        figure = "Revenue was ₹1,62,990 crore — up 6.1%."
        source = blocks(figure)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert figure in children(result)[0].text

    def test_currency_and_dashes_are_not_normalised(self) -> None:
        """An en dash is the nil marker in a financial table.

        Flattening it to a hyphen would turn "no such item" into a minus sign,
        which is the defect the Docling adapter was built to avoid.
        """
        source = blocks("Other income – ₹500")  # noqa: RUF001 - the en dash is the subject

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert "–" in children(result)[0].text  # noqa: RUF001 - deliberate
        assert "₹" in children(result)[0].text

    def test_joined_blocks_keep_the_document_s_line_boundary(self) -> None:
        """Collapsing the join to a space would merge a label into the line below."""
        source = blocks("Total", "1,234")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert "Total\n1,234" in children(result)[0].text


class TestJoiningSmallBlocks:
    """Blocks are fragments — median 38 characters — so joining is the main path."""

    def test_several_fragments_become_one_chunk(self) -> None:
        source = blocks("alpha", "beta", "gamma")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert len(children(result)) == 1

    def test_a_chunk_stops_at_the_budget(self) -> None:
        source = blocks(*[f"w{n}" for n in range(25)])

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert all(chunk.token_count <= SMALL.child_max_tokens for chunk in children(result))

    def test_joining_is_recorded(self) -> None:
        source = blocks("alpha", "beta")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert "joined_short_blocks" in children(result)[0].notes


class TestSplittingOversizedBlocks:
    def test_a_long_block_is_divided(self) -> None:
        long_text = ". ".join(f"Sentence number {n} here" for n in range(20)) + "."
        source = blocks(long_text)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert len(children(result)) > 1

    def test_splitting_is_recorded_so_a_straddled_sentence_is_explicable(
        self,
    ) -> None:
        long_text = ". ".join(f"Sentence number {n} here" for n in range(20)) + "."
        source = blocks(long_text)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert any(SPLIT_OVERSIZE in chunk.notes for chunk in children(result))

    def test_every_piece_still_cites_the_whole_block(self) -> None:
        """A piece has no address of its own.

        The source representation records a block, not a sub-block, so claiming a
        narrower citation would invent an address §14.9 cannot resolve.
        """
        long_text = ". ".join(f"Sentence number {n} here" for n in range(20)) + "."
        source = blocks(long_text)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert all(
            chunk.source_element_ids == (source[0].element_id,)
            for chunk in children(result)
        )

    def test_a_block_with_no_sentence_boundary_is_still_divided(self) -> None:
        """A flattened table row has no punctuation at all."""
        source = blocks(" ".join(f"{n},000" for n in range(50)))

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert len(children(result)) > 1

    def test_splitting_never_cuts_inside_a_number(self) -> None:
        source = blocks(" ".join("1,62,990" for _ in range(40)))

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        for chunk in children(result):
            for token in chunk.text.split():
                assert token == "1,62,990"


class TestSections:
    def test_a_numbered_heading_starts_a_new_section(self) -> None:
        source = blocks(
            "1. Brief outline on CSR Policy:",
            "Body text about policy",
            "2. Composition of CSR Committee:",
            "Body text about the committee",
        )

        result = chunk_blocks(source, config=SMALL, count_tokens=words)
        paths = {chunk.heading_path for chunk in children(result)}

        assert ("1. Brief outline on CSR Policy:",) in paths
        assert ("2. Composition of CSR Committee:",) in paths

    def test_a_subsection_nests_under_its_parent(self) -> None:
        source = blocks(
            "3. Significant accounting policies",
            "3.2 Property, plant and equipment",
            "Body text",
        )

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert children(result)[-1].heading_path == (
            "3. Significant accounting policies",
            "3.2 Property, plant and equipment",
        )

    def test_a_section_survives_a_page_break(self) -> None:
        """Resetting per page would strip the path from every continuation page."""
        source = [
            block("7. Risk factors", page=1, ordinal=0),
            block("First page of risks", page=1, ordinal=1),
            block("Second page of risks", page=2, ordinal=2),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert all(
            chunk.heading_path == ("7. Risk factors",) for chunk in children(result)
        )

    def test_a_chunk_records_every_page_it_spans(self) -> None:
        source = [
            block("alpha", page=4, ordinal=0),
            block("beta", page=5, ordinal=1),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert children(result)[0].page_numbers == (4, 5)


class TestEvidenceType:
    def test_table_derived_blocks_are_chunked_separately(self) -> None:
        """§18.4: a chunk mixing a paragraph with a flattened row is neither."""
        source = [
            block("Narrative sentence here", ordinal=0),
            block("1 Govind Iyer Chairperson 4 4", ordinal=1, table=True),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)
        kinds = {chunk.evidence_type for chunk in children(result)}

        assert kinds == {EvidenceType.NARRATIVE, EvidenceType.TABLE_DERIVED}

    def test_a_table_row_matching_a_heading_pattern_is_not_a_heading(self) -> None:
        """``3 Michael Gibbs Member 4 4`` starts with a number and is a row."""
        source = [
            block("Narrative", ordinal=0),
            block("3 Michael Gibbs Member 4 4", ordinal=1, table=True),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert all(chunk.heading_path == () for chunk in children(result))

    def test_overlap_marking_requires_real_containment(self) -> None:
        outside = (0.0, 0.0, 10.0, 10.0)
        region = (100.0, 100.0, 200.0, 200.0)

        assert mark_table_derived(outside, [region], threshold=0.5) is False

    def test_a_block_inside_a_region_is_marked(self) -> None:
        inside = (110.0, 110.0, 120.0, 120.0)
        region = (100.0, 100.0, 200.0, 200.0)

        assert mark_table_derived(inside, [region], threshold=0.5) is True


class TestParents:
    def test_a_parent_holds_the_whole_section(self) -> None:
        """Enough content to need several children, so a parent earns its place."""
        source = blocks("alpha " * 8, "middle " * 8, "gamma " * 8)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert len(children(result)) > 1
        assert "alpha" in parents(result)[0].text
        assert "gamma" in parents(result)[0].text

    def test_a_parent_identical_to_its_only_child_is_not_emitted(self) -> None:
        """Pure duplication: §20.8 expanding from that child returns the same text.

        Measured on a development filing, 527 of 680 parents were byte-identical
        to their only child.
        """
        source = blocks("alpha", "beta")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert parents(result) == []
        assert len(children(result)) == 1
        assert children(result)[0].parent_index is None

    def test_children_point_at_their_parent(self) -> None:
        source = blocks(*[f"w{n}" for n in range(25)])

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        for chunk in children(result):
            assert chunk.parent_index is not None
            assert result[chunk.parent_index].role is ChunkRole.PARENT

    def test_a_parent_is_capped_and_says_so(self) -> None:
        tight = ChunkingConfig(
            child_max_tokens=5, child_min_tokens=1, parent_max_tokens=8
        )
        source = blocks(*[f"w{n}" for n in range(30)])

        result = chunk_blocks(source, config=tight, count_tokens=words)
        parent = parents(result)[0]

        assert parent.token_count <= tight.parent_max_tokens
        assert "parent_truncated" in parent.notes


class TestProvenance:
    def test_every_chunk_names_its_sources(self) -> None:
        source = blocks("alpha beta", "gamma delta")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert all(chunk.source_element_ids for chunk in children(result))

    def test_sources_are_recorded_in_document_order(self) -> None:
        source = blocks("alpha", "beta", "gamma")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert children(result)[0].source_element_ids == tuple(
            b.element_id for b in source
        )

    def test_the_configuration_version_travels_with_each_chunk(self) -> None:
        """§18.10. Two chunk populations from different settings must be separable."""
        source = blocks("alpha beta")

        result = chunk_blocks(
            source, config=ChunkingConfig(version="7"), count_tokens=words
        )

        assert all(chunk.config_version == "7" for chunk in result)


class TestBudgetInvariant:
    """No child chunk may exceed the budget, whatever shape the input takes.

    This is the test that catches the defect a worked example hides. An earlier
    version had a separate threshold for splitting an oversized block; because the
    two defaults were equal, every example passed while a block between the two
    bounds produced a chunk far over budget — which an embedding model truncates
    without complaining.
    """

    @pytest.mark.parametrize(
        "texts",
        [
            pytest.param(["short"], id="single-fragment"),
            pytest.param(["w " * 50], id="one-block-over-budget"),
            pytest.param([". ".join(["Sentence here"] * 40)], id="many-sentences"),
            pytest.param(["1,62,990 " * 60], id="no-punctuation"),
            pytest.param(["x"] * 200, id="many-tiny-fragments"),
            pytest.param(["a " * 12, "b " * 12, "c " * 12], id="each-over-budget"),
            pytest.param(["₹1,00,000 " * 30, "tail"], id="figures-then-tail"),
        ],
    )
    def test_no_child_exceeds_the_budget(self, texts: list[str]) -> None:
        result = chunk_blocks(blocks(*texts), config=SMALL, count_tokens=words)

        over = [
            chunk.token_count
            for chunk in children(result)
            if chunk.token_count > SMALL.child_max_tokens
        ]
        assert over == []

    @pytest.mark.parametrize(
        "texts",
        [
            pytest.param(["w " * 50], id="one-block-over-budget"),
            pytest.param(["x"] * 200, id="many-tiny-fragments"),
            pytest.param([". ".join(["Sentence here"] * 40)], id="many-sentences"),
        ],
    )
    def test_coverage_holds_for_every_shape(self, texts: list[str]) -> None:
        source = blocks(*texts)

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        placed = {
            element_id
            for chunk in children(result)
            for element_id in chunk.source_element_ids
        }
        assert placed == {b.element_id for b in source}


class TestAuditFindings:
    """Regressions for defects an adversarial audit found after the first pass.

    Every one of these passed the original test suite. They are grouped so the
    class reads as what it is: the gap between "the examples work" and "the
    invariants hold".
    """

    def test_a_table_between_two_paragraphs_does_not_join_them(self) -> None:
        """The worst of the five, because the output looks correct.

        Grouping a whole section by evidence type produced a chunk containing both
        paragraphs joined by a newline, as though consecutive. The text never
        appeared that way in the document and a reader could not tell.
        """
        source = [
            block("Intro paragraph", ordinal=0),
            block("ROW ONE", ordinal=1, table=True),
            block("Closing paragraph", ordinal=2),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)
        texts = [chunk.text for chunk in children(result)]

        assert "Intro paragraph\nClosing paragraph" not in texts
        assert "Intro paragraph" in texts
        assert "Closing paragraph" in texts

    def test_runs_are_emitted_in_document_order(self) -> None:
        source = [
            block("Intro", ordinal=0),
            block("ROW", ordinal=1, table=True),
            block("Closing", ordinal=2),
        ]

        result = chunk_blocks(source, config=SMALL, count_tokens=words)

        assert [chunk.text for chunk in children(result)] == [
            "Intro",
            "ROW",
            "Closing",
        ]

    def test_two_adjacent_headings_both_survive(self) -> None:
        """A section whose body begins on the next page is ordinary."""
        source = blocks("7. Risk factors", "8. Other matters", "Body text")

        result = chunk_blocks(source, config=SMALL, count_tokens=words)
        paths = {chunk.heading_path for chunk in children(result)}

        assert ("7. Risk factors",) in paths
        assert ("8. Other matters",) in paths

    def test_a_parent_whose_first_line_exceeds_the_cap_is_still_capped(self) -> None:
        """An earlier version kept the first line unconditionally, cap or not."""
        tight = ChunkingConfig(
            child_max_tokens=5, child_min_tokens=1, parent_max_tokens=8
        )
        source = blocks("word " * 50)

        result = chunk_blocks(source, config=tight, count_tokens=words)

        assert all(
            chunk.token_count <= tight.parent_max_tokens for chunk in parents(result)
        )

    def test_a_parent_is_never_emptied_by_truncation(self) -> None:
        """An empty parent would violate the text_not_blank constraint."""
        tight = ChunkingConfig(
            child_max_tokens=5, child_min_tokens=1, parent_max_tokens=2
        )
        source = blocks("word " * 50)

        result = chunk_blocks(source, config=tight, count_tokens=words)

        assert all(chunk.text.strip() for chunk in result)

    def test_a_single_word_longer_than_the_budget_is_divided(self) -> None:
        """A URL or an unbroken digit run. Emitting it whole loses its tail."""
        config = ChunkingConfig(child_max_tokens=10, child_min_tokens=1)
        source = blocks("x" * 200)

        result = chunk_blocks(source, config=config, count_tokens=len)

        assert all(chunk.token_count <= 10 for chunk in children(result))
        assert "".join(chunk.text for chunk in children(result)) == "x" * 200

    def test_unordered_input_is_refused_rather_than_silently_scrambled(self) -> None:
        """A caller reading rows without ORDER BY would assemble prose that is not."""
        source = [
            block("third", ordinal=2),
            block("first", ordinal=0),
            block("second", ordinal=1),
        ]

        with pytest.raises(CoverageError, match="document order"):
            chunk_blocks(source, config=SMALL, count_tokens=words)


class TestShortTailAbsorption:
    """``child_min_tokens`` was declared and never read.

    The accumulator merged while filling, but whatever remained at the end was
    emitted regardless of size. On a real filing that left 57 children under the
    stated 48-token floor — fragments whose embeddings are ambiguous out of
    context, which is the failure a minimum exists to prevent.
    """

    def test_a_short_tail_is_merged_backwards(self) -> None:
        config = ChunkingConfig(child_max_tokens=12, child_min_tokens=4)
        source = blocks("a " * 10, "tail")

        result = chunk_blocks(source, config=config, count_tokens=words)

        assert len(children(result)) == 1
        assert "tail" in children(result)[0].text

    def test_merging_never_exceeds_the_budget(self) -> None:
        """An over-budget chunk is truncated by the model, which is worse."""
        config = ChunkingConfig(child_max_tokens=10, child_min_tokens=8)
        source = blocks("a " * 10, "b " * 2)

        result = chunk_blocks(source, config=config, count_tokens=words)

        assert all(
            chunk.token_count <= config.child_max_tokens
            for chunk in children(result)
        )

    def test_an_unmergeable_tail_is_kept_rather_than_dropped(self) -> None:
        """Dropping it would lose the blocks, which coverage forbids."""
        config = ChunkingConfig(child_max_tokens=10, child_min_tokens=8)
        source = blocks("a " * 10, "b " * 2)

        result = chunk_blocks(source, config=config, count_tokens=words)
        placed = {
            element_id
            for chunk in children(result)
            for element_id in chunk.source_element_ids
        }

        assert placed == {b.element_id for b in source}

    def test_a_merged_chunk_keeps_both_sets_of_sources(self) -> None:
        config = ChunkingConfig(child_max_tokens=12, child_min_tokens=4)
        source = blocks("a " * 10, "tail")

        result = chunk_blocks(source, config=config, count_tokens=words)

        assert children(result)[0].source_element_ids == tuple(
            b.element_id for b in source
        )

    def test_a_lone_short_chunk_has_nothing_to_merge_into(self) -> None:
        config = ChunkingConfig(child_max_tokens=10, child_min_tokens=8)
        source = blocks("tiny")

        result = chunk_blocks(source, config=config, count_tokens=words)

        assert len(children(result)) == 1
