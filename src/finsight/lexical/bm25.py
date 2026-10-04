"""BM25 term weighting, split between this process and Qdrant.

ADR-005 makes BM25 the primary lexical path, carried as sparse vectors. BM25 is

    score(q, d) = Σ  IDF(t) · tf(t,d)·(k1+1) / ( tf(t,d) + k1·(1 - b + b·|d|/avgdl) )
                 t in q

and the two factors live in different places, for a reason that is structural
rather than convenient:

* **The saturated, length-normalised term frequency is computed here**, at index
  time. It depends only on the one document, so it can be computed once and stored.
* **IDF is computed by Qdrant**, at query time, through
  ``models.Modifier.IDF`` on the sparse vector configuration. It depends on the
  *whole collection* — how many documents contain the term — which a process
  weighting one chunk cannot know, and which changes every time a document is
  added.

A client that computed IDF itself would freeze the collection statistics at the
moment each chunk was written, so a term's weight would depend on when its chunk
happened to be indexed. That is the defect this split exists to avoid.

**The analysis chain is PostgreSQL's**, not a tokenizer written here. ``to_tsvector``
already stemmed and stopped the text when the chunk was written (§9.7), and the
same configuration analyses the query, so the two sides of a match agree by
construction. Writing a second tokenizer would mean two chains to keep in step, and
the failure when they drift is silent: queries simply stop matching.

**What is deliberately not implemented:** field weighting. §9.7 reserves it for
recorded evidence, and a heading-weighted variant needs a golden question set to
evaluate against.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "BM25_CONFIG_VERSION",
    "BM25Config",
    "SparseVector",
    "document_vector",
    "parse_tsvector",
    "query_vector",
    "term_index",
]

BM25_CONFIG_VERSION: Final = "1"
"""Bump when any value in :class:`BM25Config` changes.

**Changing ``average_document_length`` invalidates every stored weight**, not just
new ones. Qdrant receives document weights as given, so a collection written under
two different averages holds scores that are not comparable — a chunk indexed under
one average outranks an equally good chunk indexed under another, with nothing to
show it. A change here therefore requires re-indexing the whole collection, which
is why the value is versioned rather than recomputed per run.
"""


@dataclass(frozen=True, slots=True)
class BM25Config:
    """The BM25 parameters, and where each number came from."""

    k1: float = 1.2
    """Term-frequency saturation. The long-standing BM25 default.

    Not measured on this corpus, and its effect here is small: 72.2% of term
    occurrences in the development corpus appear exactly once in their chunk, where
    saturation does nothing at all. Adopted as the standard default rather than
    chosen, and recorded as unmeasured.
    """

    b: float = 0.75
    """Length-normalisation strength. The long-standing BM25 default.

    Unmeasured, but unlike ``k1`` this one matters here. Measured over the 4,816
    development children, document length runs from a p10 of 5 positions to a p90 of
    191 — a 38x spread — so how strongly length is normalised materially reorders
    results. A chunking comparison (§18.12) and this value should be measured
    together, because the spread is a property of the chunker.
    """

    average_document_length: float = 99.21
    """**Measured**, not assumed: the mean token-position count per child chunk.

    Taken over all 4,816 children of the three development filings under chunking
    configuration 1 (median 105, p10 5, p90 191, max 255). Token *positions* in the
    stored tsvector, which is what BM25 means by document length — not distinct
    lexemes, which averages 63.09 and would understate every repeated term.

    FastEmbed's BM25 defaults this to 256 because a library cannot know the corpus.
    This project can, so it measures. The figure describes three filings and will
    need re-measuring when the corpus changes materially; §29.11's reconciliation is
    where that drift should be noticed.
    """

    version: str = BM25_CONFIG_VERSION

    def __post_init__(self) -> None:
        if self.average_document_length <= 0:
            raise ValueError("average_document_length must be positive")
        if self.k1 < 0:
            raise ValueError("k1 must not be negative")
        if not 0.0 <= self.b <= 1.0:
            raise ValueError("b must lie in [0, 1]")


@dataclass(frozen=True, slots=True)
class SparseVector:
    """Term indices and their weights, paired by position.

    One type for both sides of the search so that a mismatch between the document
    and query representations cannot arise from two shapes that look alike.
    """

    indices: tuple[int, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.indices) != len(self.values):
            raise ValueError(
                f"{len(self.indices)} indices and {len(self.values)} values; "
                "the pairing would be meaningless"
            )

    def __len__(self) -> int:
        return len(self.indices)


_ENTRY: Final = re.compile(r"'((?:[^']|'')*)'(?::([0-9A-Da-d,]*))?")
"""One entry of a tsvector's text form: ``'revenu':1,5,12`` or a bare ``'revenu'``.

``''`` inside the quotes is PostgreSQL's escape for a literal apostrophe, so the
lexeme pattern has to admit it — otherwise a stemmed possessive terminates the
match early and the rest of the vector is read as a new lexeme.

Positions may carry a weight label (``:3A``), which ``to_tsvector`` does not emit
but ``setweight`` does. Admitted in the pattern so a weighted vector parses rather
than silently losing its positions; the labels themselves are ignored until §9.7's
field weighting is actually decided.
"""

_U32: Final = 1 << 32
"""Qdrant sparse indices are unsigned 32-bit."""


def parse_tsvector(raw: str) -> tuple[tuple[str, int], ...]:
    """Lexemes and how often each occurs, from a tsvector's text form.

    An entry with no positions counts as one occurrence. ``to_tsvector`` always
    records positions, but ``strip()`` and hand-written vectors do not, and reading
    a positionless entry as zero occurrences would silently drop the term.

    Returns an empty tuple for an empty vector, which is a real outcome: eight
    development chunks analyse to no lexemes at all because every token in them is a
    stopword or punctuation. Such a chunk stays dense-searchable and carries no
    sparse vector — distinct from ``lexemes IS NULL``, which would mean the chunking
    stage never analysed it.
    """
    return tuple(
        (
            match.group(1).replace("''", "'"),
            len(match.group(2).split(",")) if match.group(2) else 1,
        )
        for match in _ENTRY.finditer(raw)
    )


def term_index(lexeme: str) -> int:
    """A stable sparse-vector index for one lexeme.

    Hashed rather than looked up in a vocabulary table, because the alternative is a
    dictionary that has to be maintained, migrated and kept consistent between the
    indexer and the query path — and a vocabulary miss at query time is a silent
    recall loss.

    ``blake2b``, not :func:`hash`. Python's ``hash`` is salted per process from
    PYTHONHASHSEED, so the same word would map to a different index in the indexer
    and the API, and every lexical query would return nothing while looking healthy.

    Collisions are possible and benign: two unrelated terms sharing an index merge
    their postings, which costs precision on those two terms. Over 2³² indices and a
    corpus of this size the expected count is negligible, and the alternative — a
    maintained vocabulary — trades it for a failure mode that is silent instead.
    """
    digest = hashlib.blake2b(lexeme.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % _U32


def document_vector(raw: str, *, config: BM25Config | None = None) -> SparseVector:
    """The index-time half of BM25 for one chunk's stored lexemes.

    IDF is absent by design — Qdrant supplies it at query time from the collection
    statistics. The weights here are therefore not BM25 scores and must not be
    compared with one; they are one factor of it.
    """
    settings = config or BM25Config()
    entries = parse_tsvector(raw)
    if not entries:
        return SparseVector(indices=(), values=())

    length = sum(frequency for _lexeme, frequency in entries)
    # The normalising denominator is per document, so it is computed once rather
    # than per term.
    normalised = settings.k1 * (
        1.0 - settings.b + settings.b * length / settings.average_document_length
    )

    weights: dict[int, float] = {}
    for lexeme, frequency in entries:
        weight = frequency * (settings.k1 + 1.0) / (frequency + normalised)
        index = term_index(lexeme)
        # A hash collision inside one document would otherwise let the second term
        # overwrite the first. Keeping the larger weight is the honest resolution:
        # the merged posting answers for both terms, and discarding the stronger one
        # would understate a term that is genuinely there.
        weights[index] = max(weights.get(index, 0.0), weight)

    ordered = sorted(weights)
    return SparseVector(
        indices=tuple(ordered),
        values=tuple(weights[index] for index in ordered),
    )


def query_vector(raw: str) -> SparseVector:
    """The query-time half: one unit weight per distinct query term.

    No saturation and no length normalisation, which is not a simplification — BM25
    applies both to the *document*'s term frequency only. Qdrant scores a sparse
    query as the IDF-weighted dot product against each document vector, so a unit
    query weight per term reproduces the sum the formula asks for.

    A repeated query term does not count twice. BM25 has no query-frequency factor;
    BM25F and the original Okapi weighting do, and adopting one here would change
    the formula ADR-005 recorded.

    Takes the *analysed* query — the caller passes it through the same
    ``to_tsvector`` configuration that produced the stored lexemes, which is what
    makes the two sides comparable.
    """
    entries = parse_tsvector(raw)
    indices = sorted({term_index(lexeme) for lexeme, _frequency in entries})
    return SparseVector(
        indices=tuple(indices), values=tuple(1.0 for _ in indices)
    )
