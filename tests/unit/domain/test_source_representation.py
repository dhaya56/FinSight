"""Tests for the source-representation domain rules.

The verbatim-text tests are the important ones. Every other rule here protects a
constraint the database also enforces; the verbatim rule has no database
equivalent, because no constraint can detect that text was tidied before it
arrived.
"""

import pytest

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    ExtractionState,
    InvalidSourceElementError,
    PageLocation,
    location_from_mapping,
)

METHOD = "pymupdf"
METHOD_VERSION = "0.0.0-test"


def block(text: str | None = "text", **overrides: object) -> ExtractedElement:
    """A minimal valid block, so each test states only what it is about."""
    fields: dict[str, object] = {
        "element_type": ElementType.BLOCK,
        "ordinal": 0,
        "locator": "p. 1",
        "location": BlockLocation(bbox=(0.0, 0.0, 10.0, 10.0)),
        "extraction_method": METHOD,
        "extraction_method_version": METHOD_VERSION,
        "text": text,
    }
    fields.update(overrides)
    return ExtractedElement(**fields)  # type: ignore[arg-type]


class TestVerbatimText:
    """Extraction preserves and interprets nothing (§14.2, §14.9)."""

    @pytest.mark.parametrize(
        "original",
        [
            "₹1,23,456.78",  # rupee sign with lakh/crore grouping
            "1\u00a0234,56",  # non-breaking space in a European numeral
            "ﬁnancial",  # fi ligature
            "लाभ",  # Devanagari
            "  leading and trailing  ",
            "line one\r\nline two",
            "soft\u00adhyphen",  # soft hyphen: only a renderer may drop it
            "zero\u200bwidth",  # zero-width space
        ],
    )
    def test_text_is_stored_exactly_as_given(self, original: str) -> None:
        assert block(original).text == original

    def test_char_count_is_derived_from_the_stored_text(self) -> None:
        """A supplied count could disagree with its text; a derived one cannot."""
        original = "₹1,23,456.78"

        assert block(original).char_count == len(original)

    def test_char_count_is_none_without_text(self) -> None:
        assert block(None).char_count is None

    def test_an_empty_string_is_not_the_same_as_no_text(self) -> None:
        """A page that yielded an empty string is a fact; a container is not."""
        assert block("").char_count == 0
        assert block(None).char_count is None


class TestPageLocation:
    def test_round_trips_through_a_mapping(self) -> None:
        location = PageLocation(page_number=12, width=595.0, height=842.0, rotation=90)

        assert PageLocation.from_mapping(location.to_mapping()) == location

    def test_page_numbers_are_one_based(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="one-based"):
            PageLocation(page_number=0, width=595.0, height=842.0, rotation=0)

    @pytest.mark.parametrize(("width", "height"), [(0.0, 842.0), (595.0, -1.0)])
    def test_dimensions_must_be_positive(self, width: float, height: float) -> None:
        with pytest.raises(InvalidSourceElementError, match="positive"):
            PageLocation(page_number=1, width=width, height=height, rotation=0)

    @pytest.mark.parametrize("rotation", [45, -90, 360])
    def test_rotation_must_be_a_quarter_turn(self, rotation: int) -> None:
        with pytest.raises(InvalidSourceElementError, match="rotation"):
            PageLocation(page_number=1, width=595.0, height=842.0, rotation=rotation)

    def test_unknown_keys_are_refused_rather_than_ignored(self) -> None:
        """An ignored key is how semantics get into an address payload."""
        payload = PageLocation(
            page_number=1, width=595.0, height=842.0, rotation=0
        ).to_mapping()
        payload["section_title"] = "Risk Factors"

        with pytest.raises(InvalidSourceElementError, match="unknown"):
            PageLocation.from_mapping(payload)

    def test_missing_keys_are_reported(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="missing"):
            PageLocation.from_mapping({"page_number": 1})


class TestBlockLocation:
    def test_round_trips_through_a_mapping(self) -> None:
        location = BlockLocation(bbox=(10.5, 20.0, 100.25, 40.0))

        assert BlockLocation.from_mapping(location.to_mapping()) == location

    def test_a_bbox_arriving_as_a_json_list_becomes_a_tuple(self) -> None:
        """JSONB returns arrays as lists; equality must not depend on that."""
        restored = BlockLocation.from_mapping({"bbox": [1, 2, 3, 4]})

        assert restored == BlockLocation(bbox=(1.0, 2.0, 3.0, 4.0))

    @pytest.mark.parametrize(
        "bbox",
        [(100.0, 0.0, 10.0, 10.0), (0.0, 100.0, 10.0, 10.0)],
    )
    def test_an_inverted_box_is_refused(
        self, bbox: tuple[float, float, float, float]
    ) -> None:
        """Pins the top-left origin: y must increase downward."""
        with pytest.raises(InvalidSourceElementError, match="top-left"):
            BlockLocation(bbox=bbox)

    def test_a_degenerate_box_is_allowed(self) -> None:
        """A zero-height rule or an empty line is a real region on a page."""
        assert BlockLocation(bbox=(10.0, 10.0, 10.0, 10.0)).bbox == (
            10.0,
            10.0,
            10.0,
            10.0,
        )

    @pytest.mark.parametrize("raw", [[1, 2, 3], [1, 2, 3, 4, 5]])
    def test_a_box_must_hold_four_coordinates(self, raw: list[int]) -> None:
        with pytest.raises(InvalidSourceElementError, match="four"):
            BlockLocation.from_mapping({"bbox": raw})

    def test_a_string_is_not_a_box(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="sequence"):
            BlockLocation.from_mapping({"bbox": "0,0,10,10"})


class TestLocationDispatch:
    def test_rebuilds_the_type_matching_the_element(self) -> None:
        page = PageLocation(page_number=3, width=595.0, height=842.0, rotation=0)

        assert location_from_mapping(ElementType.PAGE, page.to_mapping()) == page

    def test_a_page_payload_is_refused_for_a_block(self) -> None:
        page = PageLocation(page_number=3, width=595.0, height=842.0, rotation=0)

        with pytest.raises(InvalidSourceElementError):
            location_from_mapping(ElementType.BLOCK, page.to_mapping())


class TestExtractedElement:
    def test_a_page_requires_a_page_location(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="PageLocation"):
            block(element_type=ElementType.PAGE)

    def test_a_block_requires_a_block_location(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="BlockLocation"):
            block(
                location=PageLocation(
                    page_number=1, width=595.0, height=842.0, rotation=0
                )
            )

    def test_text_and_a_failure_reason_are_mutually_exclusive(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="never both"):
            block("text", failure_reason="unreadable")

    def test_a_failed_element_carries_a_reason_and_no_text(self) -> None:
        """A region that could not be read is a row, not a missing row (§11.11)."""
        failed = block(None, failure_reason="unreadable")

        assert failed.text is None
        assert failed.failure_reason == "unreadable"

    def test_ordinals_are_not_negative(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="ordinal"):
            block(ordinal=-1)

    @pytest.mark.parametrize("locator", ["", "   "])
    def test_a_locator_is_required(self, locator: str) -> None:
        with pytest.raises(InvalidSourceElementError, match="locator"):
            block(locator=locator)

    def test_the_extraction_method_is_required(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="extraction_method"):
            block(extraction_method="")

    def test_the_extraction_method_version_is_required(self) -> None:
        """§16.5 requires the method *and* its version, not one standing for both."""
        with pytest.raises(InvalidSourceElementError, match="version"):
            block(extraction_method_version="")

    def test_a_block_has_no_children(self) -> None:
        with pytest.raises(InvalidSourceElementError, match="no child"):
            block(children=(block(),))

    def test_a_page_holds_its_blocks_in_order(self) -> None:
        page = ExtractedElement(
            element_type=ElementType.PAGE,
            ordinal=0,
            locator="p. 1",
            location=PageLocation(page_number=1, width=595.0, height=842.0, rotation=0),
            extraction_method=METHOD,
            extraction_method_version=METHOD_VERSION,
            children=(block("first", ordinal=0), block("second", ordinal=1)),
        )

        assert [child.text for child in page.children] == ["first", "second"]

    def test_elements_are_immutable(self) -> None:
        """Nothing downstream may edit an element after the producer emitted it."""
        element = block("original")

        with pytest.raises(AttributeError):
            element.text = "edited"  # type: ignore[misc]


class TestExtractionState:
    def test_states_are_the_three_the_schema_allows(self) -> None:
        assert {state.value for state in ExtractionState} == {
            "succeeded",
            "partial",
            "failed",
        }
