"""Comparing the numbers in a claim against the numbers in its cited spans (§27.3, §27.4).

**Tolerant of presentation, never of value.** A filing writes the same quantity as
``48,206.00``, ``48206``, ``48,206.00 crore`` or ``₹48,206.00``, and a claim that reproduces it
in any of those forms has reproduced it. A claim that writes ``48,260.00`` has not, and no
amount of normalisation should let that pass. So both sides are reduced to decimal *values*
and compared as values — string comparison would reject a legitimate reformat, and comparing
loosely would accept a transposition.

**A parenthesised figure is negative, and the sign is part of the value.** ``(45)`` reduces to
``-45``, so a claim asserting ``45`` against a span reading ``(45)`` is **unsupported** — which
is the intended outcome. Dropping a negative sign is the worst defect available in a financial
table, and §4 of the limitation register already records the parsing version of this mistake.

**``Decimal``, not float.** ``412.10`` and ``412.1`` are the same quantity and must compare
equal; ``0.1 + 0.2`` style error must never make two different quantities compare equal. This is
comparison rather than arithmetic, but the type discipline §7 requires applies for the same
reason.

**What this does not catch**, recorded here rather than discovered later:

* **a wrong scale word.** ``412.00 crore`` against a span reading ``412.00 lakh`` passes, because
  the numeral matches and the scale is a separate token. Unit and currency consistency is §27.7's
  job, not this one;
* **a spelled-out number.** "ten per cent" carries no digits and is invisible here;
* **a number assembled from two spans.** Guarded against by joining spans with a newline in
  :attr:`ResolvedClaim.supported_text`, not by anything in this module.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

__all__ = ["MINUS_SIGNS", "Numeral", "numerals_in", "unsupported_numerals"]

MINUS_SIGNS: Final = "-−–"  # noqa: RUF001
"""Characters a filing uses for a minus: hyphen-minus, minus sign, en dash.

The lookalike warning is suppressed deliberately — these are *meant* to be
indistinguishable, because a typesetter chose between them and a reader cannot tell. Treating
only the ASCII hyphen as a sign would read a negative figure as positive, which is the one
error in this module that must not happen. Exported so a test can assert each form is handled
rather than restating the literals.
"""

_NUMERAL: Final = re.compile(
    rf"""
    (?P<open>\()?                   # opening parenthesis: accounting negative
    (?P<sign>[{MINUS_SIGNS}])?      # any of the minus forms above
    (?P<digits>\d[\d,]*)            # integer part, separators in any grouping
    (?:\.(?P<frac>\d+))?            # fractional part
    (?P<close>\))?                  # closing parenthesis
    """,
    re.VERBOSE,
)
"""One numeral as a filing writes it.

Separators are matched without assuming a grouping, because Indian filings write
``1,00,00,000`` where a thousands-separator pattern would read two numbers. Currency symbols
and scale words are deliberately outside the match: they are adjacent tokens, and pulling them
in would make ``412.00 crore`` and ``412.00`` different numerals when they are the same one.
"""


@dataclass(frozen=True, slots=True)
class Numeral:
    """One number found in text, as written and as a value."""

    as_written: str
    value: Decimal

    def __str__(self) -> str:
        return self.as_written


def numerals_in(text: str) -> tuple[Numeral, ...]:
    """Every numeral in ``text``, in order, keeping repeats.

    Deterministic and applied identically to both sides of a comparison: a quirk in how this
    reads ``2.11.5`` matters only if the two sides disagree, and they cannot, because the same
    function reads both.
    """
    found: list[Numeral] = []
    for match in _NUMERAL.finditer(text):
        value = _value_of(match)
        if value is not None:
            found.append(Numeral(as_written=match.group(0), value=value))
    return tuple(found)


def _value_of(match: re.Match[str]) -> Decimal | None:
    """Reduce one match to a signed decimal, or ``None`` when it will not parse.

    ``None`` rather than raising: a pathological separator run is not worth abandoning an
    answer over, and a numeral that cannot be read also cannot be verified, so the caller
    treats it as absent from the supported set — the conservative direction.
    """
    digits = match.group("digits").replace(",", "")
    if not digits:
        return None
    fraction = match.group("frac")
    literal = f"{digits}.{fraction}" if fraction else digits

    try:
        value = Decimal(literal)
    except InvalidOperation:
        return None

    parenthesised = bool(match.group("open")) and bool(match.group("close"))
    if parenthesised or match.group("sign"):
        value = -value
    return value


def unsupported_numerals(claim_text: str, supported_text: str) -> tuple[Numeral, ...]:
    """Numerals asserted by a claim that no cited span states.

    Returns one entry per unsupported *value*, in the order the claim wrote them, so a figure
    repeated in a sentence is reported once. The caller removes the claim (§27.3); it does not
    repair it, because a claim with its numbers removed is a different claim and nobody wrote it.
    """
    supported = {numeral.value for numeral in numerals_in(supported_text)}
    unsupported: list[Numeral] = []
    seen: set[Decimal] = set()
    for numeral in numerals_in(claim_text):
        if numeral.value in supported or numeral.value in seen:
            continue
        seen.add(numeral.value)
        unsupported.append(numeral)
    return tuple(unsupported)
