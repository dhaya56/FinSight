# ADR-006 — Provisional Reranker

- **Status:** accepted, **provisional**
- **Date:** 2026-10-05
- **Phase:** 7 — narrative retrieval
- **Decision:** rerank with **`cross-encoder/ms-marco-MiniLM-L-6-v2`**, run locally through `transformers`, behind a `Reranker` protocol that keeps the choice reversible.
- **This is not a production reranker selection.** §23.9 reserves selection to a comparison balancing "quality improvement against latency, memory, and failure complexity", and §23.3 names a BGE reranker as the challenger. No such comparison has been run, because the golden question set it needs does not exist.

## Context

§23.2 is explicit: "MiniLM is the lightweight baseline for candidate reranking." So the
blueprint nominates it for exactly this position, and this record adopts it, states
what it costs, and says what has not been measured — the same shape as **ADR-002**
(PyMuPDF) and **ADR-004** (Nomic). The mitigation is not an argument that the choice is
right; it is that the choice is cheap to reverse.

## Why MiniLM, beyond the blueprint naming it

| | Measured on this host |
|---|---|
| Availability | **Already in the local cache**, weights included |
| New Python dependencies | **None.** `transformers` and `torch` are already pinned |
| Model download required | **None** |
| Parameters | **22,713,601** |
| Output head | 1 label — a regression score, not a probability |
| Loads offline | Yes, `local_files_only=True` |
| Ordering sanity | relevant passage **−1.14**, irrelevant **−11.45** |

The tokenizer was already in use for chunk budgeting (`chunking/tokens.py`), so the
window is known exactly rather than estimated — which is what made the fit check below
a measurement instead of a hope.

## The input fits the window, measured on the worst case

MiniLM's window is 512 tokens. Query + deterministic context + chunk text, over the
**200 largest chunks in the corpus**:

| | Tokens |
|---|---|
| Median | 441 |
| p90 | 453 |
| **Max** | **461** |
| Over 512 | **0 of 200** |

So no chunk in the corpus is truncated today. Truncation is still enabled as a safety
net, and the asymmetry with the embedder is deliberate: the embedder **refuses**
over-long input because truncation there made text permanently unfindable, while here
a truncated tail costs *ordering* for one candidate in one query.

## Latency is the binding constraint, and §23.4's trigger is live

§23.1 requires the reranker to "improve ordering under the local latency and memory
budget". Measured on this host's CPU with real chunk text:

| Candidates | Median |
|---|---|
| 10 | 607 ms |
| 25 | 1,700 ms |
| 50 | **4,005 ms** |
| 100 | **9,135 ms** |

Roughly linear at 87 ms per candidate in isolation, and **95 ms per candidate in the
pipeline** once text resolution and context composition are included. End to end at the
configured depth of 25:

| | Median |
|---|---|
| Reranked query | **2,544 ms** |
| Reranker off | **175 ms** |
| Reranking share | **93%** |

**Batch 8, measured**: it beats 16 and 32 at every depth, because a larger batch pads
every sequence to the longest in it and the wasted compute outweighs the fewer forward
passes.

§23.4 says "FlashRank is tested only if reranker latency prevents interactive use."
Whether 2.5 s prevents interactive use is a product judgement rather than a
measurement, but the trigger condition is now evidenced rather than hypothetical. The
options, in increasing cost: lower the depth (10 candidates puts a query near 950 ms),
quantise or export to ONNX, or evaluate FlashRank under §23.4.

**Depth 25 is a latency choice, not a quality one.** §23.5 leaves "input depth and final
evidence count" baseline-driven, and no quality comparison exists to justify any depth.
25 was chosen as the point where the reranker has enough to reorder while a query stays
near two seconds; 50 would make reranking 94% of a four-second query.

## What it does on real data

One query, `"what does the company say about credit risk"`, same 25 candidates, reranker
on and off:

- **4 of the top 5 positions changed.**
- **2 chunks entered the top 5 that the fused order did not contain at all** — which is
  the depth separation earning its cost.
- Fused rank 1 was an Ola Electric *fair-value table fragment*; reranked ranks 1–3 are
  three passages that each open by defining or quantifying credit risk.

**This is an illustration and not evidence of quality.** One query, no relevance labels,
and the reader judging the improvement is the author. §22.6's metrics on a golden set are
what would settle it.

A useful incidental observation: the reranked scores on that query were **4.36, 3.62,
1.77, −0.12, −0.47**, with a natural sign change where relevance visibly drops off. A
score floor at zero would be a far more defensible filter than the dense-cosine floor
ENV-010 measured as query-dependent — and it is still a threshold §4 reserves for the
developer.

## What keeps it reversible

- **`Reranker` protocol.** No model runtime crosses it. `reranking/cross_encoder.py` is
  the only module that loads weights, mirroring the rule confining boto3 to
  `s3_store.py` and HTTP embedding to `ollama_embedder.py`.
- **The stage is optional by construction** (§23.1). `rerank_enabled` switches it off
  deliberately, and a failure returns the fused order with a flag (§23.8). Nothing
  downstream may assume reranking happened.
- **The model is recorded per result**, so two orderings produced by different rerankers
  are distinguishable and a trace can be reproduced (§20.13).
- **Nothing is stored.** Reranking happens per query, so changing or removing the model
  costs no re-indexing — unlike the embedding model, where a change is a 43-minute
  re-embed.

## Consequences and current limits

- **Quality is unmeasured.** §23.9's selection needs a golden question set. Nothing here
  claims MiniLM reranks well, only that it reranks, deterministically, within the
  window, and visibly changes order on a real query.
- **Latency is 93% of a query** at the configured depth, and the model is CPU-only on a
  host measured at 0.8 GB free memory during an earlier failure. Peak reranker memory is
  **unmeasured** and is owed against §41.11 alongside the Phase 8 generation model.
- **No BGE comparison** (§23.3), and no FlashRank evaluation (§23.4) despite the trigger.
- **Absent from CI**, like the embedding model: the HF cache does not exist on a fresh
  runner, so integration tests skip the real model and the deterministic fake covers
  orchestration. The fake carries **no semantics** by design, so no test can accidentally
  assert that reranking improves results.
- **Scores are logits**, unbounded and negative here. They order candidates. §27 forbids
  presenting an evidence-support band as a correctness probability, and a raw logit is
  further from one still.

## What would trigger revisiting

A recorded comparison under §23.9 — MiniLM against a BGE reranker on identical candidate
lists and query classes — with developer approval. Or a measured failure: latency that
makes the UI unusable, peak memory colliding with `llama3.1:8b` under §41.11, or a
quality comparison showing the reranker reorders no better than fusion alone, which would
make 93% of the query latency unjustifiable.
