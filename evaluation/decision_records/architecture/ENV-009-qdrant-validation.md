# ENV-009 — Qdrant Validation

- **Status:** complete for what it covers; resource limits and loaded memory outstanding
- **Date:** 2026-10-04
- **Phase:** 7 — narrative retrieval
- **Scope:** local validation of the vector index: image, health, collection shape, hybrid search, hard filters, idempotency. **Not a retrieval-quality measurement.**

## What this is

§29.2 makes Qdrant derived and rebuildable — "stores vectors and filter payloads
that can be rebuilt from PostgreSQL" — and §10.7 is blunter: "No vector-store
record can become financial truth." This record validates that the service works
and that the adapter cannot quietly violate either statement. Whether retrieval is
*good* is unmeasured and belongs to §22.6's comparison.

## Artifact

| | |
|---|---|
| Image | `qdrant/qdrant:v1.19.1` |
| Digest | `sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10` |
| Client | `qdrant-client==1.19.1` |
| New transitive pins | `grpcio`, `protobuf`, `h2`, `hpack`, `hyperframe`, `portalocker` |

The server tag tracks the client version deliberately. The two negotiate API
shapes, and a silent drift surfaces as a schema error on a collection that worked
yesterday.

## The two assumptions ADR-005 rested on

Both were recorded as unverified in the Phase 7 plan, with a commitment to return
for a decision if either failed. Both hold.

| Assumption | Result |
|---|---|
| The pinned client exposes a server-side **IDF modifier** | **Yes.** `models.Modifier` is `['NONE', 'IDF']` and `SparseVectorParams.modifier` accepts it |
| Dense and sparse vectors can **share one collection** | **Yes.** `create_collection` takes `vectors_config` *and* `sparse_vectors_config`; `PointStruct.vector` accepts named vectors |

So BM25 runs where the corpus statistics live: term weights are computed from
PostgreSQL's analysed lexemes, and Qdrant applies IDF at query time — the half of
BM25 that needs to know about the whole collection.

`models.Fusion` also exposes `RRF` and `DBSF` natively. **Not adopted.** §20.13
requires QueryTrace to record fusion inputs and §21.4 names Qdrant-native fusion as
a thing to *evaluate* against application-level RRF "for ranking reproducibility
and traceability". Application-level fusion keeps the trace.

## Health, and a defect found writing it

The image carries **bash but no curl, wget or nc**, so the healthcheck speaks HTTP
over bash's `/dev/tcp`. Verified against the image: `/healthz`, `/livez` and
`/readyz` all return 200.

The first version used `CMD-SHELL`, which runs `/bin/sh` — dash here — and
`/dev/tcp` is a **bash** feature. Every probe failed with
`cannot create /dev/tcp/127.0.0.1/6333: Directory nonexistent` and the service
never reported healthy. The check now names bash explicitly.

Worth recording because the manual verification *passed*: it ran
`docker exec … bash -c`, which is not what Compose runs.

## Degradation is real, not aspirational

§10.9 says "Qdrant can degrade to lexical retrieval" and §20.12 requires explicit
fallback paths. Qdrant is therefore registered as **`DEGRADABLE`**, not essential.

Verified by stopping the service: the probe reported unreachable and
`/health/ready` returned **200 `{"ready": true}`**. An essential classification
would have taken the whole service out of readiness over a capability with a
defined fallback.

## Adapter properties verified against the running service

- **Searches return chunk identifiers, not point identifiers.** Point ids are
  derived from chunk + model + configuration (§29.9) and resolve to nothing in
  PostgreSQL. An earlier version did not request the payload and returned those
  derived ids, so every search found matches that could not be read back — and the
  symptom was an empty result set, not an error.
- **Replay is idempotent** (§29.9): upserting the same chunk twice leaves one
  point, which is what makes the indexer safe to re-run after a crash mid-batch.
- **A new embedding configuration writes new points** rather than overwriting, so
  an active generation keeps serving while a shadow one is built (§11.12).
- **Hard filters cannot be escaped by similarity** (§7, §20.2). Two chunks holding
  the *identical* vector are separated only by their payload, and a filtered search
  returns one. Filters are a conjunction: an OR would let a close vector out of its
  scope.
- **The same filters apply to sparse search**, which is the point of one collection
  — a filter cannot be enforced on one retriever and forgotten on the other.
- **The collection name encodes model and width**, so changing the embedding model
  targets a different collection automatically. Mixing two vector spaces of the
  same width has no error to raise, so the naming removes the possibility rather
  than detecting it.

20 integration tests, run against the real service.

## Memory, and why no resource limits yet

Idle, with three services coexisting for the first time:

| Service | Idle memory |
|---|---|
| postgres | 76.6 MiB |
| objectstore | 218.8 MiB |
| qdrant | 49.4 MiB |

Against **7.6 GiB** available to Docker. Worth noting against ENV-001's 15.7 GB
host figure: Docker Desktop's WSL 2 VM is allocated roughly half the machine, so
the envelope the containers share is smaller than the host total — and Ollama sits
*outside* it, on the host.

`compose.yaml` has deferred resource limits since Phase 2 with a comment naming
"the phase where multiple services coexist and memory is measured" as the point to
add them. That is this phase, and they are **still not set**, for a narrower
reason: idle is not the figure a limit should be derived from, and nothing is
indexed yet. A value chosen now would be invented headroom. They are set once the
corpus is embedded and peak usage is measured.

## Production-configuration audit

Every item below came from a review against four production-practice sources, and
each was checked against the running service rather than reasoned about.

### Acted on

| Finding | Evidence | Resolution |
|---|---|---|
| **Payload indexes absent — the full-scan trap.** Filtering on an unindexed field makes Qdrant scan every candidate's payload | `payload_schema: NONE`. Measured at 20,000 points: **23.5 ms without an index against 11.5 ms with one, 2.0x**, widening with the collection because the scan is linear | Indexes created for all seven §20.2 filter fields, **before ingest** — Qdrant uses them to build filterable HNSW links, so adding them afterwards does not retroactively improve the graph |
| **HNSW parameters inherited silently** | `m=16`, `ef_construct=100` — the recommended production values, but by default | Stated explicitly. A future Qdrant changing its defaults would otherwise change this collection's recall with nothing in the diff |
| **Mismatched sparse indices and values unvalidated** | A shifted pairing would be accepted | Refused. The point would look healthy — right dimensions, right payload, right id — and score nonsense on every lexical query |

### Measured and deliberately not adopted

**Dot product instead of cosine.** Mathematically identical for unit vectors, which
the embedding port guarantees, and measured **1.4x faster — 13.8 ms against 19.2 ms
at 20,000 points.** Not adopted: Qdrant normalises on insert under cosine, so cosine
stays correct if the port's guarantee is ever violated, while dot would rank by
vector **magnitude** — longer documents first, silently and systematically. Five
milliseconds at this scale does not buy that risk. Revisit if latency binds.

**Quantization (scalar or product).** Cuts memory up to 4x and alters top-k
neighbours. Not adopted: a subtly different nearest neighbour is a subtly wrong
answer about a filing, §22.6 has measured no recall cost to justify the trade, and
memory is not currently a constraint. If it is ever adopted it applies to the dense
vectors **only** — sparse vectors carry meaning in exact irregular weights that
dense quantization destroys — and requires an `oversampling` setting so the rough
scan does not miss the true top-k.

**gRPC transport.** Recommended for throughput. **Could not be measured**: compose
publishes only the REST port, by design, and gRPC needs 6334. Left for the
throughput measurement, where the comparison can justify opening a second port.

### Checked and already correct

| Point | Status |
|---|---|
| Deterministic point ids, so an update overwrites rather than duplicating | Already implemented and tested — uuid5 over chunk + model + config (§29.9) |
| HNSW as the index algorithm | Qdrant's default and the right choice at this scale |
| Hybrid dense + sparse rather than dense alone | The whole design; ADR-005 |
| RRF rather than weighted score averaging | Planned application-level, because dense and BM25 scores are not comparable. §20.13 needs the fusion in QueryTrace, which native fusion does not expose |
| Batched writes rather than per-point upserts | The port takes a sequence; the indexer batches |
| Read consistency in a cluster | Single node. Not applicable, and noted so it is not forgotten if that changes |

### Open, with the reason

- **`on_disk` / memmap for vectors and the sparse index.** Left in RAM. The corpus
  is roughly 4,000 chunks — about 12 MB of vectors — so disk-backing would trade
  latency for memory that is not scarce. Revisit when the collection grows or when
  loaded memory is measured.
- **Low-weight term pruning.** BM25 weights derive from PostgreSQL lexemes rather
  than a learned sparse model, so the pathological "noise token" bloat the practice
  warns about does not arise the same way. Unmeasured.
- **Distinguishing "no terms" from "term generation failed".** The adapter refuses a
  malformed sparse pair but cannot tell a legitimately term-free chunk from one
  whose terms were lost upstream. The indexer can: a chunk whose `lexemes` is NULL
  is a pipeline failure, while an empty vector is a real outcome. Recorded as a
  requirement for the indexer rather than contorting the port.

## Limitations

- **No retrieval quality measured.** This record covers plumbing only.
- **No loaded-memory figure**, so no resource limits, so §38.11 remains unmet.
- **No throughput measurement** for upsert or search at corpus scale.
- **Exact vs approximate search policy unexamined** (§35.13). The collection uses
  Qdrant's defaults, which is a choice made by not choosing.
- **CI has no healthcheck for the service container.** The image carries no curl
  or wget and a service container cannot run the bash check, so readiness there
  rests on `ensure_collection` failing loudly.
- **No reconciliation command yet** (§29.11). `count(filters=…)` exists for it and
  nothing calls it.

## Open items

| # | Item | Owner |
|---|---|---|
| 1 | Peak memory with the corpus indexed, and resource limits derived from it | Commit 7 / ENV-010 |
| 2 | Upsert and search throughput at corpus scale | ENV-010 |
| 3 | Exact vs approximate search policy (§35.13) | retrieval evaluation |
| 4 | Administrator-triggered reconciliation between PostgreSQL and Qdrant (§29.11) | later phase |
| 5 | Qdrant-native RRF and sparse retrieval as §21 alternatives | §21 evaluation |
