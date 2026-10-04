"""Whether a detected table may be trusted as evidence.

The failure this exists to prevent is not a table that is obviously wrong. It is a
table that looks complete and is not — a region that swallowed two columns of
narrative prose, a grid missing 11% of its cells, four statements merged into one.
Each of those was found by looking at rendered pages by hand. None of them raised
an error, and every one would have entered retrieval presenting as financial data.

So extraction gains a verdict: **accepted**, **review required**, or **rejected**.
A table that cannot be trusted is refused rather than quietly used, which is the
only honest answer when a detector is imperfect and the content is financial.

**Thresholds are not invented here.** CLAUDE.md §4 forbids inventing thresholds and
§9 requires gates to stay informational until an approved baseline defines them.
So the verdict rests only on conditions that are structurally unambiguous — a table
with no content at all is not a table, whatever one's threshold — while the graded
signals are *measured and recorded* without gating anything. When the ground truth
exists it will set those cutoffs on evidence, and the signals are already stored
for it to calibrate against.
"""

from dataclasses import dataclass
from typing import Final

from finsight.domain.representations.source import Verdict
from finsight.extraction.tables.contracts import DetectedTable
from finsight.extraction.tables.structure import DerivedTable, looks_numeric

_SENTENCE_WORDS: Final = 12
"""Words above which a cell reads as prose rather than as a label or a value.

Not a tuning parameter so much as a description: financial row labels run to a few
words, and "Of which: term deposits" is five. A cell carrying twelve or more is a
sentence, and a region full of sentences is a page of narrative that a detector
claimed as a table.
"""


EMPTY: Final = "no_cell_holds_text"
DEGENERATE: Final = "not_a_grid"
ALL_PROSE: Final = "every_cell_is_prose"
CELLS_DROPPED: Final = "detector_dropped_cells"
"""Reason codes, stored rather than rendered.

A sentence would be read by a person and by nothing else. These are compared,
counted and filtered on, so they are identifiers; the explanation belongs in the
rule that raises them.
"""


@dataclass(frozen=True, slots=True)
class TableQuality:
    """A verdict, the reasons for it, and the signals behind them.

    The signals are kept alongside the verdict rather than discarded, because a
    verdict without its evidence cannot be re-examined when the thresholds change —
    and these thresholds will change, since none of them is calibrated yet.
    """

    verdict: Verdict
    reasons: tuple[str, ...]

    prose_ratio: float
    """Share of the table's *characters* that sit in sentence-shaped cells.

    Weighted by characters rather than by cell, so one long paragraph beside twenty
    figures does not read the same as twenty paragraphs.
    """

    numeric_ratio: float
    """Numerals as a share of all body cells — **row labels included**.

    Header rows are excluded, so a column of years does not inflate it. The label
    column is not, which means the ratio moves with column count as well as with
    numeric density: a well-formed two-period statement caps near 0.67. A
    calibration has to account for that; it is a density signal, not a score.
    """

    filled_ratio: float
    """Cells holding text, over cells the detector reported as present.

    Absent positions are outside both terms. They are covered by a neighbour's
    span, and counting them as unfilled would make every spanning header a hole.
    """

    unassigned_words: int
    """Words inside the table region that landed in no cell.

    Content the detector's own geometry could not place. Not the same as
    ``dropped_cells``: this is measured here, that is reported by the detector.
    """

    dropped_cells: float
    """Cells the detector said it could not assign to a row or column band."""

    @property
    def usable(self) -> bool:
        """True only for an accepted table.

        Retrieval asks this. A table under review is not evidence yet.
        """
        return self.verdict is Verdict.ACCEPTED


def _is_prose(text: str) -> bool:
    return len(text.split()) >= _SENTENCE_WORDS


def assess(detected: DetectedTable, derived: DerivedTable) -> TableQuality:
    """Judge one table, using only unambiguous rules to decide.

    Takes both representations because they answer different questions: the
    detected table knows what the detector discarded, the derived one knows what
    the structure turned out to be.
    """
    body = [
        cell
        for cell in derived.cells
        if not cell.is_absent and cell.row_index not in derived.header_row_indices
    ]
    present = [cell for cell in derived.cells if not cell.is_absent]
    with_text = [cell for cell in present if cell.text.strip()]

    prose_chars = sum(len(c.text) for c in with_text if _is_prose(c.text))
    total_chars = sum(len(c.text) for c in with_text)
    numeric = sum(1 for c in body if looks_numeric(c.text))

    prose_ratio = prose_chars / total_chars if total_chars else 0.0
    numeric_ratio = numeric / len(body) if body else 0.0
    filled_ratio = len(with_text) / len(present) if present else 0.0

    reasons: list[str] = []
    verdict = Verdict.ACCEPTED

    # --- rejections: these are not tables, on any reading --------------------
    if not with_text:
        reasons.append(EMPTY)
        verdict = Verdict.REJECTED
    if derived.row_count < 2 or derived.column_count < 2:
        # A single row or column has no grid relationship to express, so nothing
        # downstream can bind a value to a period through it.
        reasons.append(DEGENERATE)
        verdict = Verdict.REJECTED
    if total_chars and prose_ratio >= 1.0:
        # Every character sits in a sentence-shaped cell. Observed on a real page,
        # where a detector returned a 1x2 "table" that was a paragraph.
        reasons.append(ALL_PROSE)
        verdict = Verdict.REJECTED

    # --- review: the content is incomplete and we know it --------------------
    if verdict is not Verdict.REJECTED and detected.dropped_cells >= 1.0:
        reasons.append(CELLS_DROPPED)
        verdict = Verdict.REVIEW_REQUIRED

    return TableQuality(
        verdict=verdict,
        reasons=tuple(reasons),
        prose_ratio=round(prose_ratio, 4),
        numeric_ratio=round(numeric_ratio, 4),
        filled_ratio=round(filled_ratio, 4),
        unassigned_words=detected.unassigned_words,
        dropped_cells=round(detected.dropped_cells, 4),
    )
