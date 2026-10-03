"""Tests for binding footnote text to the table whose cells refer to it.

The failure this prevents is a number released without the qualification that
changes its meaning. The failure it must not *introduce* is a footnote bound to
the wrong table, which changes a figure's meaning silently and in a way no reader
can detect.

So the tests pull both ways: :class:`TestBinding` checks that a real reference is
found, and :class:`TestRefusal` checks every way a plausible-looking line is left
alone. The second class is the important one — ENV-008 measured table regions as
frequently mis-bounded, so "the text just below" is routinely another table's.
"""

from finsight.extraction.tables.adjacency import bind_footnotes

TABLE = (50.0, 100.0, 450.0, 200.0)


def block(top: float, text: str, *, left: float = 50.0, right: float = 450.0):
    return ((left, top, right, top + 10.0), text)


class TestBinding:
    def test_a_marker_the_table_uses_is_bound(self) -> None:
        notes = bind_footnotes(
            TABLE,
            ["a"],
            [block(210.0, "(a) Includes one-time non-recurring tax gains.")],
        )

        assert len(notes) == 1
        assert notes[0].marker == "a"

    def test_the_text_is_stored_verbatim_with_its_marker(self) -> None:
        """§14.9 offsets are into the stored string, so trimming shifts them all."""
        text = "(a) Includes one-time non-recurring tax gains."
        notes = bind_footnotes(TABLE, ["a"], [block(210.0, text)])

        assert notes[0].text == text

    def test_a_symbol_marker_binds(self) -> None:
        notes = bind_footnotes(
            TABLE, ["*"], [block(210.0, "* Net of adjustments on modifications.")]
        )

        assert notes[0].marker == "*"

    def test_brackets_are_presentation_not_identity(self) -> None:
        """The cell may carry ``a`` while the line carries ``(a)``."""
        notes = bind_footnotes(TABLE, ["(a)"], [block(210.0, "(a) Something.")])

        assert notes[0].marker == "a"

    def test_several_markers_each_find_their_line(self) -> None:
        notes = bind_footnotes(
            TABLE,
            ["1", "2"],
            [block(210.0, "(1) First note."), block(222.0, "(2) Second note.")],
        )

        assert [note.marker for note in notes] == ["1", "2"]

    def test_the_nearest_line_wins_a_repeated_marker(self) -> None:
        """A later line repeating a marker belongs to a different table."""
        notes = bind_footnotes(
            TABLE,
            ["a"],
            [block(210.0, "(a) The right one."), block(300.0, "(a) A later table's.")],
        )

        assert notes[0].text == "(a) The right one."


class TestRefusal:
    def test_a_marker_the_table_does_not_use_is_left_alone(self) -> None:
        """Measured: two such lines sat below a table whose cells never cited them.

        Both belonged to a table two regions away whose boundary was wrong. Binding
        on position alone would have attached them here.
        """
        notes = bind_footnotes(TABLE, ["a"], [block(210.0, "(2) A different table's.")])

        assert notes == ()

    def test_a_table_with_no_markers_binds_nothing(self) -> None:
        notes = bind_footnotes(TABLE, [], [block(210.0, "(a) Looks like a footnote.")])

        assert notes == ()

    def test_text_above_the_table_is_not_a_footnote(self) -> None:
        notes = bind_footnotes(TABLE, ["a"], [block(50.0, "(a) Above the table.")])

        assert notes == ()

    def test_the_scan_stops_at_the_next_table(self) -> None:
        """Text past another table belongs to that table."""
        below = (50.0, 250.0, 450.0, 320.0)
        notes = bind_footnotes(
            TABLE, ["a"], [block(400.0, "(a) Beneath the next table.")], [below]
        )

        assert notes == ()

    def test_a_neighbouring_column_is_not_beneath(self) -> None:
        """A two-column page puts unrelated text directly below, in the other column."""
        notes = bind_footnotes(
            TABLE,
            ["a"],
            [block(210.0, "(a) Other column.", left=460.0, right=560.0)],
        )

        assert notes == ()

    def test_a_parenthesised_abbreviation_is_not_a_marker(self) -> None:
        """A line opening "(RSU) ..." is prose, not footnote two."""
        notes = bind_footnotes(
            TABLE, ["RSU"], [block(210.0, "(RSU) Restricted stock units vest...")]
        )

        assert notes == ()

    def test_a_bare_marker_with_no_text_is_not_a_footnote(self) -> None:
        """Binding it would attach an empty qualification, which reads as a real one."""
        notes = bind_footnotes(TABLE, ["a"], [block(210.0, "(a)")])

        assert notes == ()

    def test_a_line_merely_containing_a_marker_is_not_a_footnote(self) -> None:
        """A footnote's marker opens its line; a cell's ends its text."""
        notes = bind_footnotes(
            TABLE, ["a"], [block(210.0, "Revenue grew, as note (a) explains.")]
        )

        assert notes == ()

    def test_a_degenerate_table_box_binds_nothing(self) -> None:
        """A zero-width region would make every overlap test vacuously true."""
        notes = bind_footnotes(
            (50.0, 100.0, 50.0, 200.0), ["a"], [block(210.0, "(a) Something.")]
        )

        assert notes == ()

    def test_the_search_is_bounded(self) -> None:
        """A marker matched far down the page is coincidence, not reference."""
        filler = [block(210.0 + 12.0 * n, f"Paragraph {n}.") for n in range(10)]
        notes = bind_footnotes(TABLE, ["a"], [*filler, block(400.0, "(a) Far below.")])

        assert notes == ()
