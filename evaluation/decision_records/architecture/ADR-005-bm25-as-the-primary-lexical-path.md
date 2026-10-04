# ADR-005 — BM25 as the Primary Lexical Path

- **Status:** accepted
- **Date:** 2026-10-05
- **Phase:** 7 — narrative retrieval
- **Decision:** the primary lexical retriever is **BM25, carried as Qdrant sparse vectors with server-side IDF**. PostgreSQL full-text search is retained as the degradation path, not removed.
- **This contradicts the blueprint and is recorded for that reason.** §9.7 commits the lexical path to PostgreSQL FTS and says explicitly that it is "not described as BM25", keeping `rank-bm25` as an offline evaluation baseline. §21.9 requires an explicit architecture decision to depart from that. **The departure was directed by the developer**, who asked for a production BM25 implementation rather than an evaluation baseline.

## Context

The blueprint's position is defensible: PostgreSQL FTS needs no new service, its
analysis chain is already configured, and `ts_rank_cd` is a usable relevance
signal. What it is not is BM25. `ts_rank_cd` has no term-frequency saturation, no
document-length normalisation and no inverse document frequency — it weights by
position and cover density. On financial prose those omissions bite in a specific
way: a page repeating "deposits" eleven times outranks the note that explains the
deposit base, because nothing saturates the repetition.

The developer's instruction was explicit: implement BM25 the way production systems
do, not "some basic, fast thing". That rules out the tempting shortcut — a SQL
expression that computes the BM25 arithmetic over `ts_stat` and a join. That
expression would be *arithmetically* BM25 and structurally nothing like it: no
inverted index, no maintained collection statistics, no top-k pruning, and a cost
linear in the candidate set on every query.

## Decision

BM25 is split across the two places its two factors belong.

```text
score(q, d) = Σ  IDF(t) · tf(t,d)·(k1+1) / ( tf(t,d) + k1·(1 - b + b·|d|/avgdl) )
             t in q
             └──┬──┘  └──────────────────────┬──────────────────────────────┘
            Qdrant, at query time        this process, at index time
```

| Factor | Where | Why there |
|---|---|---|
| Saturated, length-normalised term frequency | `src/finsight/lexical/bm25.py`, at index time | Depends only on the one document, so it is computed once and stored |
| **IDF** | **Qdrant**, via `models.Modifier.IDF` on the sparse vector configuration | Depends on the whole collection — how many documents hold the term — which changes whenever a document is added |

**A client that computed IDF itself would freeze the collection statistics at the
moment each chunk was written.** A term's weight would then depend on when its
chunk happened to be indexed, and a term that was rare in October would stay
weighted as rare after it became common. That is the defect the split exists to
avoid, and it is the reason this is not simply "sparse vectors".

Qdrant's sparse index supplies what the SQL expression could not: a real inverted
index, maintained per-term document counts, and top-k retrieval that does not scan
the candidate set.

### The analysis chain is PostgreSQL's, on both sides

`to_tsvector` already stemmed and stopped the chunk text when the chunk was written
(§9.7), and the **same configuration analyses the query**. No tokenizer is written
here.

That is a deliberate constraint rather than a convenience. Two tokenizers are two
things to keep in step, and the failure when they drift is silent: queries simply
stop matching, with no error anywhere. The integration test that pins this asks for
"lending" and requires it to find a chunk that says "lends" — it fails the moment a
second analysis chain appears on either side.

It also keeps §9.7's remaining requirement meaningful. The text-search
configuration is still a recorded, changeable choice; changing it is a re-chunk
under a new generation, because the stored lexemes were analysed with the old one.

### Term indices are hashed, not looked up

Sparse indices are `blake2b(lexeme)` truncated to 32 bits.

The alternative is a vocabulary table, which has to be maintained, migrated and
kept consistent between the indexer and the API — and a vocabulary **miss** at
query time is a silent recall loss. Hashing trades that for collisions, which are
benign and bounded: two unrelated terms sharing an index merge their postings and
lose precision on those two terms.

`hashlib`, never `hash()`. Python's `hash` is salted per process from
PYTHONHASHSEED, so a salted index would map a word one way in the indexer and
another in the API, and **every lexical query would return nothing while every
component reported success**. The unit test pins four literal indices for that
reason.

## Parameters, and which of them is measured

| Parameter | Value | Status |
|---|---|---|
| `k1` | 1.2 | **Unmeasured.** The long-standing BM25 default, adopted rather than chosen. Its effect here is small: 72.2% of term occurrences in the development corpus appear exactly once in their chunk, where saturation does nothing |
| `b` | 0.75 | **Unmeasured**, and unlike `k1` this one matters — see below |
| `average_document_length` | **99.21** | **Measured** over all 4,816 development children |
| Field weighting | none | §9.7 reserves it for recorded evidence; it needs a golden question set |

### `average_document_length` is measured, not borrowed

FastEmbed's BM25 defaults this to 256 because a library cannot know the corpus.
This project can, so it measured: the mean token-position count per child chunk
across the three development filings, under chunking configuration 1.

| | Token positions per child chunk |
|---|---|
| Mean | **99.21** |
| Median | 105 |
| p10 | 5 |
| p90 | 191 |
| Max | 255 |
| Distinct lexemes (for contrast) | 63.09 |

Token *positions*, which is what BM25 means by document length — not distinct
lexemes, which would understate every repeated term by ignoring its repetitions.

The p10-to-p90 spread is **38x**, which is why `b` matters here more than `k1`:
with lengths that uneven, how strongly length is normalised materially reorders
results. `b` and the chunk-size comparison §18.12 requires should be measured
together, because the spread is a property of the chunker rather than of the
corpus.

**Changing `average_document_length` invalidates every stored weight**, not only
new ones. Qdrant takes document weights as given, so a collection written under two
different averages holds scores that are not comparable — a chunk indexed under one
average outranks an equally good chunk indexed under another, with nothing to show
it. Hence `BM25_CONFIG_VERSION`, and hence the value is versioned rather than
recomputed per run. It describes three filings and will need re-measuring when the
corpus changes materially.

## What is retained, and why that answers §20.12

**PostgreSQL FTS is kept.** `chunks.lexemes` is a real `tsvector` with a GIN index,
and it is not vestigial — it is simultaneously the source of the sparse weights and
a working independent lexical retriever.

This is the answer to the objection that consolidating lexical retrieval into
Qdrant removes the independent fallback §20.12 mandates. It does not: losing Qdrant
costs dense retrieval **and** BM25, and degrades to FTS with a flag, rather than
losing lexical retrieval entirely. Qdrant stays `DEGRADABLE` in the readiness probe
(§10.9) for the same reason.

## Verified against the running service

Asserted in `tests/integration/test_indexing_pipeline.py`, against real PostgreSQL
and real Qdrant:

| Property | How it is pinned |
|---|---|
| The analysis chain closes | "lending" finds a chunk saying "lends"; an unrelated chunk is not returned |
| **Server-side IDF actually ranks** | Four chunks all containing "deposits", one also containing "debentures"; a query for both ranks the debenture chunk first. **Nothing in this process knows "debenture" is rare** — the weights sent to Qdrant carry no IDF at all — so this passing is the evidence that IDF is being applied where it was configured |
| A term absent from the corpus matches nothing | Rather than returning a weak match |
| A term-free chunk is still indexed | Every token a stopword: no sparse vector, dense-searchable, not an error |
| Replay rewrites rather than duplicates | §29.9's deterministic point ids |

## Consequences and current limits

- **No comparison against FTS has been run.** This record justifies BM25 on
  construction and on the developer's direction, **not** on measured retrieval
  quality against the alternative. §22.6's metrics need a golden question set that
  does not exist, so no claim is made that BM25 retrieves better here — only that
  it is BM25, which `ts_rank_cd` is not.
- **`k1` and `b` are unmeasured**, and `b` interacts with chunk size.
- **`average_document_length` describes three filings.** §29.11's reconciliation is
  where drift should be noticed; nothing currently watches it.
- **Hash collisions are unmeasured** on this corpus. Expected to be negligible over
  2³² indices, but not counted.
- **Query-side term frequency is ignored.** BM25 has no query-frequency factor;
  BM25F and the original Okapi weighting do. Adopting one would change the formula
  recorded here.
- **The sparse index's memory footprint is unmeasured** against §41.11's shared
  envelope.

## What would trigger revisiting

A recorded comparison of BM25 against PostgreSQL FTS on a golden question set, with
developer approval — which is also what would let §9.7's original position be
re-adopted if FTS measured as well or better. Or a measured failure: hash
collisions degrading precision, sparse-index memory exceeding the envelope, or
`average_document_length` drifting far enough from a grown corpus that re-indexing
costs more than maintaining per-segment statistics would.
