"""Preparing text for lexical analysis, without touching the text itself.

**Measured defect this exists to fix.** PostgreSQL's text-search parser splits a
comma-grouped number at the comma, before any dictionary sees it:

| Input | Lexemes |
|---|---|
| ``10,000`` | ``10``, ``000`` |
| ``10000`` | ``10000`` |

So the two ways of writing one value **share no lexeme at all** — a query written one
way cannot match a document written the other, and filings mix both freely. Worse,
``000`` is produced by every thousands group in the corpus, so it behaves like a
term while carrying almost no information.

**No text-search configuration can fix this.** Confirmed with ``ts_debug``: the
parser emits two separate ``uint`` tokens, and a configuration only chooses which
dictionary processes tokens the parser has already decided. The join has to happen
before ``to_tsvector`` is called.

**What this does not do.** It produces a *separate* string for analysis;
``chunks.text`` stays verbatim, because §14.4 keeps the stored text comparable to its
source and citations resolve into it. The normalised form is never stored and never
shown.

**Both sides must use this function.** The stored lexemes and the query are analysed
the same way or they stop matching — that is the whole reason PostgreSQL's analysis
chain is shared in the first place (ADR-005). A query path that skipped this would
turn a measured fix into a measured regression.
"""

import re
from typing import Final

__all__ = ["for_analysis"]

_GROUPED: Final = re.compile(r"(?<=\d),(?=\d{2,3}(?!\d))")
"""A thousands (or lakh) separator: a comma between digits, where what follows is a
group of exactly two or three digits.

Two or three, because Indian grouping is ``1,23,456`` and Western is ``123,456``;
requiring a full group is what keeps an ordinary enumeration intact. ``notes 1,2,3``
has single-digit groups and is left alone, so the commas there still separate.

The negative lookahead matters: without it, ``1,2345`` would match on the first
three digits of a four-digit run and silently join something that is not a
thousands group.
"""


def for_analysis(text: str) -> str:
    """The string to hand ``to_tsvector``, with digit groups joined.

    ``10,000`` becomes ``10000``; ``1,23,456`` becomes ``123456``; ``1,234.56``
    becomes ``1234.56``. Character offsets are **not** preserved, which is why the
    result is only ever analysed and never stored: §14.9 resolves citations into the
    verbatim text, and this is not it.

    Deliberately narrow. Three further corruptions are measured and left alone
    because each would require a semantic claim or a configuration change, both of
    which are the developer's call:

    * ``(10,000)`` loses its parentheses, so a loss reads like a profit. Restoring
      the sign means asserting that parentheses mean negative — true by convention in
      a financial statement, and still an inference this layer must not make.
    * ``Ind AS 115`` loses ``AS`` to the English stopword list.
    * ``ESOS`` stems to ``eso``.

    The last two need a different text-search configuration, which §9.7 reserves for
    a recorded decision.
    """
    return _GROUPED.sub("", text)
