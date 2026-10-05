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

## Three runtime properties measured against the running service

These are not model-quality questions. They are the ways a local Ollama
deployment fails without saying so, and each was tested rather than assumed.

### The context window is 2,048 tokens, not 8,192 — and overflow is silent

`nomic-embed-text` is natively an 8,192-token model. **Ollama anchors it to 2,048**,
and discards the remainder without an error:

| Input | Appending a distinctive sentence changed the vector? |
|---|---|
| ~1,300 tokens | yes — cosine 0.997959 |
| ~1,950 tokens | yes — cosine 0.999788 |
| **~2,600 tokens** | **no — cosine 1.000000** |
| ~3,900 tokens | no — cosine 1.000000 |

A binary search put the boundary between **2,015 and 2,062** single-token words,
i.e. exactly 2,048. A chunk half-embedded this way is indexed, searched and
trusted, while the discarded half is unfindable — no error, no log line, nothing
to distinguish it from a document that never contained the text.

Child chunks are bounded at 384 tokens and so sit far clear, but **nothing enforced
that**, and a configuration change could have crossed the line silently. The
adapter now refuses over-long input (`EmbeddingInputTooLongError`) rather than
letting the model truncate. The bound is in characters because the adapter
deliberately carries no tokenizer; English financial prose measured at ~4.4
characters per token, so the default of 6,000 sits well below the ~9,000 the window
allows. Dense numerals tokenize worse and would reach the wall sooner, which is the
residual risk and why it is configurable.

### Ollama serialises embedding; client concurrency buys nothing

| Client threads | Throughput |
|---|---|
| 1 | 12.3 texts/s |
| 2 | 13.2 texts/s |
| 4 | 12.8 texts/s |
| 8 | 12.3 texts/s |

Batching through one request gives the same 12.0–12.5 texts/s. So the adapter
batches to cut round trips and does **not** thread, and a future attempt to add a
worker pool would be wasted effort. At this rate a 1,303-chunk filing embeds in
roughly 105 seconds.

#### Correction, 2026-10-05: that rate was measured on the wrong text

**The throughput table above is real but was measured on 34-character sentences,
and it does not describe indexing.** The conclusion about threading survives; the
rate does not. Measured against the same running model, one variable changed at a
time, after a warm-up call:

| Case | Mean characters | Throughput |
|---|---|---|
| Short probe — the shape measured above | 34 | **32.4 texts/s** |
| Real child chunk text | 1,349 | **1.82 texts/s** |
| Real child chunk text, enriched for indexing | 1,456 | **1.68 texts/s** |

**An 18x shortfall**, and the "1,303-chunk filing in roughly 105 seconds" claim is
wrong by about 7x. The first full index of a real filing took **522 seconds for
1,301 chunks — 2.1 chunks/s**, which agrees with the 1.68/s above once the DB and
Qdrant round trips are counted as the small part they are.

The cause is simply sequence length: the probe was 40x shorter than the work. Nomic
on CPU costs time per token, so a per-text rate measured on one text length
predicts nothing at another. **The lesson is the one ENV-004 already recorded about
synthetic fixtures** — this is the same mistake in a different place, and it reached
a decision record rather than a test.

Two figures worth carrying forward:

- **Enrichment costs 8%** (1.82 → 1.68 texts/s). That is the price of the context
  §14.2 asks for, and it is cheap.
- **A full re-index of the development corpus is ~47 minutes** (4,816 children at
  1.7/s), not the ~6 minutes the old figure implied. That is the number to plan a
  re-embed around, and it makes `BM25_CONFIG_VERSION` and
  `embedding_config_version` changes genuinely expensive rather than nominally so.

The original table is left in place rather than rewritten, because the error was not
in the measurement — it was in generalising it to work it never covered.

The contention that *does* matter is with generation: §41.11 flags a shared 15.7 GB
envelope, and Phase 8 puts `llama3.1:8b` (4.9 GB) on the same host Ollama. That is
unmeasured and remains an open item.

### Re-embedding must be idempotent, and is not yet

Generations are per *document version*, so changing one filing never re-embeds the
corpus. But re-running chunking on an unchanged version with an unchanged
configuration currently produces a new generation and re-embeds every chunk —
~105 seconds of work for a byte-identical result.

Extraction already solves this: a partial unique index on
`(document_version_id, producer_policy, config_version)` makes a repeat run a
recorded no-op. **Chunking needs the same**, keyed on the chunking configuration
version. Recorded here as a requirement for the chunking service rather than left
to be rediscovered as an operational surprise.

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
  arriving alongside and `llama3.1:8b` due in Phase 8.

  **2026-10-05: the envelope risk stopped being theoretical.** A corpus re-index
  failed partway with `WinError 10061` — Ollama's server was not listening, and the
  tray app was respawning a server process that died immediately. The cause was the
  host, not the model: **0.8 GB of 15.7 GB physical memory available**, while commit
  was comfortable at 13.3 GB free. Ollama needs the model resident and there was
  nowhere to put it. The containers were not the cause either, holding 718 MB between
  PostgreSQL, Qdrant and the object store.

  Two things this says. First, **this is a host-capacity limit that the application
  cannot engineer around** — a smaller batch does not help, because the pressure is
  the model's residency rather than our request size. Second, Phase 8 putting
  `llama3.1:8b` (4.9 GB) on the same host Ollama is now a measured risk rather than a
  noted one, and §41.11 needs a real figure before that lands.

  **The pipeline handled it correctly**, which is the one good part. The embedding
  port classifies an unreachable model as transient, so all 4,969 outbox events
  stayed `pending`, the new generations stayed `shadow`, and the previously active
  generations stayed active and queryable. Recovery is re-running `corpus index` with
  no flag and no data loss — the behaviour `_write` was shaped for, exercised by an
  unplanned outage rather than a test.
- **Dense retrieval alone will miss exact identifiers.** A dense model matches
  meaning, not strings, so a query naming a specific figure, clause or code is
  precisely where it underperforms. That is why §20.3's lexical path and §20.7's
  reranker are part of the same phase rather than a later improvement.
- **No Qdrant storage-footprint or rebuild-time figure** yet (§22.9).

## What would trigger revisiting

A recorded comparison under §22.6 and §35.14 — Nomic against BGE-M3 on identical
chunks, filters and queries — with developer approval. Or a measured failure:
retrieval that misses evidence a lexical search finds, memory contention with
generation under §41.11, or indexing throughput that makes a full re-embed
impractical. Any of these is a model swap behind the port, not a schema change.
