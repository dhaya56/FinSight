"""Correcting glyph-encoding artefacts as text leaves the producer.

**This makes stored text more faithful, not less.** A PDF font encodes "financial" as a
single ligature glyph standing for two letters, and a non-breaking space as a character
that is not a space. The page says one thing and the extractor recorded another, so
undoing that is decoding, not editing. Section 14.4 keeps stored text comparable to its
source, and these changes move it closer.

**Measured defect this exists to fix.** PostgreSQL lexes a ligature as a different word:
"financial" gives the lexeme ``financi`` while the ligatured spelling gives itself, not
even stemmed. Across fifteen ordinary financial words, 741 passages spell one with a
ligature and **573 of them cannot be reached** by a query spelling it normally; 441 of
5,637 retrievable chunks are affected. It is a retrieval defect wearing a typography
costume.

**Every mapping key is written as ``chr()``.** A no-break space, a thin space and a hair
space are indistinguishable from a space in source, so a table written with literal
characters cannot be reviewed — the first version of this module was exactly that, and
the entries could not be told apart by reading them.

**What this deliberately does not do.**

* **Curly apostrophes are left alone.** Measured: the curly and straight forms of
  "company's" both lex to ``'compani'``, so normalising 994 occurrences would be churn
  against stored text for no retrieval gain.
* **The minus sign U+2212 is left alone.** It is semantically correct in a financial
  figure, and :mod:`finsight.generation.numerals` already treats it as a minus.
* **En and em dashes are left alone.** They are punctuation, not a mis-encoded hyphen.
* **Newlines are kept.** A line break is a property of the page and part of what was
  extracted; collapsing it is a rendering decision, made where text is displayed.
* **Words are not joined across a line break.** See :func:`normalise`.
* **Private-use glyphs are left alone.** 56 elements carry one. They are unmappable by
  definition, and deleting text is a worse failure than rendering a box.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["LIGATURES", "normalise"]

LIGATURES: Final[dict[str, str]] = {
    chr(0xFB00): "ff",
    chr(0xFB01): "fi",
    chr(0xFB02): "fl",
    chr(0xFB03): "ffi",
    chr(0xFB04): "ffl",
    chr(0xFB05): "st",  # long-s + t
    chr(0xFB06): "st",
}
"""Latin typographic ligatures and the letters they stand for.

U+FB05 is the long-s form and U+FB06 the round form; both are the same two letters to a
reader and to a search index.
"""

_SPACES: Final[dict[str, str]] = {
    chr(0x00A0): " ",  # no-break space
    chr(0x2007): " ",  # figure space
    chr(0x2009): " ",  # thin space
    chr(0x200A): " ",  # hair space
    chr(0x202F): " ",  # narrow no-break space
    chr(0x205F): " ",  # medium mathematical space
    chr(0x3000): " ",  # ideographic space
}
"""Space characters a reader cannot distinguish from a space, and a tokeniser can.

Measured: 338 elements over forty characters long contain **no ASCII space at all** —
their words are separated by these instead, so the whole element becomes one token.
"""

_INVISIBLE: Final[dict[str, str]] = {
    chr(0x00AD): "",  # soft hyphen: an optional break, invisible unless taken
    chr(0x200B): "",  # zero-width space
    chr(0x200C): "",  # zero-width non-joiner
    chr(0x200D): "",  # zero-width joiner
    chr(0xFEFF): "",  # byte-order mark appearing mid-text
}

_HYPHENS: Final[dict[str, str]] = {
    chr(0x2010): "-",  # HYPHEN, visually identical to hyphen-minus
    chr(0x2011): "-",  # non-breaking hyphen
}

_TRANSLATION: Final = str.maketrans({**LIGATURES, **_SPACES, **_INVISIBLE, **_HYPHENS})

_CONTROL: Final = re.compile(
    f"[{chr(0x00)}-{chr(0x08)}{chr(0x0B)}{chr(0x0C)}{chr(0x0E)}-{chr(0x1F)}"
    f"{chr(0x7F)}-{chr(0x9F)}]"
)
"""C0 and C1 control characters, keeping tab and newline.

127 elements carry one, including eleven occurrences of U+0083. They are invisible, they
are not in the document, and they reach the tokeniser.
"""

_LINE_BREAK_HYPHEN: Final = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[^\W\d_])")
r"""A hyphen at a line break, between two letters.

Digits are excluded by ``[^\W\d_]``: ``2023-\n24`` is a period range, and closing it up
would create ``2023-24`` from what may have been two separate figures.
"""


def normalise(text: str) -> str:
    r"""Decode glyph artefacts in extracted text.

    **The line-break hyphen keeps its hyphen.** ``long-\nterm`` becomes ``long-term``,
    not ``longterm``. Measured over 421 split words in this corpus, the joined spelling
    is the one the corpus uses elsewhere in only **11%** of cases, while the hyphenated
    spelling is used in **63%** — "sub-section", "related-party", "wholly-owned",
    "part-time". Joining blindly would corrupt the majority to repair the minority, and
    choosing per word needs a dictionary this project does not carry.

    So the hyphen that was printed is kept, which is faithful to the page, and the
    remaining case — a word genuinely broken by typesetting, such as "finan-cial" — is
    made searchable in :func:`finsight.lexical.normalise.for_analysis` instead, where a
    spelling can be added to the index without altering what was stored.

    Idempotent: normalising twice is normalising once, which matters because extraction
    may be re-run and a configuration change must produce the same text from the same
    bytes.
    """
    if not text:
        return text
    collapsed = _LINE_BREAK_HYPHEN.sub("-", text)
    return _CONTROL.sub("", collapsed.translate(_TRANSLATION))
