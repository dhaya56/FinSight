"""Tests for the PyMuPDF producer.

The coordinate test is the one that matters. A silent origin flip would leave
every stored citation pointing at the mirror image of its evidence, and nothing
downstream could detect it — the offsets would still resolve, the highlight
would just be in the wrong place.
"""

import io

import pytest

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    ExtractionState,
    PageLocation,
)
from finsight.extraction.contracts import (
    DocumentUnreadableError,
    PdfProducer,
    derive_state,
)
from finsight.extraction.pdf.pymupdf_adapter import PyMuPdfProducer
from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED
from pdf_fixtures import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PlacedText,
    build_encrypted_pdf,
    build_financial_table_pdf,
    build_hyphenated_pdf,
    build_image_only_pdf,
    build_malformed_pdf,
    build_mixed_page_size_pdf,
    build_pdf,
    build_rotated_pdf,
    build_staggered_columns_pdf,
    build_truncated_pdf,
    build_two_column_pdf,
)


@pytest.fixture
def producer() -> PyMuPdfProducer:
    return PyMuPdfProducer()


def extract(producer: PyMuPdfProducer, data: bytes) -> tuple[ExtractedElement, ...]:
    return producer.produce(io.BytesIO(data))


def only_page(producer: PyMuPdfProducer, data: bytes) -> ExtractedElement:
    pages = extract(producer, data)
    assert len(pages) == 1
    return pages[0]


class TestCoordinateConvention:
    """Pins the top-left origin against a writer that uses the bottom-left one."""

    def test_the_drawn_baseline_falls_inside_the_reported_box(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The assertion is exact and needs no tolerance.

        ReportLab puts the text baseline 700pt up from the bottom, so it sits
        ``PAGE_HEIGHT - 700`` down from the top. A box reported in top-left
        coordinates must straddle that line. A box reported in bottom-left
        coordinates would sit near y=700 and could not.
        """
        data = build_pdf([[PlacedText("Revenue", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)
        block = page.children[0]
        assert isinstance(block.location, BlockLocation)
        top, bottom = block.location.bbox[1], block.location.bbox[3]

        baseline_from_top = PAGE_HEIGHT - 700

        assert top < baseline_from_top < bottom

    def test_a_block_near_the_top_reports_a_small_y(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The same fact stated the other way round, so the intent is unmissable."""
        data = build_pdf([[PlacedText("Header", x=72, y_from_bottom=800)]])
        page = only_page(producer, data)
        block = page.children[0]
        assert isinstance(block.location, BlockLocation)

        assert block.location.bbox[1] < PAGE_HEIGHT / 2

    def test_the_horizontal_origin_is_the_left_edge(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf([[PlacedText("Revenue", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)
        block = page.children[0]
        assert isinstance(block.location, BlockLocation)

        assert block.location.bbox[0] == pytest.approx(72.0, abs=0.5)

    def test_a_lower_block_reports_a_larger_y_than_a_higher_one(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """y increases downward, which is what the reading-order key assumes."""
        data = build_pdf(
            [
                [
                    PlacedText("higher", x=72, y_from_bottom=700),
                    PlacedText("lower", x=72, y_from_bottom=600),
                ]
            ]
        )
        page = only_page(producer, data)
        boxes = [child.location for child in page.children]
        assert all(isinstance(box, BlockLocation) for box in boxes)

        tops = [box.bbox[1] for box in boxes if isinstance(box, BlockLocation)]
        assert tops[0] < tops[1]


class TestReadingOrder:
    def test_blocks_are_ordered_down_the_page(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf(
            [
                [
                    PlacedText("first", x=72, y_from_bottom=700),
                    PlacedText("second", x=72, y_from_bottom=600),
                ]
            ]
        )
        page = only_page(producer, data)

        assert [child.text.strip() for child in page.children if child.text] == [
            "first",
            "second",
        ]

    def test_drawing_order_does_not_decide_reading_order(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Drawn bottom-first; must still read top-first.

        A PDF stores the order a generator happened to emit, which need not be
        the order a person reads. This proves FinSight sorts rather than trusts.
        """
        data = build_pdf(
            [
                [
                    PlacedText("second", x=72, y_from_bottom=600),
                    PlacedText("first", x=72, y_from_bottom=700),
                ]
            ]
        )
        page = only_page(producer, data)

        assert [child.text.strip() for child in page.children if child.text] == [
            "first",
            "second",
        ]

    def test_ordinals_are_dense_and_start_at_zero(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf(
            [
                [
                    PlacedText(f"line {index}", x=72, y_from_bottom=700 - index * 40)
                    for index in range(4)
                ]
            ]
        )
        page = only_page(producer, data)

        assert [child.ordinal for child in page.children] == [0, 1, 2, 3]


class TestVerbatimText:
    def test_text_is_kept_exactly_as_the_producer_returned_it(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Including the newline PyMuPDF appends to every block.

        That newline is this producer's artefact, not a character in the
        document, and it is still not trimmed: citations are offsets into the
        stored string, and trimming "obvious" noise is where normalisation
        starts.
        """
        data = build_pdf([[PlacedText("Revenue", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)

        assert page.children[0].text == "Revenue\n"

    def test_char_count_matches_the_stored_text(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf([[PlacedText("Revenue", x=72, y_from_bottom=700)]])
        block = only_page(producer, data).children[0]

        assert block.text is not None
        assert block.char_count == len(block.text)

    def test_a_rupee_amount_survives_extraction(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Lakh/crore grouping must not be reinterpreted on the way through."""
        data = build_pdf([[PlacedText("1,23,456.78", x=72, y_from_bottom=700)]])
        block = only_page(producer, data).children[0]

        assert block.text == "1,23,456.78\n"


class TestPageStructure:
    def test_every_page_becomes_an_element(self, producer: PyMuPdfProducer) -> None:
        data = build_pdf(
            [
                [PlacedText("one", x=72, y_from_bottom=700)],
                [PlacedText("two", x=72, y_from_bottom=700)],
                [PlacedText("three", x=72, y_from_bottom=700)],
            ]
        )
        pages = extract(producer, data)

        assert [page.element_type for page in pages] == [ElementType.PAGE] * 3
        assert [page.ordinal for page in pages] == [0, 1, 2]

    def test_locators_are_one_based_page_references(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf(
            [
                [PlacedText("one", x=72, y_from_bottom=700)],
                [PlacedText("two", x=72, y_from_bottom=700)],
            ]
        )
        pages = extract(producer, data)

        assert [page.locator for page in pages] == ["p. 1", "p. 2"]

    def test_a_block_cites_the_page_it_sits_on(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf(
            [
                [PlacedText("one", x=72, y_from_bottom=700)],
                [PlacedText("two", x=72, y_from_bottom=700)],
            ]
        )
        pages = extract(producer, data)

        assert pages[1].children[0].locator == "p. 2"

    def test_page_dimensions_are_recorded(self, producer: PyMuPdfProducer) -> None:
        data = build_pdf([[PlacedText("one", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)
        assert isinstance(page.location, PageLocation)

        assert page.location.page_number == 1
        assert page.location.width == pytest.approx(PAGE_WIDTH, abs=0.1)
        assert page.location.height == pytest.approx(PAGE_HEIGHT, abs=0.1)
        assert page.location.rotation == 0

    def test_page_rotation_is_recorded(self, producer: PyMuPdfProducer) -> None:
        """A viewer applies rotation, so a highlight must agree with it."""
        page = only_page(producer, build_rotated_pdf(90))
        assert isinstance(page.location, PageLocation)

        assert page.location.rotation == 90

    def test_a_page_carries_no_text_of_its_own(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Text lives on blocks; a page is a container that addresses them."""
        data = build_pdf([[PlacedText("one", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)

        assert page.text is None
        assert page.char_count is None


class TestCoverageGaps:
    def test_a_page_with_no_text_is_recorded_rather_than_omitted(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Silence would be indistinguishable from a blank page (§11.11)."""
        data = build_pdf([[]])
        page = only_page(producer, data)

        assert page.failure_reason == NO_TEXT_EXTRACTED
        assert page.children == ()

    def test_an_image_only_page_is_a_coverage_gap(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The shape of a scanned filing. Until OCR exists it was not searched."""
        page = only_page(producer, build_image_only_pdf())

        assert page.failure_reason == NO_TEXT_EXTRACTED

    def test_a_gap_makes_the_run_partial_rather_than_failed(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf([[PlacedText("one", x=72, y_from_bottom=700)], []])
        pages = extract(producer, data)

        assert derive_state(pages).value == "partial"

    def test_a_clean_document_succeeds(self, producer: PyMuPdfProducer) -> None:
        data = build_pdf([[PlacedText("one", x=72, y_from_bottom=700)]])

        assert derive_state(extract(producer, data)).value == "succeeded"


class TestLayoutFixtures:
    """A synthetic control group.

    When a real filing extracts badly, these say whether the document is unusual
    or our code is wrong. Without them every real-document failure is a guess.
    """

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_a_rotated_page_still_yields_text(
        self,
        producer: PyMuPdfProducer,
        rotation: int,
    ) -> None:
        """Landscape fold-outs are common in filings for wide tables."""
        page = only_page(producer, build_rotated_pdf(rotation))
        assert isinstance(page.location, PageLocation)

        assert page.location.rotation == rotation
        assert page.failure_reason is None
        assert len(page.children) == 1

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_a_block_box_lies_within_its_own_page(
        self,
        producer: PyMuPdfProducer,
        rotation: int,
    ) -> None:
        """The assertion that was missing, and the defect it would have caught.

        PyMuPDF reports text in unrotated page space while ``page.rect`` is the
        rotated display box, so on a ``/Rotate 90`` page an untransformed block
        sits outside the page it belongs to. Checking only that text came back
        passed regardless; checking only a box near the origin passed too,
        because that corner is inside both spaces.

        On the development corpus this was 2,401 blocks across 78 pages of one
        annual report, every one of them a citation pointing somewhere the
        reader is not looking.
        """
        page = only_page(producer, build_rotated_pdf(rotation))
        assert isinstance(page.location, PageLocation)
        block = page.children[0]
        assert isinstance(block.location, BlockLocation)

        x0, y0, x1, y1 = block.location.bbox
        assert x0 >= 0 and x1 <= page.location.width + 1
        assert y0 >= 0 and y1 <= page.location.height + 1

    def test_an_untransformed_box_would_fail_that_check(self) -> None:
        """Proves the fixture can actually fail, rather than passing by luck.

        Without this, a future change that dropped the rotation transform could
        be met by a fixture whose text happened to sit inside both coordinate
        spaces, and the suite would stay green.
        """
        import pymupdf

        with pymupdf.open(stream=build_rotated_pdf(90), filetype="pdf") as document:
            pdf_page = document[0]
            raw = next(e for e in pdf_page.get_text("blocks") if e[6] == 0)
            rotation = pdf_page.rotation
            width, height = pdf_page.rect.width, pdf_page.rect.height

        assert rotation == 90
        assert raw[3] > height or raw[2] > width

    def test_page_dimensions_vary_within_one_document(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Geometry is per page; caching the first page's size mis-places citations."""
        pages = extract(producer, build_mixed_page_size_pdf())
        sizes = [
            (round(page.location.width), round(page.location.height))
            for page in pages
            if isinstance(page.location, PageLocation)
        ]

        assert len(sizes) == 2
        assert sizes[0] != sizes[1]

    def test_aligned_columns_read_in_the_correct_order(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The control: each column merges into one block, and both start level."""
        page = only_page(producer, build_two_column_pdf())
        texts = [child.text or "" for child in page.children]

        assert len(texts) == 2
        assert texts[0].startswith("Revenue from operations")
        assert texts[1].startswith("Finance costs")

    def test_staggered_columns_are_read_across_rather_than_down(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The known multi-column failure, asserted rather than left implicit.

        Correct reading order is the whole left column, then the right:
        LEFT TOP, LEFT BOTTOM, RIGHT MIDDLE. The positional rule interleaves
        them. Fixing this needs real filings and a recorded evaluation (§12.9),
        so the failure is pinned here instead of hidden.
        """
        page = only_page(producer, build_staggered_columns_pdf())
        order = [(child.text or "").strip() for child in page.children]

        assert order == ["LEFT TOP", "RIGHT MIDDLE", "LEFT BOTTOM"]

    def test_a_hyphen_at_a_line_break_is_preserved(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """Rejoining is a retrieval-representation decision (§18.7), not extraction's."""
        page = only_page(producer, build_hyphenated_pdf())

        assert page.children[0].text == "consoli-\ndated statements\n"


class TestControlledFailure:
    def test_an_encrypted_document_is_refused(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        with pytest.raises(DocumentUnreadableError, match="password"):
            extract(producer, build_encrypted_pdf())

    def test_unparsable_bytes_are_refused(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        with pytest.raises(DocumentUnreadableError, match="could not be parsed"):
            extract(producer, b"this is not a PDF")

    def test_an_empty_stream_is_refused(self, producer: PyMuPdfProducer) -> None:
        with pytest.raises(DocumentUnreadableError):
            extract(producer, b"")

    def test_a_truncated_document_is_refused(self, producer: PyMuPdfProducer) -> None:
        """Signed as a PDF, so it reaches the producer rather than intake."""
        with pytest.raises(DocumentUnreadableError):
            extract(producer, build_truncated_pdf())

    @pytest.mark.parametrize(
        "builder", [build_truncated_pdf, build_encrypted_pdf]
    )
    def test_unopenable_documents_raise(
        self,
        producer: PyMuPdfProducer,
        builder: object,
    ) -> None:
        """No negative fixture may raise something the caller cannot classify.

        An escaping ``RuntimeError`` from the parser would reach the service as
        an unknown failure and be recorded as a defect rather than as a document
        FinSight declined to read.
        """
        assert callable(builder)
        with pytest.raises(DocumentUnreadableError):
            extract(producer, builder())

    def test_a_recovered_document_yields_nothing_rather_than_raising(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """The second shape of controlled failure, and the less obvious one.

        PyMuPDF **repairs** a broken cross-reference table rather than refusing
        it, so a structurally malformed file opens cleanly with zero pages. That
        is why ``derive_state`` treats an empty result as failed: a recovered
        document with nothing in it is indistinguishable from a genuinely empty
        one, and calling either a success would publish a filing FinSight never
        read.
        """
        elements = extract(producer, build_malformed_pdf())

        assert elements == ()
        assert derive_state(elements) is ExtractionState.FAILED

    def test_failure_messages_carry_no_document_content(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """An extraction failure must not leak filing text into a log."""
        secret = "ACQUISITION OF SUBSIDIARY"
        data = build_pdf([[PlacedText(secret, x=72, y_from_bottom=700)]])

        with pytest.raises(DocumentUnreadableError) as caught:
            extract(producer, data[:80])

        assert secret not in str(caught.value)


class TestProducerIdentity:
    def test_the_method_is_recorded_on_every_element(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        data = build_pdf([[PlacedText("one", x=72, y_from_bottom=700)]])
        page = only_page(producer, data)

        assert page.extraction_method == "pymupdf"
        assert page.children[0].extraction_method == "pymupdf"

    def test_the_version_is_recorded_separately_from_the_method(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        """§16.5 requires the method *and* its version, not one standing in."""
        import pymupdf

        assert producer.method_version == pymupdf.__version__
        assert producer.method_version != producer.method

    def test_it_satisfies_the_producer_protocol(
        self,
        producer: PyMuPdfProducer,
    ) -> None:
        assert isinstance(producer, PdfProducer)

    def test_extraction_is_reproducible(self, producer: PyMuPdfProducer) -> None:
        """Same bytes, same elements — a run must be reproducible from its record."""
        data = build_pdf(
            [
                [
                    PlacedText("first", x=72, y_from_bottom=700),
                    PlacedText("second", x=72, y_from_bottom=600),
                ]
            ]
        )

        assert extract(producer, data) == extract(producer, data)


class TestTablesOnAPage:
    """Tables arrive as page children alongside blocks, carrying their semantics."""

    def test_a_ruled_table_becomes_a_table_element(
        self, producer: PyMuPdfProducer
    ) -> None:
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]

        tables = [
            child
            for child in page.children
            if child.element_type is ElementType.TABLE
        ]

        assert len(tables) == 1

    def test_blocks_and_tables_share_one_ordinal_sequence(
        self, producer: PyMuPdfProducer
    ) -> None:
        """They share a page, so they share its reading order.

        Appending tables after the blocks would assert that every table sits below
        every paragraph, which is false the moment a statement opens a section.
        """
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]

        ordinals = [child.ordinal for child in page.children]

        assert ordinals == list(range(len(page.children)))

    def test_a_table_at_the_top_of_a_page_is_ordered_first(
        self, producer: PyMuPdfProducer
    ) -> None:
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]

        assert page.children[0].element_type is ElementType.TABLE

    def test_absent_cells_do_not_become_elements(
        self, producer: PyMuPdfProducer
    ) -> None:
        """The fixture's grid is 7x3 with one position the spanning header covers.

        A row for "something may be missing here" would record a conclusion the
        evidence cannot support, since a merge and a detection failure look alike.
        """
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]
        table = next(
            child
            for child in page.children
            if child.element_type is ElementType.TABLE
        )

        assert len(table.children) == 20

    def test_a_value_cell_carries_its_full_context(
        self, producer: PyMuPdfProducer
    ) -> None:
        """The whole point of the layer. Without any one of these the number is wrong.

        Header path says which period, row-label path says which concept and that
        it is a component rather than a total, units says which scale.
        """
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]
        table = next(
            child
            for child in page.children
            if child.element_type is ElementType.TABLE
        )
        cell = next(
            child for child in table.children if child.text.strip() == "1,234"
        )

        assert cell.semantics is not None
        assert cell.semantics.header_path == ("Year ended March 31", "2025")
        assert cell.semantics.row_label_path == ("Deposits",)
        assert cell.semantics.units == "(Rs in crore)"

    def test_a_cell_locator_addresses_its_grid_position_one_based(
        self, producer: PyMuPdfProducer
    ) -> None:
        """One-based for a reader; the stored indices stay zero-based for a grid."""
        page = extract(producer, build_financial_table_pdf(ruled=True))[0]
        table = next(
            child
            for child in page.children
            if child.element_type is ElementType.TABLE
        )
        cell = next(
            child for child in table.children if child.text.strip() == "1,234"
        )

        assert cell.locator == "p. 1, table 1, R4C2"
        assert (cell.location.row_index, cell.location.column_index) == (3, 1)

    def test_a_page_without_a_table_yields_only_blocks(
        self, producer: PyMuPdfProducer
    ) -> None:
        page = extract(producer, build_two_column_pdf())[0]

        assert all(
            child.element_type is ElementType.BLOCK for child in page.children
        )

    def test_the_table_strategy_is_reported(self, producer: PyMuPdfProducer) -> None:
        """Provisional, so which one ran has to be visible rather than implied."""
        assert producer.table_strategy == "lines"
