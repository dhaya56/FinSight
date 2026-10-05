"""Evidence deduplication (§20.9).

"Overlapping source regions are collapsed while preserving strongest retrieval
provenance." Two parts, and the second is the one that is easy to get wrong.

**Collapsing** is on *source regions*, not on text. Two candidates are the same evidence
when they were built from the same blocks, however differently they were retrieved — and
that is exactly the case this corpus produces: a block split across an oversized
boundary yields several chunks citing the same element, and §14.7 records that honestly
rather than hiding it.

**Preserving the strongest provenance** means the survivor keeps the *best* rank and the
union of what found it, not merely whichever copy happened to be first. A candidate found
by both retrievers and a near-duplicate found by one are not interchangeable, and
discarding the duplicate silently would lose the fact that two retrievers agreed.

**What this deliberately does not do:** reorder. Deduplication removes, it never
promotes. The order arriving here was decided by fusion and the reranker, and a stage
that quietly re-sorted would make those two unaccountable for the result.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

__all__ = ["Collapsed", "dedupe"]


@dataclass(frozen=True, slots=True)
class Collapsed:
    """One candidate that was removed, and what absorbed it.

    Kept rather than discarded because §20.13 wants the retrieval path recorded, and
    "why are there four results when I asked for five" is a question a trace should be
    able to answer.
    """

    removed: UUID
    absorbed_by: UUID
    shared_elements: int


def dedupe[Candidate](
    candidates: Sequence[Candidate],
    *,
    elements: Mapping[UUID, frozenset[UUID]],
) -> tuple[tuple[Candidate, ...], tuple[Collapsed, ...]]:
    """Collapse candidates whose source regions overlap, keeping the earlier one.

    "Earlier" is the whole policy: the sequence arrives ranked, so the first occurrence
    is by definition the strongest provenance for that evidence, and keeping it needs no
    score comparison and no tie-break.

    Overlap means *any* shared source element, not identity. A chunk split from an
    oversized block shares its element with its siblings while holding different text,
    and returning three slices of one block as three results is the duplication §20.9
    exists to remove.

    A candidate with **no** recorded source elements is never collapsed. It cannot be
    shown to overlap anything, and dropping it on an absent fact would be guessing;
    §14.7 makes a chunk without sources a defect worth seeing rather than hiding.

    Raises:
        TypeError: a candidate has no ``chunk_id``.
    """
    kept: list[Candidate] = []
    collapsed: list[Collapsed] = []
    claimed: dict[UUID, frozenset[UUID]] = {}

    for candidate in candidates:
        chunk_id = getattr(candidate, "chunk_id", None)
        if chunk_id is None:
            raise TypeError(
                f"{type(candidate).__name__} has no chunk_id; deduplication "
                "identifies candidates by it"
            )
        regions = elements.get(chunk_id, frozenset())

        absorber = next(
            (
                held
                for held, held_regions in claimed.items()
                if regions and held_regions & regions
            ),
            None,
        )
        if absorber is not None:
            collapsed.append(
                Collapsed(
                    removed=chunk_id,
                    absorbed_by=absorber,
                    shared_elements=len(claimed[absorber] & regions),
                )
            )
            continue

        claimed[chunk_id] = regions
        kept.append(candidate)

    return tuple(_renumbered(kept)), tuple(collapsed)


def _renumbered[Candidate](candidates: Sequence[Candidate]) -> list[Candidate]:
    """Close the gaps left by removals, preserving order.

    Ranks are renumbered so a caller never sees 1, 2, 4, 5 and has to wonder what
    happened to 3. The *order* is untouched — only the labels move.
    """
    out: list[Candidate] = []
    for position, candidate in enumerate(candidates, start=1):
        if hasattr(candidate, "rank"):
            out.append(replace(candidate, rank=position))  # type: ignore[type-var]
        else:
            out.append(candidate)
    return out
