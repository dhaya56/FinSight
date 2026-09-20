"""Tests for the reading-order rule.

Including the case it gets wrong. A limitation with a test around it is a
recorded limitation; a limitation without one is a bug waiting to be discovered
by a reader who trusts a citation.
"""

from finsight.extraction.pdf.reading_order import reading_order_key

TOP_LEFT = (72.0, 100.0, 300.0, 120.0)
TOP_RIGHT = (320.0, 100.0, 540.0, 120.0)
BOTTOM_LEFT = (72.0, 400.0, 300.0, 420.0)


class TestReadingOrderKey:
    def test_a_higher_block_sorts_first(self) -> None:
        assert reading_order_key(TOP_LEFT) < reading_order_key(BOTTOM_LEFT)

    def test_blocks_at_the_same_height_sort_left_to_right(self) -> None:
        assert reading_order_key(TOP_LEFT) < reading_order_key(TOP_RIGHT)

    def test_height_outranks_horizontal_position(self) -> None:
        """A block lower on the page follows, however far left it starts."""
        far_left_but_lower = (0.0, 400.0, 50.0, 420.0)

        assert reading_order_key(TOP_RIGHT) < reading_order_key(far_left_but_lower)

    def test_the_key_ignores_the_far_corner(self) -> None:
        """Only the top-left corner orders a block; extent must not."""
        narrow = (72.0, 100.0, 90.0, 120.0)
        wide = (72.0, 100.0, 540.0, 300.0)

        assert reading_order_key(narrow) == reading_order_key(wide)

    def test_sorting_is_stable_for_identical_corners(self) -> None:
        """Equal keys keep input order, so extraction stays reproducible."""
        blocks = [("a", TOP_LEFT), ("b", TOP_LEFT)]

        ordered = sorted(blocks, key=lambda entry: reading_order_key(entry[1]))

        assert [name for name, _ in ordered] == ["a", "b"]

    def test_two_columns_are_read_across_rather_than_down(self) -> None:
        """The known multi-column failure, asserted rather than left implicit.

        Correct reading order for two columns is left-column-then-right-column.
        This rule interleaves them. Fixing it needs real filings and a recorded
        evaluation (§12.9), so the failure is pinned here instead of hidden.
        """
        left_top = (72.0, 100.0, 280.0, 120.0)
        left_bottom = (72.0, 300.0, 280.0, 320.0)
        right_top = (320.0, 100.0, 540.0, 120.0)

        ordered = sorted(
            [
                ("left-bottom", left_bottom),
                ("left-top", left_top),
                ("right-top", right_top),
            ],
            key=lambda entry: reading_order_key(entry[1]),
        )

        assert [name for name, _ in ordered] == [
            "left-top",
            "right-top",
            "left-bottom",
        ]
