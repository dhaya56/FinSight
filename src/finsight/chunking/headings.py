"""Recovering section structure that the parser deliberately did not record.

§18.2 attaches a heading path to every chunk and §14.6 permits it as deterministic
enrichment — but nothing upstream supplies one. ``extraction/contracts.py`` states
the reason plainly: a producer "makes no claim about document structure; a heading
is a block that happens to be large, not a heading." That was right for extraction.
It leaves chunking with text and geometry and no sections.

So sections are recovered here, from what filings actually do: they number their
headings. Measured on a development page, ``1. Brief outline on CSR Policy of the
Company:`` and ``2. Composition of CSR Committee:`` are headings while the 933- and
1,872-character blocks between them are not, and the difference is visible without
reading either.

**The rule is biased hard toward precision**, and the asymmetry is deliberate.
Missing a heading merges two sections, which makes a section longer and loses a
little context. Inventing one splits a section in the wrong place and attaches a
*wrong* heading path to every chunk after it — a chunk that says it comes from
"Risk Factors" when it comes from the notes is worse than a chunk that says
nothing. So a block must both match a structural pattern **and** be short.

**Known misfire, measured.** PyMuPDF sometimes merges a heading into the block
before it: one real page yields ``3 Michael Gibbs Member 4 4 3. Web link(s) for
composition of C...``, where a table row and the following heading share a block.
That heading is invisible to any rule operating on whole blocks, and no pattern
fixes it — the block genuinely contains both. Recorded in the limitation register
rather than papered over.
"""

import re
from typing import Final

_NUMBERED: Final = re.compile(
    r"""^\s*
    (?P<number>
        \d{1,2}\.(?:\d{1,2})?           # 7.      7.2
        (?:\.\d{1,2}){0,2}              # 7.2.1
    )
    \s+
    (?P<title>\S.*)$
    """,
    re.VERBOSE,
)
"""A numbered heading: ``2. Composition of CSR Committee:``, ``3.2 Leases``.

The depth of the number *is* the depth of the heading, which is why this is worth
matching precisely rather than treating every heading as one level.

**The dot is mandatory, and that is the whole rule.** An earlier version accepted a
bare leading number, and measured against a development filing it read a table of
subsidiaries as 20 consecutive section headings — ``3 Infosys``, ``4 EdgeVerve``,
``5 Infosys Public``. Those rows escape the table-derived guard because the
detector never found that table (ADR-003: 2 of 6 regions bounded correctly), so
nothing else was going to catch them.

Filings write a section number as ``3.`` or ``3.2``; a table row writes ``3``.
Requiring the dot costs any heading genuinely written without one, which is a
longer section rather than a wrong one.
"""

_KEYWORD: Final = re.compile(
    r"""^\s*
    (?P<keyword>
        item | note | notes | annexure | annex | schedule |
        section | part | chapter | appendix
    )
    \s+
    (?P<number>[0-9IVXLC]{1,6})
    \b
    """,
    re.VERBOSE | re.IGNORECASE,
)
"""A named structural heading: ``Item 7``, ``Note 12``, ``Annexure 6``, ``Part II``.

``Item`` and ``Note`` carry most of the weight in filings — a 10-K is organised by
Item and an annual report's financial section by Note. Roman numerals are admitted
because ``Part II`` and ``Schedule III`` are standard in Indian filings.
"""

_SENTENCE_END: Final = re.compile(r"[.!?]\s*$")

_MIN_TITLE_CHARS: Final = 3
"""Below this there is no title, only a number. ``2.`` alone is a list marker."""


def heading_level(text: str, *, max_chars: int) -> int | None:
    """The heading depth of a block, or None when it is not a heading.

    Returns 1 for a top-level section and deeper numbers for subsections, so a
    caller can maintain a stack without re-parsing.

    Length is checked on the *collapsed* text so that a block wrapped across lines
    is judged by what it says rather than how it was laid out.
    """
    collapsed = " ".join(text.split())
    if not collapsed or len(collapsed) > max_chars:
        return None

    numbered = _NUMBERED.match(collapsed)
    if numbered is not None:
        title = numbered.group("title")
        if len(title) < _MIN_TITLE_CHARS:
            return None
        if _ends_like_prose(title) or not _titled(title):
            return None
        # A trailing dot is punctuation, not depth: "7." is level 1 while
        # "7.2" is level 2, and both contain exactly one dot.
        return numbered.group("number").rstrip(".").count(".") + 1

    keyword = _KEYWORD.match(collapsed)
    if keyword is not None:
        return 1

    return None


def _titled(title: str) -> bool:
    """Whether a numbered line's title reads as a title rather than a measurement.

    A section title begins with a capital. A decimal number does not, and that is
    what this rejects: measured on a development filing the pattern otherwise read
    ``7.6 years``, ``63.39 64.50`` and ``4.7 6.1`` as sections, because a decimal
    is indistinguishable from a sub-section number once the digits are stripped.

    Costs a heading whose title genuinely opens with a digit or a lowercase brand
    name. That is a longer section, which is the direction this module errs in.
    """
    return title[:1].isupper()


def _ends_like_prose(title: str) -> bool:
    """Whether a numbered line reads as a sentence rather than a title.

    ``1. We acquired three businesses during the year.`` is a numbered *sentence*
    in a list, not a section heading. Headings end with a colon, a word, or
    nothing — rarely with a full stop. A colon is explicitly allowed because
    filings write ``2. Composition of CSR Committee:``.
    """
    return bool(_SENTENCE_END.search(title))


class HeadingStack:
    """The current section path, maintained as headings are encountered.

    A heading at depth *n* replaces everything at depth *n* and below, which is
    what turns a flat sequence of headings into a path. A heading deeper than the
    current path simply extends it; filings skip levels often enough that
    demanding a strict sequence would drop real structure.
    """

    __slots__ = ("_levels",)

    def __init__(self) -> None:
        self._levels: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        while self._levels and self._levels[-1][0] >= level:
            self._levels.pop()
        self._levels.append((level, " ".join(title.split())))

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(title for _, title in self._levels)

    def reset(self) -> None:
        """Forget the path. Called at a document boundary, never a page one.

        A section runs across pages; resetting per page would give every chunk
        after the first page of a section an empty path.
        """
        self._levels.clear()


def suppress_list_runs(levels: list[int | None]) -> list[int | None]:
    """Blank out heading candidates that form a numbered *list* rather than sections.

    The signal is adjacency. A filing writes ``1. Brief outline on CSR Policy:``,
    then several hundred words, then ``2. Composition of CSR Committee:`` — body
    text separates one section from the next. A numbered list writes ``1. …``,
    ``2. …``, ``3. …`` in consecutive blocks with nothing between them.

    Measured on a development filing, this is what distinguishes real sections from
    a CSR projects table whose rows are numbered with dots and were otherwise
    indistinguishable from headings. Without it, 341 "headings" were detected where
    the document has a small fraction of that, and every chunk after one of them
    carried a heading path naming a charitable project.

    Two genuinely adjacent headings differ in level — a section followed by its
    first subsection — so requiring the *same* level keeps that case intact.
    """
    suppressed = list(levels)
    for index, level in enumerate(levels):
        if level is None:
            continue
        before = levels[index - 1] if index > 0 else None
        after = levels[index + 1] if index + 1 < len(levels) else None
        if before == level or after == level:
            suppressed[index] = None
    return suppressed
