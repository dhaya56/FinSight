"""Tests for the page signals.

These measure; they never decide. Any test here that asserted a routing outcome
would be inventing a threshold, which §12.9 defers to an evaluation on real data.
"""

from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED, PageSignals

TEXT_PAGE = PageSignals(text_block_count=4, image_block_count=0, char_count=820)
SCANNED_PAGE = PageSignals(text_block_count=0, image_block_count=1, char_count=0)
BLANK_PAGE = PageSignals(text_block_count=0, image_block_count=0, char_count=0)
EMPTY_BLOCKS = PageSignals(text_block_count=3, image_block_count=0, char_count=0)


class TestYieldedNothing:
    def test_a_page_without_text_blocks_yielded_nothing(self) -> None:
        assert SCANNED_PAGE.yielded_nothing is True
        assert BLANK_PAGE.yielded_nothing is True

    def test_a_page_with_text_blocks_did_not(self) -> None:
        assert TEXT_PAGE.yielded_nothing is False

    def test_blocks_without_characters_still_count_as_something(self) -> None:
        """Structure without content is a weaker signal than nothing at all.

        Keeping these apart is what stops a page that FinSight did read from
        being recorded as a region it never searched.
        """
        assert EMPTY_BLOCKS.yielded_nothing is False
        assert EMPTY_BLOCKS.is_text_bearing is False


class TestTextBearing:
    def test_characters_make_a_page_text_bearing(self) -> None:
        assert TEXT_PAGE.is_text_bearing is True

    def test_a_scanned_page_is_not(self) -> None:
        assert SCANNED_PAGE.is_text_bearing is False


class TestImageOnly:
    def test_images_without_characters_are_the_scanned_shape(self) -> None:
        assert SCANNED_PAGE.is_image_only is True

    def test_a_blank_page_is_not_image_only(self) -> None:
        """Nothing at all is a different problem from a picture of something."""
        assert BLANK_PAGE.is_image_only is False

    def test_a_page_mixing_text_and_images_is_not_image_only(self) -> None:
        mixed = PageSignals(text_block_count=2, image_block_count=1, char_count=40)

        assert mixed.is_image_only is False


class TestFailureReason:
    def test_the_code_is_stable_and_carries_no_content(self) -> None:
        """Later phases count these, so the value is a contract, not a message."""
        assert NO_TEXT_EXTRACTED == "no_text_extracted"
