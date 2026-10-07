"""Turning reranked candidates into the evidence set a model is shown.

**This is the largest quality lever in the generation path, and it was unused.** Retrieval
returns children, which are precise by design and fragmentary as a consequence: measured on
the active corpus they average 200 tokens, and 1,211 of 4,867 fall under 48. A model handed
a twelve-token fragment answers from a twelve-token fragment. The project already builds
parents for exactly this — §18.5 keeps them for interpretation, each verified to contain its
own children — and until now nothing read them.

Four things happen here, each of which current practice is explicit about:

* **expand** a candidate that is a fragment to the parent containing it. Measured, this is
  rarer than it looks — the reranker filters most fragments out before they are retrieved,
  so expanding everything discards half the reranked set to the budget for no gain. See
  :data:`FRAGMENT_CHARS`;
* **merge** candidates that expand to the same parent, because sending one passage twice
  spends budget to say nothing;
* **budget** the set rather than filling the context window. Published guidance is blunt
  that feeding everything retrieved to the model is not good practice;
* **order** so the strongest passages sit at the beginning *and* the end, because a long
  context loses its middle.

**Identifiers follow rank, not position.** Passage ``[1]`` is the strongest evidence
wherever it appears in the prompt. A reader following a citation is asking "what supports
this", not "what came third in the prompt", and the answer and the source list must agree on
the number whatever the ordering does.

**The budget is counted in characters, deliberately.** The generation model's tokenizer is
not available here and acquiring one would mean carrying a second model's vocabulary to
approximate a first — the argument the embedding adapter already makes at length. Characters
are a conservative proxy, and the adapter's truncation guard is the backstop that makes an
estimate safe to use: if the estimate is wrong, the request fails loudly rather than
answering from a prompt that was quietly cut.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from finsight.persistence.repositories.chunks import Citation, PendingChunk
from finsight.retrieval.pipeline import RetrievedChunk

__all__ = [
    "CHARS_PER_TOKEN",
    "FRAGMENT_CHARS",
    "EvidencePassage",
    "EvidenceSet",
    "assemble_evidence",
]

FRAGMENT_CHARS: Final = 170
"""Below this a passage is treated as a fragment and expanded to its parent.

Derived from an existing declared value rather than invented: the chunker's
``child_min_tokens`` floor is 48 tokens, and this corpus measures about 4.4 characters per
token in prose, so 170 characters is that floor expressed in the unit this module budgets in.

**Measured over six real queries, three policies, same candidate sets:**

| Policy | Passages | Avg chars | Fragments | Dropped to budget |
|---|---|---|---|---|
| never expand | 48 | 1,574 | 1 (2%) | 0 |
| expand fragments | 48 | 1,574 | 1 (2%) | 0 |
| expand everything | 22 | 5,779 | 1 (5%) | **20** |

Two findings, both against the design this module was first written for. Reranking already
filters the corpus's 1,211 sub-floor children out, so retrieved passages are not fragments —
only 1 of 48 was. And expanding everything discards **20 of 48 reranked passages** to the
budget, trading evidence the reranker chose for length it did not ask for.

**The fragment policy produced output identical to never expanding**, because the single
fragment that surfaced has no parent: it is one of the whole-run children whose byte-identical
parent the chunker drops. So this threshold is currently a no-op on this corpus.

It is kept at the floor rather than disabled because the cost of keeping it is measured at
exactly zero and the benefit is non-zero on any corpus where a retrieved fragment does have a
parent. §20.8's bounded context expansion therefore exists as a mechanism and is, on this
corpus, unexercised — which is a different statement from implemented, and the distinction
belongs here rather than in a status table.
"""

CHARS_PER_TOKEN: Final = 3.5
"""Conservative characters-per-token estimate for budgeting.

English prose measured on this corpus runs about 4.4 characters per token, but financial
text is dense with numerals and numerals tokenize far worse — a figure like ``48,206.00``
costs several tokens for ten characters. 3.5 deliberately *over*-estimates token cost so the
budget errs toward sending less, which is the safe direction: an under-filled prompt loses a
passage, while an over-filled one is silently truncated.
"""


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    """One passage as the model will see it, and everything needed to cite it back."""

    id: int
    """Stable identifier shown to the model, assigned by rank. 1 is the strongest."""

    chunk_id: UUID
    """The chunk whose text this is — a parent when expanded, otherwise the child."""

    stands_for: tuple[UUID, ...]
    """The retrieved children this passage covers.

    More than one when several candidates shared a parent. Kept because a citation has to
    resolve back to what retrieval actually found, and because collapsing three hits into
    one passage is a fact about the evidence set that a trace should record.
    """

    text: str
    char_count: int
    expanded: bool
    """True when the parent's text replaced the child's."""

    best_rank: int
    """The best rank among the candidates this passage stands for."""

    citations: tuple[Citation, ...] = ()
    heading_path: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    evidence_type: str = "narrative"
    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    reporting_basis: str | None = None
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    """The ordered passages a prompt is built from, and what shaping them cost."""

    passages: tuple[EvidencePassage, ...]
    """In **presentation** order — strongest at the edges. Not rank order."""

    budget_chars: int
    used_chars: int
    considered: int
    dropped_for_budget: int
    expanded: int
    merged: int
    """Candidates absorbed into a passage that already stood for another."""

    _by_id: Mapping[int, EvidencePassage] = field(default_factory=dict, repr=False)

    def by_id(self, identifier: int) -> EvidencePassage | None:
        """Resolve a citation reference, or ``None`` when it names nothing.

        ``None`` rather than a raise: a model citing an identifier outside the set is an
        expected failure the Evidence Gate handles by removing the claim, not an
        exceptional one that should abandon the answer.
        """
        return self._by_id.get(identifier)

    @property
    def ranked(self) -> tuple[EvidencePassage, ...]:
        """The same passages in rank order, for a source list a reader reads."""
        return tuple(sorted(self.passages, key=lambda passage: passage.id))

    @property
    def is_empty(self) -> bool:
        return not self.passages


def assemble_evidence(
    candidates: Sequence[RetrievedChunk],
    *,
    parents: Mapping[UUID, PendingChunk],
    citations: Mapping[UUID, tuple[Citation, ...]] | None = None,
    budget_chars: int,
    expand_below_chars: int = FRAGMENT_CHARS,
) -> EvidenceSet:
    """Build the evidence set from reranked candidates.

    ``parents`` maps a candidate's chunk id to the parent containing it, absent where the
    candidate has none. ``citations`` supplies source regions for *parent* chunks; a
    candidate's own citations are carried on it already.

    ``expand_below_chars`` expands only a candidate shorter than it. **Measured, and the
    opposite of what this module was first written to do.** Expanding everything looked
    obviously right — the corpus has 1,211 children under the 48-token floor — but measured
    over six real queries the reranker had already filtered them out: retrieved children
    averaged 1,574 characters and **1 of 48 was a fragment**. Full expansion then traded 8
    passages of ~450 tokens for 3 of ~1,651, discarding half the reranked evidence to make
    the survivors longer. Targeting fragments costs almost nothing and helps the only case
    that occurs.

    ``0`` disables expansion; a very large value restores the expand-everything behaviour,
    which is kept so the two can be compared rather than assumed.
    """
    if budget_chars <= 0:
        raise ValueError(f"budget_chars must be positive, got {budget_chars}")
    if expand_below_chars < 0:
        raise ValueError(
            f"expand_below_chars must not be negative, got {expand_below_chars}"
        )

    resolved = _resolve(
        candidates,
        parents=parents,
        citations=citations or {},
        expand_below_chars=expand_below_chars,
    )
    merged = len(candidates) - len(resolved)

    # Identifiers are assigned here and nowhere else, so they are dense from 1 by
    # construction. Assigning them during resolution and repairing them after budgeting
    # left two places that could disagree about what [3] means.
    kept: list[EvidencePassage] = []
    used = 0
    dropped = 0
    for passage in resolved:
        if kept and used + passage.char_count > budget_chars:
            # Whole passages only. Truncating one would cut a sentence the model then
            # cites, and §14.9 makes citations offsets into stored text — a trimmed
            # passage no longer matches the span its citation names.
            dropped += 1
            continue
        kept.append(_with_id(passage, len(kept) + 1))
        used += passage.char_count

    ordered = _edge_order(tuple(kept))
    return EvidenceSet(
        passages=ordered,
        budget_chars=budget_chars,
        used_chars=used,
        considered=len(candidates),
        dropped_for_budget=dropped,
        expanded=sum(1 for passage in kept if passage.expanded),
        merged=merged,
        _by_id={passage.id: passage for passage in kept},
    )


def _resolve(
    candidates: Sequence[RetrievedChunk],
    *,
    parents: Mapping[UUID, PendingChunk],
    citations: Mapping[UUID, tuple[Citation, ...]],
    expand_below_chars: int,
) -> list[EvidencePassage]:
    """Expand short candidates and merge those landing on the same passage.

    Rank order is preserved: a merged passage keeps the best rank of its members, which is
    the rank it would have had anyway, so merging never reorders the set.
    """
    order: list[UUID] = []
    seen: dict[UUID, EvidencePassage] = {}

    for candidate in candidates:
        parent = (
            parents.get(candidate.chunk_id)
            if len(candidate.text) < expand_below_chars
            else None
        )
        identity = parent.chunk_id if parent is not None else candidate.chunk_id

        existing = seen.get(identity)
        if existing is not None:
            seen[identity] = _absorb(existing, candidate)
            continue

        order.append(identity)
        seen[identity] = _passage_of(
            candidate, parent=parent, parent_citations=citations
        )

    return [seen[identity] for identity in order]


def _passage_of(
    candidate: RetrievedChunk,
    *,
    parent: PendingChunk | None,
    parent_citations: Mapping[UUID, tuple[Citation, ...]],
) -> EvidencePassage:
    """One passage, from the parent where there is one and the child where there is not.

    ``id`` is left at 0 and assigned by the budgeting step. The rank recorded is the
    candidate's own, so there is one source of truth for where retrieval placed it.
    """
    if parent is None:
        return EvidencePassage(
            id=0,
            chunk_id=candidate.chunk_id,
            stands_for=(candidate.chunk_id,),
            text=candidate.text,
            char_count=len(candidate.text),
            expanded=False,
            best_rank=candidate.rank,
            citations=candidate.citations,
            heading_path=candidate.heading_path,
            page_numbers=candidate.page_numbers,
            evidence_type=candidate.evidence_type,
            issuer_name=candidate.issuer_name,
            fiscal_period=candidate.fiscal_period,
            reporting_basis=candidate.reporting_basis,
            rerank_score=candidate.rerank_score,
        )

    # The parent's citations, not the child's: the passage shown is the parent's text, so a
    # reader following a citation must land on a region that text actually came from.
    return EvidencePassage(
        id=0,
        chunk_id=parent.chunk_id,
        stands_for=(candidate.chunk_id,),
        text=parent.text,
        char_count=len(parent.text),
        expanded=True,
        best_rank=candidate.rank,
        citations=parent_citations.get(parent.chunk_id, candidate.citations),
        heading_path=parent.heading_path,
        page_numbers=parent.page_numbers,
        evidence_type=parent.evidence_type,
        issuer_name=parent.issuer_name,
        document_type=parent.document_type,
        fiscal_period=parent.fiscal_period,
        reporting_basis=parent.reporting_basis,
        rerank_score=candidate.rerank_score,
    )


def _absorb(passage: EvidencePassage, candidate: RetrievedChunk) -> EvidencePassage:
    """Record that another candidate also landed on this passage.

    The text is unchanged — it already contains the second candidate, because a parent
    contains its children by construction and ``verify_parents`` asserts it on every
    chunking run. Only the record of what the passage stands for grows.
    """
    if candidate.chunk_id in passage.stands_for:
        return passage
    return EvidencePassage(
        id=passage.id,
        chunk_id=passage.chunk_id,
        stands_for=(*passage.stands_for, candidate.chunk_id),
        text=passage.text,
        char_count=passage.char_count,
        expanded=passage.expanded,
        best_rank=min(passage.best_rank, candidate.rank),
        citations=passage.citations,
        heading_path=passage.heading_path,
        page_numbers=passage.page_numbers,
        evidence_type=passage.evidence_type,
        issuer_name=passage.issuer_name,
        document_type=passage.document_type,
        fiscal_period=passage.fiscal_period,
        reporting_basis=passage.reporting_basis,
        rerank_score=passage.rerank_score,
    )


def _with_id(passage: EvidencePassage, identifier: int) -> EvidencePassage:
    """Renumber a passage after budgeting, so identifiers stay dense from 1."""
    if passage.id == identifier:
        return passage
    return EvidencePassage(
        id=identifier,
        chunk_id=passage.chunk_id,
        stands_for=passage.stands_for,
        text=passage.text,
        char_count=passage.char_count,
        expanded=passage.expanded,
        best_rank=passage.best_rank,
        citations=passage.citations,
        heading_path=passage.heading_path,
        page_numbers=passage.page_numbers,
        evidence_type=passage.evidence_type,
        issuer_name=passage.issuer_name,
        document_type=passage.document_type,
        fiscal_period=passage.fiscal_period,
        reporting_basis=passage.reporting_basis,
        rerank_score=passage.rerank_score,
    )


def _edge_order(passages: tuple[EvidencePassage, ...]) -> tuple[EvidencePassage, ...]:
    """Place the strongest passages at the beginning and the end.

    A long context is read least attentively in the middle, and published RAG guidance is to
    reorder so higher-scoring passages sit at both ends. Odd ranks ascend from the front and
    even ranks descend to the back, so passage 1 opens the block and passage 2 closes it.

    Deterministic, and reversible: a reader sees :attr:`EvidenceSet.ranked` instead, so this
    ordering shapes what the model attends to without changing what anyone is shown.
    """
    front = passages[0::2]
    back = passages[1::2]
    return (*front, *reversed(back))
