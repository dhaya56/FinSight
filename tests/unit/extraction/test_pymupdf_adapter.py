"""Tests for the PyMuPDF producer.

The coordinate test is the one that matters. A silent origin flip would leave
every stored citation pointing at the mirror image of its evidence, and nothing
downstream could detect it — the offsets would still resolve, the highlight
would just be in the wrong place.
"""

import io

import pytest
from pdf_fixtures import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PlacedText,
    build_encrypted_pdf,
    build_image_only_pdf,
    build_pdf,
)

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    PageLocation,
)
from finsight.extraction.contracts import (
    DocumentUnreadableError,
    PdfProducer,
    derive_state,
)
from finsight.extraction.pdf.pymupdf_adapter import PyMuPdfProducer
from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED


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
        data = build_pdf(
            [[PlacedText("one", x=72, y_from_bottom=700)]], rotation=90
        )
        page = only_page(producer, data)
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
