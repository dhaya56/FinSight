"""Tests for the producer boundary.

``derive_state`` is the reason these exist: a run's outcome is computed from what
was produced, so a producer cannot report a clean run while emitting coverage
gaps.
"""

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    ExtractionState,
    PageLocation,
)
from finsight.extraction.contracts import derive_state

METHOD = "probe"
METHOD_VERSION = "0.0.0-test"


def block(text: str | None = "text", failure_reason: str | None = None) -> ExtractedElement:
    return ExtractedElement(
        element_type=ElementType.BLOCK,
        ordinal=0,
        locator="p. 1",
        location=BlockLocation(bbox=(0.0, 0.0, 10.0, 10.0)),
        extraction_method=METHOD,
        extraction_method_version=METHOD_VERSION,
        text=text,
        failure_reason=failure_reason,
    )


def page(
    *children: ExtractedElement,
    failure_reason: str | None = None,
) -> ExtractedElement:
    return ExtractedElement(
        element_type=ElementType.PAGE,
        ordinal=0,
        locator="p. 1",
        location=PageLocation(page_number=1, width=595.0, height=842.0, rotation=0),
        extraction_method=METHOD,
        extraction_method_version=METHOD_VERSION,
        failure_reason=failure_reason,
        children=children,
    )


class TestDeriveState:
    def test_clean_output_succeeded(self) -> None:
        assert derive_state([page(block())]) is ExtractionState.SUCCEEDED

    def test_no_elements_at_all_is_a_failure(self) -> None:
        """Otherwise an unextracted document is indistinguishable from an empty one."""
        assert derive_state([]) is ExtractionState.FAILED

    def test_a_failed_page_makes_the_run_partial(self) -> None:
        assert (
            derive_state([page(), page(failure_reason="no_text_extracted")])
            is ExtractionState.PARTIAL
        )

    def test_a_gap_nested_in_a_child_is_still_found(self) -> None:
        """A producer cannot hide a gap by burying it below the top level."""
        buried = page(block(), block(text=None, failure_reason="unreadable"))

        assert derive_state([buried]) is ExtractionState.PARTIAL

    def test_one_gap_among_many_clean_pages_is_enough(self) -> None:
        pages = [page(block()) for _ in range(5)]
        pages.append(page(failure_reason="no_text_extracted"))

        assert derive_state(pages) is ExtractionState.PARTIAL
