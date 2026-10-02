"""Tests for the PyMuPDF table detectors.

Two of these carry most of the weight.

:meth:`TestBorderlessTables.test_the_lines_strategy_finds_nothing_at_all` pins the
finding that reshaped this phase: on a borderless copy of a table it reads
correctly when ruled, ``lines`` returns **zero** tables. A total miss is far harder
to notice in aggregate counts than a bad read, and it is why selection may end up
being a routing rule rather than a winner.

:class:`TestRotatedPages` is the regression guard for the defect that cost a real
annual report the citation coordinates on 78 of its 369 pages. Cells inherit that
trap exactly as blocks did.
"""

import io

import pymupdf
import pytest

from finsight.extraction.contracts import DocumentUnreadableError
from finsight.extraction.pdf.pymupdf_tables import (
    STRATEGY_LINES,
    STRATEGY_TEXT,
    PyMuPdfTableDetector,
)
from finsight.extraction.tables.contracts import DetectedTable, TableDetector
from finsight.extraction.tables.structure import derive
from pdf_fixtures import (
    build_financial_table_pdf,
    build_malformed_pdf,
    build_rotated_table_pdf,
    build_truncated_pdf,
)


def detect(content: bytes, strategy: str) -> dict[int, tuple[DetectedTable, ...]]:
    detector = PyMuPdfTableDetector(strategy=strategy)
    found = detector.detect(io.BytesIO(content))
    return {page: tuple(tables) for page, tables in found.items()}


def only_table(content: bytes, strategy: str) -> DetectedTable:
    found = detect(content, strategy)
    tables = found[1]
    assert len(tables) == 1
    return tables[0]


class TestProtocolConformance:
    def test_the_detector_satisfies_the_contract(self) -> None:
        assert isinstance(PyMuPdfTableDetector(), TableDetector)

    def test_the_strategy_is_reported(self) -> None:
        assert PyMuPdfTableDetector(strategy=STRATEGY_TEXT).strategy == STRATEGY_TEXT

    def test_the_method_and_version_are_reported(self) -> None:
        """§16.5 requires the method *and* its version, per element."""
        detector = PyMuPdfTableDetector()

        assert detector.method == "pymupdf"
        assert detector.method_version == str(pymupdf.__version__)

    def test_an_unknown_strategy_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy"):
            PyMuPdfTableDetector(strategy="vision")


class TestRuledTables:
    def test_the_grid_shape_matches_the_ruling(self) -> None:
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)

        assert (table.row_count, table.column_count) == (7, 3)

    def test_the_spanning_header_reports_an_absent_neighbour(self) -> None:
        """The only signal a merge gives, and what span derivation is built on."""
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)

        assert table.cell_at(0, 1).text.strip() == "Year ended March 31"
        assert table.cell_at(0, 2).is_absent is True

    def test_an_empty_cell_is_reported_with_a_box(self) -> None:
        """Empty and absent are different facts, and the detector keeps them apart."""
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)
        cell = table.cell_at(4, 2)

        assert cell.is_absent is False
        assert cell.text.strip() == ""

    def test_parenthesised_negatives_survive_verbatim(self) -> None:
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)

        assert table.cell_at(6, 1).text.strip() == "(45)"
        assert table.cell_at(6, 2).text.strip() == "(30)"

    def test_a_footnote_marker_survives_inside_its_cell(self) -> None:
        """Preserved rather than separated — separation happens above this layer."""
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)

        assert table.cell_at(5, 0).text.strip() == "Other income (a)"

    def test_every_cell_sits_inside_the_table_region(self) -> None:
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)
        x0, y0, x1, y1 = table.bbox

        for cell in table.cells:
            if cell.bbox is None:
                continue
            assert cell.bbox[0] >= x0 - 1.0
            assert cell.bbox[1] >= y0 - 1.0
            assert cell.bbox[2] <= x1 + 1.0
            assert cell.bbox[3] <= y1 + 1.0


class TestBorderlessTables:
    def test_the_lines_strategy_finds_nothing_at_all(self) -> None:
        """The finding that reshaped the phase.

        Not a degraded read of the table — no table. A strategy that silently
        returns nothing on a whole class of financial table cannot be selected on
        aggregate detection counts, because its failures are invisible in them.
        """
        assert detect(build_financial_table_pdf(ruled=False), STRATEGY_LINES) == {}

    def test_the_text_strategy_finds_it(self) -> None:
        table = only_table(build_financial_table_pdf(ruled=False), STRATEGY_TEXT)

        assert table.column_count == 3

    def test_the_text_strategy_over_segments_the_rows(self) -> None:
        """Recorded as observed behaviour, not endorsed.

        The table has seven rows. This strategy inserts a phantom row between every
        real one, which is the over-detection the corpus counts hinted at — roughly
        one table per page where ``lines`` found a quarter of that.
        """
        ruled = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)
        borderless = only_table(build_financial_table_pdf(ruled=False), STRATEGY_TEXT)

        assert borderless.row_count > ruled.row_count

    def test_the_two_strategies_are_complementary_not_competing(self) -> None:
        """Neither dominates: each reads what the other cannot."""
        borderless = build_financial_table_pdf(ruled=False)
        ruled = build_financial_table_pdf(ruled=True)

        assert detect(borderless, STRATEGY_LINES) == {}
        assert detect(borderless, STRATEGY_TEXT) != {}
        assert detect(ruled, STRATEGY_LINES) != {}


class TestRotatedPages:
    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_cells_stay_inside_their_page(self, rotation: int) -> None:
        """The 78-page defect, reachable again at cell level.

        ``find_tables`` reports geometry in unrotated space while the page
        rectangle has rotation applied, so a missing transform puts a cell's
        citation box somewhere the reader is not looking.
        """
        content = build_rotated_table_pdf(rotation)
        with pymupdf.open(stream=content, filetype="pdf") as document:
            page_rect = document[0].rect

        found = detect(content, STRATEGY_LINES)
        assert found, f"no table detected at rotation {rotation}"

        for tables in found.values():
            for table in tables:
                for cell in table.cells:
                    if cell.bbox is None:
                        continue
                    assert cell.bbox[0] >= page_rect.x0 - 1.0
                    assert cell.bbox[1] >= page_rect.y0 - 1.0
                    assert cell.bbox[2] <= page_rect.x1 + 1.0
                    assert cell.bbox[3] <= page_rect.y1 + 1.0

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_table_region_stays_inside_its_page(self, rotation: int) -> None:
        content = build_rotated_table_pdf(rotation)
        with pymupdf.open(stream=content, filetype="pdf") as document:
            page_rect = document[0].rect

        for tables in detect(content, STRATEGY_LINES).values():
            for table in tables:
                assert table.bbox[0] >= page_rect.x0 - 1.0
                assert table.bbox[1] >= page_rect.y0 - 1.0
                assert table.bbox[2] <= page_rect.x1 + 1.0
                assert table.bbox[3] <= page_rect.y1 + 1.0


class TestPagesWithoutTables:
    def test_a_page_with_no_table_is_absent_rather_than_empty(self) -> None:
        """"None found" and "not examined" must not look alike to a caller."""
        found = detect(build_malformed_pdf(), STRATEGY_LINES)

        assert found == {}


class TestControlledFailure:
    def test_an_unopenable_document_is_refused(self) -> None:
        with pytest.raises(DocumentUnreadableError, match="could not be parsed"):
            detect(build_truncated_pdf(), STRATEGY_LINES)


class TestDerivationOnRealDetectorOutput:
    """The derivation is unit-tested on hand-built grids; this proves it on real output."""

    def test_header_paths_resolve_through_the_spanning_header(self) -> None:
        table = only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES)
        derived = derive(table)

        assert derived.cell_at(3, 1).header_path == ("Year ended March 31", "2025")
        assert derived.cell_at(3, 2).header_path == ("Year ended March 31", "2024")

    def test_the_units_row_is_recorded_and_excluded_from_headers(self) -> None:
        derived = derive(only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES))

        assert derived.units_rows == ("(Rs in crore)",)
        assert derived.header_row_indices == (0, 1)

    def test_the_footnote_marker_is_separated_without_touching_the_text(self) -> None:
        derived = derive(only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES))
        cell = derived.cell_at(5, 0)

        assert cell.text.strip() == "Other income (a)"
        assert cell.footnote_refs == ("a",)

    def test_the_indented_row_keeps_its_parent(self) -> None:
        """Geometry from a real detector, not a hand-set bbox."""
        derived = derive(only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES))

        assert derived.cell_at(4, 1).row_label_path == (
            "Deposits",
            "Of which: term deposits",
        )

    def test_the_negative_keeps_its_sign_and_gains_no_marker(self) -> None:
        derived = derive(only_table(build_financial_table_pdf(ruled=True), STRATEGY_LINES))
        cell = derived.cell_at(6, 1)

        assert cell.text.strip() == "(45)"
        assert cell.footnote_refs == ()
