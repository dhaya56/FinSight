# ADR-004 — Provisional Embedding Model

- **Status:** accepted, **provisional**
- **Date:** 2026-10-04
- **Phase:** 7 — narrative retrieval
- **Decision:** embed with **`nomic-embed-text`**, served by host-native Ollama, behind an `Embedder` protocol that keeps the choice reversible.
- **This is not a production model selection.** §22.10 reserves selection for "the smallest model that provides acceptable retrieval quality and operational behavior", measured under §22.6's Recall@k, MRR and nDCG on identical chunks and filters. No such comparison has been run, because the golden question set it needs does not exist.

## Context

§22 names three candidates and ranks them by intent rather than merit: **§22.2
Nomic Embed Text** is "the lightweight initial candidate for early integration and
baseline measurements"; **§22.3 BGE-M3** is "evaluated for dense retrieval and may
later enable learned sparse experiments"; **§22.4 BGE Large English** is gated on
two prior failures — only "if BGE-M3 is too heavy **and** Nomic underperforms".
§22.5 and §40.7 exclude 4B–8B models from the laptop target outright.

So the blueprint already nominates Nomic for exactly this position. This record
adopts it, states what that costs, and says plainly what has not been measured.

The precedent is **ADR-002**, which adopted PyMuPDF provisionally in Phase 4 for
the same reason: a comparative evaluation needs development data and a golden set
that do not yet exist, and waiting for them means building nothing. The mitigation
is not an argument that the choice is right — it is that the choice is cheap to
reverse.

## Why Nomic, beyond the blueprint naming it

| | Measured on this host |
|---|---|
| Availability | **Already pulled** in Ollama 0.35.1, 274 MB |
| New Python dependencies | **None.** `httpx` is already pinned; Ollama is an HTTP call |
| Model download required | **None** |
| Vector width | **768**, measured rather than assumed |
| Normalisation | **Already unit length** (norm measured 1.0) |
| Throughput | A batch of two documents in 0.09 s; a query in 0.05 s |
| Reproducibility | Identical text returns an identical vector (§22.1) |

The alternative worth naming is BGE-M3, which §22.3 marks for evaluation. It is
1024-dimensional, materially heavier on a CPU-only host (ENV-001 records no
accelerator and 15.7 GB shared with Ollama), and would require either
`sentence-transformers` or hand-rolled `transformers` inference plus a model
download. None of that is disqualifying; all of it is cost that a measurement
should justify rather than precede.

## Task prefixes, which are not optional

Nomic is asymmetric: it expects `search_document:` when indexing and
`search_query:` when searching. **Measured against the running model, Ollama does
not apply them** — the prefixed and plain forms of one sentence embed to a cosine
of **0.9388** of each other, so they are materially different vectors.

The adapter applies them, and the port exposes `embed_documents` and `embed_query`
as **separate methods** rather than one method with a flag. A flag makes it
possible to index with one prefix and search with the other, which degrades
retrieval silently and presents exactly as a poor model. Two methods make that
unreachable.

On one probe the prefixed pair gave a slightly wider margin between a relevant and
an irrelevant passage (+0.1565 against +0.1519). **That is one example and is not
evidence of quality**; the reason to apply prefixes is that the model documents
them, not that a single measurement favoured them.

## What keeps it reversible

- **`Embedder` protocol.** No HTTP client or model runtime crosses it.
  `ollama_embedder.py` is the only module that embeds over the wire, mirroring the
  rule confining boto3 to `s3_store.py` — which ADR-001 showed costs six files
  under a real backend swap.
- **The model identifier is recorded per generation** (§14.10). Two chunk
  populations embedded by different models are otherwise indistinguishable in one
  collection, and every distance between them is meaningless.
- **Dimensionality is configuration, not a constant.** `FINSIGHT_EMBEDDING_DIMENSIONS`
  moves with the model, and the adapter *verifies* the response width rather than
  trusting it, so a model swapped behind the same tag fails loudly instead of
  filling a collection with differently-shaped vectors.
- **The vector index is rebuildable** (§29.2, §29.14). Changing the model is a
  re-embed under a new generation, not a migration and not a data loss.

## Consequences and current limits

- **Retrieval quality is unmeasured.** §22.6's metrics need a golden question set
  that does not exist. Nothing in this phase may claim the model is good — only
  that the pipeline works.
- **Ollama is host-native and absent from CI.** Integration tests use a
  deterministic fake, so the real model is exercised only locally. That is a real
  coverage gap, recorded rather than hidden. The fake carries **no semantics** by
  design: near-identical sentences get dissimilar vectors, so no test can
  accidentally assert that retrieval is good.
- **Long-context behaviour is untested** (§22.7), and §22.7 cautions that "large
  chunks are not preferred by default" in any case.
- **Peak memory is unmeasured** against §41.11's shared envelope, now with Qdrant
  arriving alongside.
- **No Qdrant storage-footprint or rebuild-time figure** yet (§22.9).

## What would trigger revisiting

A recorded comparison under §22.6 and §35.14 — Nomic against BGE-M3 on identical
chunks, filters and queries — with developer approval. Or a measured failure:
retrieval that misses evidence a lexical search finds, memory contention with
generation under §41.11, or indexing throughput that makes a full re-embed
impractical. Any of these is a model swap behind the port, not a schema change.
