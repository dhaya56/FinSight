# ENV-010 — Retrieval Validation

- **Status:** **in progress.** Opened at commit 9 and completed at commit 12.
- **Opened:** 2026-10-05
- **Phase:** 7 — narrative retrieval

## What this is

The measurement record for Phase 7's retrieval path. It is opened early and
deliberately: the figures below were taken while building commits 7 through 9, and a
record created at the end of the phase would have been written from memory or not at
all. Each section says which commit produced it.

**What is still owed at commit 12**, so an incomplete record is not mistaken for a
complete one:

| Owed | Why it is not here yet |
|---|---|
| ~~Reranker latency and its degradation path~~ | **Measured at commit 10; see below and ADR-006** |
| Peak reranker memory against §41.11 | Latency was the binding constraint and was measured first |
| End-to-end `search` cost with QueryTrace persisted | Commit 11 |
| Peak container memory, and the compose resource limits derived from it | Needs a sampled peak, not the spot samples below |
| Index storage footprint on disk | Not yet measured |
| Any statement about retrieval **quality** | No golden question set exists (§22.6, §34). Nothing in this record is a quality claim |

---

## Indexing cost, measured twice on the real corpus

Commit 7 built the indexer; commit 8's lexeme change forced a full re-index, which
gave a second independent measurement of the same work.

### Run 1 — chunking configuration 2

| Filing | Children | Seconds | Rate |
|---|---|---|---|
| Infosys AR FY2025 | 1,352 | 613.95 | 2.20/s |
| HDFC Bank AR FY2025 | 2,069 | 915.84 | 2.26/s |
| Ola Electric DRHP | 1,548 | 928.13 | 1.67/s |
| **Total** | **4,969** | **2,457.92** | **2.02/s** |

### Run 2 — chunking configuration 3

| Filing | Children | Seconds | Rate |
|---|---|---|---|
| Infosys AR FY2025 | 1,352 | 650.67 | 2.08/s |
| HDFC Bank AR FY2025 | 2,069 | 960.81 | 2.15/s |
| Ola Electric DRHP | 1,548 | 964.26 | 1.61/s |
| **Total** | **4,969** | **2,575.75** | **1.93/s** |

**The two runs agree within 5%**, which is what makes these usable as a baseline
rather than as one observation. Ola Electric is consistently the slowest per chunk in
both runs — its chunks are the longest of the three (mean 246 narrative tokens against
Infosys's 239 and HDFC's 215), and embedding cost scales with sequence length.

### This corrects ADR-004's correction, mildly

ADR-004 records a correction from 12.3 texts/s (measured on 34-character probes) to
**1.68 texts/s** on real enriched chunks, and projects "~47 minutes" for the corpus.
The projection was slightly **pessimistic**: the corpus average is **1.93/s** and the
actual elapsed time was **43 minutes**.

Both figures are real and neither is wrong. 1.68/s came from 64 consecutive chunks of
one filing; 1.93/s is the average over three filings of differing chunk length. The
operational figure to plan a re-embed around is **1.93/s**, and the per-filing spread
is 1.61–2.26/s.

### One failure, and what it cost

Run 2 failed partway with `WinError 10061` — Ollama's server not listening, its tray
app respawning a process that died immediately, with **0.8 GB of 15.7 GB physical
memory available**. Recorded in full against ADR-004's §41.11 open item.

What matters for this record is the recovery: **all 4,969 outbox events stayed
`pending`, the new generations stayed `shadow`, and the previously active generations
stayed active and queryable.** Re-running `corpus index` with no flag resumed and
completed. Unplanned, and a better test of the transient-failure path than a test.

---

## Query latency

### Components, measured under indexing load (commit 7)

Load affects these only through contention; none of them embeds.

| Step | Median | Min | Max |
|---|---|---|---|
| Analyse the query (PostgreSQL) | 1.6 ms | 1.5 | 5.5 |
| Build the BM25 query vector | 0.0 ms | 0.0 | 0.1 |
| BM25 search, filtered (Qdrant) | 13.4 ms | 12.3 | 34.8 |
| Dense search, filtered (Qdrant) | 10.7 ms | 10.0 | 44.9 |
| Resolve 10 chunk ids to text | 3.3 ms | 3.3 | 10.4 |
| **Total, excluding query embedding** | **~29 ms** | | |

Query embedding is the other term: **33 ms** median warm over 8 calls (min 30.8, max
43.7).

### Whole hybrid search (commit 9)

| | |
|---|---|
| Warm, end to end | **337 ms** |
| Cold, end to end | **6.2 s** |

The cold figure is almost entirely Ollama loading the model on first use, not fusion
or filtering — the same cold-start pattern the embedding probe showed (11.05 s for a
first batch against ~5.3 s steady).

**Roughly 10% of the warm figure is redundant**: the query is encoded once per
evidence type, so the same text is embedded twice at 33 ms each. Left in place with
the reasoning recorded in `retrieval/hybrid.py`; removing it means the retriever
protocol accepting a set of filters.

**No reranking is in these figures.** Commit 10 adds a cross-encoder on CPU, which is
expected to dominate everything above.

---

## BM25 against PostgreSQL full-text search (commit 8)

`"credit risk management"`, unfiltered, top 5, same corpus and same query:

| | BM25 (Qdrant sparse) | PostgreSQL FTS |
|---|---|---|
| Latency | 158 ms | **15 ms** |
| Top score | 12.02 | 0.23 |
| Ranks 1–2 | identical to FTS | identical to BM25 |
| Ranks 3–5 | diverge | diverge |

Three findings, also recorded in ADR-005. **The fallback is ten times faster**, being
one local GIN query rather than a network round trip plus sparse scoring — so
"degraded" means less well ranked, never slower. **The score scales differ by two
orders of magnitude**, which is the measured argument for fusing on rank (§20.6)
rather than on score. And **the two agree on the strongest results and diverge at the
margin**, which is the shape a fallback should have.

**This is not the comparison §9.7 owes.** That one needs relevance labels and a golden
question set. This says the paths behave differently and plausibly; it does not say
which retrieves better.

---

## Fusion behaviour on the real corpus (commit 9)

`"what does the company say about credit risk"`, unfiltered, limit 10:

| Rank | Fused score | Contributions | Evidence type |
|---|---|---|---|
| 1 | 0.03202 | `bm25@1, dense@4` | table_derived |
| 2 | 0.03200 | `bm25@2, dense@3` | narrative |
| 3 | 0.03200 | `bm25@3, dense@2` | table_derived |
| 4 | 0.03178 | `bm25@5, dense@1` | narrative |
| 5 | 0.03154 | `bm25@1, dense@6` | narrative |
| 6–10 | ~0.0159–0.0164 | one retriever each | mixed |

Two properties visible here that the unit tests assert in miniature:

- **Every one of the top five was found by both retrievers**, and everything below by
  one. That is RRF rewarding agreement, which is the reason to fuse rather than
  concatenate.
- **Both evidence types appear**, so §20.5's allocation floor is reaching the
  candidate set rather than being a configuration value nothing acts on.

Per-retriever contribution before fusion was 10 from BM25 and 10 from dense, with no
degradation flags.

---

## RRF audited against production practice (commit 9)

Four claims from two production-practice reviews, each checked against the live index
rather than reasoned about. Two are confirmed, one is confirmed with a **correction to
the prescribed fix**, and one does not arise here.

### 1. "Score oblivion" is real — and the prescribed floor would break retrieval

**The diagnosis is right.** RRF uses rank only, so a dense rank 1 at cosine 0.49 and a
dense rank 1 at cosine 0.77 contribute identically. The prescribed fix is an absolute
floor before fusion, "e.g. < 0.65".

**Measured, that floor would be a disaster on this model and corpus.** Top-20 dense
cosines, five queries, 100 scores pooled:

| Query | Top | Median | Min |
|---|---|---|---|
| "what does the company say about credit risk" | 0.7713 | 0.7161 | 0.7052 |
| "total deposits" | 0.6657 | 0.6301 | 0.6236 |
| "EBITDA margin" | 0.7184 | 0.5847 | 0.5733 |
| "PAT" | 0.5391 | 0.5044 | 0.5014 |
| "RoNW" | 0.4888 | 0.4587 | 0.4539 |

| Floor | Dense results dropped |
|---|---|
| 0.50 | 20% |
| 0.55 | 40% |
| 0.60 | 51% |
| **0.65** | **77%** |
| 0.70 | 79% |

A floor at 0.65 removes **every** dense result for `PAT`, `RoNW` and most for
`EBITDA margin` — precisely the acronym queries where the same research says dense is
weakest and most needs help. It would silently disable dense retrieval for the hardest
cases while appearing to improve precision on the easy ones.

**The reason is that the distribution is query-dependent, not corpus-dependent.** A
natural-language question sits at 0.70–0.77 and a bare acronym at 0.45–0.54, because
cosine against a short query is systematically lower. An absolute floor therefore
encodes "how verbose was the question", not "how good is the match". If a floor is
ever adopted it has to be **relative to the top score for that query**, and that is a
threshold requiring a golden set (§22.6) and developer approval (§4). Nothing is
implemented, and the figure 0.65 is recorded here as **measured wrong for this
deployment** rather than as a pending task.

### 2. Consensus always outranks exclusivity — confirmed, and k=20–30 does not fix it

Measured on `"what does the company say about credit risk"`, limit 20: candidates with
two contributing retrievers occupy ranks 1–9, and **the best single-retriever candidate
is rank 10.** Nine dual, eleven single, cleanly separated.

That is not an accident of the data, it is arithmetic. A chunk found by both retrievers
at rank *r* each scores `2/(k+r)`; a chunk found by one retriever at rank 1 scores
`1/(k+1)`. Consensus wins whenever

```text
2/(k+r) > 1/(k+1)   ⟺   r < k + 2
```

With **k = 60 and a depth of 20, every r satisfies that** — so a chunk agreed on by
both retrievers *anywhere* in the top 20 outranks a chunk either retriever ranked
first exclusively. That is exactly the "overshadowed acronym" the research describes,
in exact form.

**The prescribed fix is insufficient**, and this is the correction worth carrying: the
research suggests lowering k to 20–30 "for high-precision financial lookups". At depth
20, `r < k + 2` still holds for every rank at k = 20 or 30. Making an exclusive rank-1
able to beat deep consensus needs **k < depth − 2**, i.e. below about 18 at this depth.

The research's own worked example does flip, because its weak document is still found
by the second retriever at rank 100:

| | k = 60 | k = 20 |
|---|---|---|
| #20 by both | 2/80 = **0.0250** | 2/40 = 0.0500 |
| #1 by dense, #100 by sparse | 0.0226 | **0.0559** |

So k genuinely matters, and the direction of the advice is right — but the specific
range does not address the pure-exclusive case it was offered for.

**Nothing is changed.** k is a threshold, §20.6 does not specify one, and choosing it
needs the golden set. `retrieval/fusion.py` carries the crossover condition so the
next person does not re-derive it.

### 3. Tie-breaking non-determinism does not arise

The concern is that a search engine returning tied scores in arbitrary order makes RRF
inputs differ per call, producing flaky answers. Measured over 5 repeated calls each:

| Retriever | Query | Identical order |
|---|---|---|
| BM25 | "credit risk" | **yes** |
| dense | "credit risk" | **yes** |
| BM25 | "total deposits" | **yes** |
| dense | "total deposits" | **yes** |

Input order is stable. The fallback also orders by `score DESC, c.ordinal`, and fusion
breaks its own ties by `(−score, best rank, chunk id)` with a test asserting two runs
agree. So determinism holds at all three stages — measured at the input, enforced at
the output.

### 4. Retrieval depth is not yet separable from the final limit

The research asks for a wide retrieval window narrowed by fusion and then by a
reranker — "top-100 each, RRF to top-50, rerank to top-5". Today `limit` bounds both
retrieval depth and fused output, so asking for 5 results retrieves only 5 per type.

That is a real gap and it belongs to the reranker commit, where the three numbers
become meaningful together. Noted here because the crossover arithmetic above depends
on depth: a wider retrieval window makes consensus dominance *stronger*, not weaker.

---

## Reranking (commit 10)

§23.7 requires per-query reranking latency and peak resource use. Latency is measured;
peak memory is owed.

### Latency by depth, isolated

Real chunk text at realistic length, this host's CPU, batch 8, median of 3:

| Candidates | Median | Per candidate |
|---|---|---|
| 10 | 607 ms | 61 ms |
| 25 | 1,700 ms | 68 ms |
| 50 | **4,005 ms** | 80 ms |
| 100 | **9,135 ms** | 91 ms |

Batch size was measured too: **8 beats 16 and 32 at every depth.** A larger batch pads
every sequence to the longest in it, and the wasted compute outweighs the fewer forward
passes — the opposite of the usual batching intuition, and worth knowing before someone
"optimises" it upward.

### End to end, steady state, depth 25

| | Median | Min | Max |
|---|---|---|---|
| Reranked query | **2,544 ms** | 2,441 | 2,705 |
| Reranker off | **175 ms** | 169 | 202 |
| Reranking delta | **2,369 ms** over 25 candidates — 95 ms each | | |

**Reranking is 93% of a query's latency.** That is the single most important figure in
this record for Phase 9: a Streamlit page issuing this query waits two and a half
seconds, almost all of it in the cross-encoder.

§23.4 — "FlashRank is tested only if reranker latency prevents interactive use" — is
therefore **measurably triggered** rather than hypothetical. Whether 2.5 s *prevents*
interactive use is a product judgement, not a measurement. The levers, cheapest first:
depth 10 puts a query near 950 ms; ONNX export or quantisation; then FlashRank.

### Input fits the window, worst case measured

Query + deterministic context + chunk text, over the **200 largest chunks** in the
corpus, tokenized by the model's own tokenizer:

| | Tokens |
|---|---|
| Median | 441 |
| p90 | 453 |
| **Max** | **461** |
| Over the 512 window | **0 of 200** |

Nothing is truncated today. Truncation stays enabled as a safety net, and the asymmetry
with the embedder is deliberate: there, truncation made text permanently unfindable and
is refused; here, it costs ordering for one candidate in one query.

### What it changes on a real query

`"what does the company say about credit risk"`, identical 25 candidates, reranker off
then on:

| | Fused order | Reranked |
|---|---|---|
| Rank 1 | Ola Electric fair-value **table fragment** | Infosys, "Credit risk on cash and cash equivalents is limited…" |
| Rank 2 | Infosys credit-loss allowance | Infosys credit-loss allowance |
| Rank 3 | HDFC transition-risk **table** | HDFC, "Credit Risk is the possibility of losses…" |
| Positions changed in the top 5 | — | **4 of 5** |
| Chunks promoted from outside the fused top 5 | — | **2** |

The two promoted chunks are the depth separation earning its cost: without a window
wider than the final count, neither could have been considered.

Reranked scores on that query were **4.36, 3.62, 1.77, −0.12, −0.47**, with a sign change
where relevance visibly falls away. A floor at zero would be a far more defensible filter
than the dense-cosine floor measured above as query-dependent — and it is still a
threshold §4 reserves for the developer.

**This is an illustration, not evidence of quality.** One query, no relevance labels, and
the person judging the improvement wrote the code. §22.6's metrics on a golden set are
what would settle it.

---

## Store state after indexing (commits 7–9)

| | |
|---|---|
| Qdrant points | 4,969 — exactly the active children |
| Collection status | green, 4 segments |
| `indexed_vectors_count` | 4,808 of 4,816 at the time sampled |
| Payload indexes | 10 fields, all present |
| Chunks in PostgreSQL | 5,759 (4,969 children + 790 parents) |
| Chunk sources | 66,928 |
| Outbox events | 4,969, all `completed` |

Container memory **sampled twice during indexing**, not peak:

| Service | Memory |
|---|---|
| postgres | 259 MiB |
| qdrant | 267 MiB |
| objectstore | 192 MiB |

**These are spot samples and the compose resource limits must not be set from them.**
`compose.yaml` defers limits until "the corpus is embedded and peak usage is
measured"; the corpus is now embedded, so the precondition is satisfied and the peak
measurement is owed at commit 12. Setting a limit from two samples is the invented
headroom that comment exists to prevent.

---

## Coverage, re-verified against the PDFs (commit 8)

| | Infosys | HDFC Bank | Ola DRHP |
|---|---|---|---|
| PDF pages / page elements recorded | 369 / **369** | 590 / **590** | 444 / **444** |
| Page numbers | 1..369, no gaps | 1..590, no gaps | 1..444, no gaps |
| Blocks / blocks reaching a chunk | 14,686 / 14,669 | 16,271 / 16,105 | 9,519 / 8,145 |
| Pages holding text not reached by any chunk | **0** | **0** | **0** |
| Chunks with no lexemes, pages or sources | **0** | **0** | **0** |

The 1,557 blocks that reach no chunk are **whitespace only** — verified with Python's
`str.strip()`, which is what the chunker uses, against 0 holding any non-whitespace
character. A SQL check with `btrim` reports them as content loss and is wrong, because
`btrim` trims spaces and not newlines.

---

## What this record does not claim

- **Nothing about retrieval quality.** Every figure here is throughput, latency,
  coverage or behaviour. §22.6's Recall@k, MRR and nDCG need a golden question set
  that §34 has not produced, and no number in this document may be read as evidence
  that retrieval returns the right passage.
- **Nothing about HNSW at scale.** Recall measured 1.0000 against exact search on 60
  probes, because `indexing_threshold: 10000` means the graph is not being traversed
  at this size (ENV-009 open item 6). These latencies are brute-force latencies.
- **Nothing about a multi-document corpus.** Three filings, two fiscal periods, one
  jurisdiction-dominant set.
