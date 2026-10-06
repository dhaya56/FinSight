# FinSight

FinSight is a self-hosted system for analysing company financial filings. It ingests supported
filings, preserves their source structure, extracts a bounded set of structured financial facts,
and answers natural-language questions with traceable evidence.

Scope, architecture, data strategy, evaluation approach, and known limitations are defined in
[PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Development rules are defined in
[CLAUDE.md](CLAUDE.md). This README describes only how to set up and verify what is currently
implemented.

## Status

Phase 7 — narrative retrieval. **A question typed at the command line returns ranked passages from
real filings, each citing the exact source elements it was built from.**

```cmd
python -m finsight.cli.main search "what does the company say about credit risk"
```

Implemented: packaging and tooling, application settings, PostgreSQL with Alembic migrations, an
S3-compatible object store behind a backend-neutral port, health and readiness endpoints, document
intake — validation, content-addressed preservation of originals, and identity recording — PDF
extraction into a citable source representation of pages, blocks, tables, cells and footnotes with
exact coordinates, a governed development corpus of real filings the extraction path has been
measured against, and the retrieval path: structure-aware chunking, local embedding, a Qdrant index
carrying dense and BM25 sparse vectors, hybrid retrieval under hard metadata filters, reciprocal
rank fusion, and cross-encoder reranking.

The development corpus is indexed end to end: **1,403 pages → 40,476 blocks → 5,637 chunks → 4,867
indexed vectors** across three filings, under chunking configuration 4.

Intake has no HTTP route yet. PROJECT_BLUEPRINT.md §28.2 requires authentication on every
non-health route, so upload is exposed in the authentication phase. Extraction, chunking, indexing
and search are driven from the command line in the meantime.

The PDF producer is **provisional**, not selected. No evaluation has compared it against
alternatives on real filings; see [ADR-002](evaluation/decision_records/architecture/ADR-002-provisional-pdf-producer.md).
Pages that yield no text record a coverage gap rather than being read by other means.

### No table detector is admitted

Tables are extracted, stored and citable. They are **excluded from the retrieval path**, and that
exclusion is a measured decision rather than a sequencing convenience —
[ADR-003](evaluation/decision_records/architecture/ADR-003-table-detector-no-admission.md).

Two detectors were measured against human-verified judgement of real filing pages. On the six pages
carrying tables, PyMuPDF's `text` strategy bounded **0 of 6** correctly and Docling bounded **2 of
6**. Docling is right where PyMuPDF is catastrophically wrong — it returns nothing on the four pages
that hold no table, where `text` claimed a table on every one — and two of six is not a production
detector. Its characteristic failure is a region offset by a row or two: clipping a final row,
starting after the first few, absorbing the line beneath.

Every table therefore carries a quality verdict — `accepted`, `review_required` or `rejected` — and
only an accepted table is evidence. A region the page's ruling lines do not support is refused
outright, and a run of rules no region covers is recorded as a coverage gap rather than lost.

**The verdict does not compensate for the detector.** Measured on the same six pages, its signal ran
*against* boundary quality: it accepted both regions that contained no table at all. No consumer may
read `accepted` as evidence that a region bounds a table. The reconstruction heuristics and their
known misfires are catalogued in
[evaluation/limitations/reconstruction-heuristics.md](evaluation/limitations/reconstruction-heuristics.md).

Docling needs ~530 MB of model weights that are not in its wheel. `scripts/stage_docling_models.py`
fetches them at pinned commits over verified TLS; with `FINSIGHT_DOCLING_ARTIFACTS_PATH` set, a
missing model raises instead of being downloaded mid-parse (§20.6).

Reading order is **positional** — top to bottom, then left to right — which is not column-aware.
Measured across the 1,403 pages of the development corpus, a column-aware ordering would differ on
195 of them (14%): 25% of one annual report, 13% of another, and under 1% of the offer document.
Choosing between the two needs a recorded comparison on development data (§35.4), so the limitation
is measured and asserted by test rather than assumed away.

Not yet implemented: processing jobs, the Fact Ledger, generation, the Evidence Gate, and the
evaluation harness. The repository grows one phase at a time; a directory exists only once its
capability is implemented.

### How generation will be grounded

The phase in progress adds answers. Worth stating here because the design was changed on
evidence, and the earlier one is still described in older records:

The model **emits claims with citation references and never writes a value**. Code resolves each
reference to the source span stored in PostgreSQL, and a numeral reaches a reader only if it
appears in a span its own claim cites. An unsupported claim is removed and the answer becomes
partial; an answer with nothing supported abstains.

This replaces typed numeric placeholders with deterministic substitution, which the blueprint
originally specified. The reason is that a placeholder moves the failure rather than removing it:
a model that emits the wrong placeholder produces a numeral that is genuinely source-bound,
passes every gate check, and is wrong — undetectable by construction. A citation reference is
checkable, because the resolved span either contains the asserted numeral or does not.
[ADR-009](evaluation/decision_records/architecture/ADR-009-citation-resolution-over-typed-placeholders.md)
carries the reasoning, the amended blueprint sections, and an audit of what was adopted and
rejected from current practice.

**Arithmetic is refused rather than attempted.** Asked how much a figure grew year on year, the
system reports both values with their periods and declines the subtraction, because comparison and
calculation need the typed records the Fact Ledger will hold and no amount of prompting substitutes
for them.

### The retrieval path

```text
question
  → analyse with PostgreSQL's text-search configuration (the same one that built the lexemes)
  → BM25 over Qdrant sparse vectors  +  dense over Qdrant   [both under §20.2 hard filters]
  → reciprocal rank fusion on ranks, never on scores
  → cross-encoder reranking
  → deduplicate overlapping source regions
  → passages, each citing source elements
```

Four properties worth knowing before reading results.

**BM25 is the primary lexical path, which contradicts the blueprint.** §9.7 commits to PostgreSQL
full-text search and says it is "not described as BM25". BM25 was adopted on the developer's
direction and recorded in
[ADR-005](evaluation/decision_records/architecture/ADR-005-bm25-as-the-primary-lexical-path.md).
Term weights are computed at index time; **Qdrant applies IDF at query time**, because that half
needs collection-wide statistics a per-chunk writer cannot have. Full-text search is retained as
§20.12's degradation path and is reached automatically when Qdrant is unavailable, with a flag.

**Fusion is on rank, and that is measured rather than conventional.** On one query BM25's top score
was 12.02 and full-text search's 0.23 for the same corpus and question; a dense cosine lives in
[-1, 1]. Combining those scales directly lets whichever happens to be largest decide the ranking.

**The embedding model, the reranker and the PDF producer are all provisional**, each adopted with a
recorded decision and none selected:
[ADR-002](evaluation/decision_records/architecture/ADR-002-provisional-pdf-producer.md),
[ADR-004](evaluation/decision_records/architecture/ADR-004-provisional-embedding-model.md),
[ADR-006](evaluation/decision_records/architecture/ADR-006-provisional-reranker.md).

**Nothing here claims retrieval quality.** Every figure in
[ENV-010](evaluation/decision_records/architecture/ENV-010-retrieval-validation.md) is throughput,
latency, coverage or behaviour. §22.6's Recall@k, MRR and nDCG need a golden question set that §34
has not produced, so no measurement in this repository is evidence that retrieval returns the right
passage.

### What is known to be wrong

Measured, recorded, and **not yet fixed**. Listed here because they are spread across seven records
and a reader deciding whether to trust a result needs them in one place. Severity is this project's
judgement, not a metric.

| # | Problem | Measured | Where |
|---|---|---|---|
| 1 | **All three retrieval stages are blind to negation.** `not` is an English stopword, so "is not expected to lose market share" and "is expected to lose market share" produce **identical lexemes**; the reranker separates them by 0.32 of a ~20-point range. Opposite facts, indistinguishable | identical lexeme sets; 0.32 separation | register §21 |
| 2 | **Multi-column reading order is positional.** A column-aware ordering would differ on **195 of 1,403 pages (14%)**, and interleaved chunks read as prose while being two columns spliced together | 14% of pages, verbatim example | register §16 |
| 3 | **Dense retrieval is nearly blind to which number a sentence states.** `1,234.56 crore` against `4,321.65 crore` scores **0.9863**; changing the fiscal year moves the vector *less* than changing the metric | cosine table | register §21 |
| 4 | **An acronym cannot reach its own expansion.** Zero shared lexemes for all seven pairs tested, dense cosine 0.52–0.64. "PAT" reaches 8 chunks where "profit after tax" reaches 229 | 7 pairs, corpus counts | register §21 |
| 5 | **19% of detected tables have their text split across chunks**, one across 25, so a header row and its figures can land apart. **128 of those 166 are unavoidable** — the table exceeds the child budget outright (median 734 tokens, max 3,801) and no chunking rule reunites it. Two attempts at the remainder were measured and rejected | 166 of 861; achievable gain measured at 2 | register §20 |
| 6 | **24% of children are under the stated size floor**, 427 under 10 tokens, the smallest a single word | 1,212 of 4,969 | register §18 |
| 7 | **Reranking is 93% of query latency** — 2,273 ms median against 175 ms with it off. §23.4's FlashRank trigger is live | per-token scaling table | ADR-006, ENV-010 |
| 8 | **Fixed and verified.** Chunks that were up to 91% dot leaders outranked the sections they point at. Leader lines are now withheld: **zero** dot-dominated chunks remain, and the two probes that returned a table of contents at rank 1 now return the section itself | 78 children and 13 parents over 80% dots → 0; max dot share in results 0.84 → 0.02 | ADR-007, ENV-011 |
| 9 | **A footnote cannot be reached from the figure it qualifies.** The text is searchable — 34 of 36 bound footnotes are already in the index via the blocks they were read from — but `footnote_refs` resolves to nothing, so a retrieved figure never carries its own exclusion. Indexing the footnote elements would only duplicate text already present | 36 of 36 also stored as blocks, 34 findable | register §24 |
| 10 | **Consensus outranks exclusivity at every rank** while the fusion constant is 60, so a chunk both retrievers agree on beats one either ranked first alone | crossover arithmetic | ENV-010 |
| 11 | **A superscript footnote marker is stored as part of the number it annotates.** `get_text("blocks")` discards font size, so `145,000` with a superscript 1 becomes `145,0001`. Three cases in the development split, all glued, one turning a three-digit figure into a four-digit one. A floor, not a count | 3 of 3 glued, 0 separated | register §23 |

Three more that are deliberate rather than defective: table cells are excluded from retrieval
(ADR-003), QueryTrace is **not persisted** so §31.9 is unsatisfied, and no query planner derives
§20.2's filters from a question — they are CLI flags.

Problems 1, 3, 4, 7 and 10 are **blocked on evidence rather than effort**: each has a realistic
alternative, and §8 requires recorded evidence plus approval to choose one. With no golden
question set there is no way to show a fix helps, so they wait on §22.6. Problems 2, 6 and 11
share a cause in the producer — `get_text("blocks")` discards the geometry and font information
all three would need — so they belong to one extraction change, not three.

### The extraction architecture

Three layers, deliberately separate, with a fourth that does not exist yet.

| Layer | Owns | Must never |
|---|---|---|
| **Parser** | Opening the document, verbatim text, geometry, page properties, failure signals | Interpret. A heading is a large block, not a heading |
| **Detector** | Proposing table regions and grids, preserving the empty/absent distinction | Decide what a row *means*, or normalise away its own uncertainty |
| **Reconstruction** | Spans, header rows, header and row-label paths, units, aggregate rows, footnote binding | Import a PDF library, or rewrite verbatim text |
| **Adjudication** *(missing)* | Choosing between competing proposals per page and recording why | Discard the losing proposal without trace |

Only `pymupdf_adapter.py` and `docling_tables.py` import a PDF library, mirroring the rule that
confines the object-store SDK to one adapter. That boundary is what let one detector be swapped for
another without touching the schema, the citations, or anything downstream.

## Prerequisites

- Windows with Command Prompt
- Python 3.12
- Docker Desktop with the WSL 2 backend and Linux containers
- Ollama running on the Windows host (verified, not used yet)

Measured host details and validation results are recorded in the
[decision records](evaluation/decision_records/architecture/): ENV-001 (host envelope), ENV-002
(Docker and PostgreSQL), ENV-003 (object storage and schema), ENV-004 (extraction), ENV-005 (corpus
and real-document validation), ADR-001 (object-storage backend selection), and ADR-002 (provisional
PDF producer).

## Setup

Create and activate the virtual environment, then install the pinned dependencies:

```cmd
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

`requirements.txt` is the single pinned dependency file. It is generated from a verified
environment and must remain installable at all times.

Copy the environment template and set values:

```cmd
copy .env.example .env
```

`.env` is never committed. `FINSIGHT_POSTGRES_PASSWORD` is required and has no default. It is
rejected at startup when empty or when it matches a known default value such as `postgres` or
`changeme`. That check guards against well-known defaults; it is not a password-strength check.

## Local infrastructure

Two containers: PostgreSQL, authoritative for all application state, and SeaweedFS, which serves the
S3 API for immutable source objects. Docker Desktop must be running with the WSL 2 Linux-container
backend.

### Object-store credentials

SeaweedFS enforces S3 credentials only when given an identities file. Without one it accepts **any**
access key and secret, so this file is a security control rather than a convenience. Generate it
from the values already in `.env`:

```cmd
powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path docker/seaweedfs | Out-Null; $e=@{}; Get-Content .env | Where-Object { $_ -match '^FINSIGHT_S3_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' } | ForEach-Object { $p=$_ -split '=',2; $e[$p[0]]=$p[1] }; $json=@{identities=@(@{name='finsight'; credentials=@(@{accessKey=$e['FINSIGHT_S3_ACCESS_KEY_ID']; secretKey=$e['FINSIGHT_S3_SECRET_ACCESS_KEY']}); actions=@('Admin','Read','Write','List','Tagging')})} | ConvertTo-Json -Depth 6; Set-Content -Path docker/seaweedfs/s3.json -Value $json -Encoding ascii"
```

`docker/seaweedfs/s3.json` is gitignored because it holds the same credential as `.env`.
`s3.json.example` documents its shape.

### Starting the stack

```cmd
docker compose config
docker compose up -d
docker compose ps
```

`docker compose config` fails with a named variable if anything required is missing from `.env`.
Both services are published on the loopback interface only. Data lives in the named volumes
`finsight-postgres-data` and `finsight-objectstore-data` and survives `docker compose down`.

```cmd
docker compose down
```

`docker compose down -v` additionally destroys the volumes and everything in them.

## Database migrations

PostgreSQL is authoritative, so every schema change is a reviewed, reversible Alembic revision.

```cmd
alembic upgrade head
alembic current
```

Readiness reports the schema as current only when the database is at the head of `migrations/`, so
run `alembic upgrade head` after pulling changes. The database URL is not configured in
`alembic.ini`: it is built from settings so the password stays in `.env` alone.

## Running the API

```cmd
python -m uvicorn finsight.api.app:create_app --factory --reload
```

Two health surfaces are available:

| Endpoint | Behavior |
|---|---|
| `GET /health/live` | Reports that the process is running. Touches no dependency, so it answers while the database is down. |
| `GET /health/ready` | Returns 200 when every essential dependency is healthy, and 503 when one is not. |

Readiness checks database reachability and schema currency. It returns 503 when the database is
unreachable **or** when the schema is behind the head revision, and it names no component, because
it is unauthenticated.

## Extracting a document

Extraction turns a stored document version into source elements — pages, blocks, tables, cells and
footnotes with exact coordinates — that later phases cite. It runs from the command line until there
is an authenticated route to trigger it.

```cmd
python -m finsight.cli.main extract <document-version-id>
```

Table detection uses PyMuPDF by default. To use Docling instead, stage its model artifacts once and
point the setting at them:

```cmd
python scripts\stage_docling_models.py
set FINSIGHT_DOCLING_ARTIFACTS_PATH=model_cache\docling
```

Staging downloads roughly 530 MB over TLS with the host's own trust store; verification is never
disabled. Once staged, no parse reaches the network — a missing artifact raises instead of being
fetched.

The identifier is the version recorded by intake. Both containers must be running: the original is
read from object storage, and the elements are written to PostgreSQL.

Re-running against the same version under the same extraction configuration is a **no-op**, and
reports itself as one. Changing `EXTRACTION_CONFIG_VERSION` records a new run and moves the version
to it; the superseded run and its elements are kept, so citations issued against them keep
resolving.

A run reports one of three states:

| State | Meaning |
|---|---|
| `succeeded` | Every page yielded text |
| `partial` | Some regions were not extracted, each recorded as a coverage gap with a reason |
| `failed` | Nothing usable was produced; the attempt is recorded and may be retried |

`partial` is not a warning to dismiss. A page that could not be read is stored as a row with a
failure reason rather than silently omitted, so that later phases can tell a reader a region was
never searched rather than implying it held nothing.

The command prints identifiers, a state and a count. It never prints document content.

## Chunking, indexing and search

The three stages after extraction, each addressable for a whole corpus split so no identifier has to
be read out of the database by hand:

```cmd
python -m finsight.cli.main corpus ingest --split development
python -m finsight.cli.main corpus chunk  --split development
python -m finsight.cli.main corpus index  --split development
python -m finsight.cli.main search "what does the company say about credit risk"
```

Each stage is idempotent and says so. The work it skips, as the commands report it: re-running
`corpus ingest` on an unchanged corpus is **0.34 s against 251 s**, and `corpus chunk` **0.14 s
against 34 s**. Wall-clock is a couple of seconds longer either way, because every invocation pays
Python's start-up and `corpus chunk` loads a tokenizer before discovering it has nothing to do.

A single document can be driven with `chunk <document-version-id>` and `index <generation-id>`.

**Indexing is the slow stage and the cost is the embedding model.** Measured over two full corpus
runs: **1.93–2.02 chunks/s, 41–43 minutes for 4,969 chunks**, dominated by host-native Ollama. It is
a per-document one-time cost — a newly uploaded 369-page annual report costs ~57 s to extract, ~8 s
to chunk and ~13 min to embed — never a per-query cost.

**A full re-index happens only when a configuration version changes** (the embedding model,
`embedding_config_version`, `CHUNKING_CONFIG_VERSION`, or BM25's measured average document length).
That is a deliberate operator action, and the **old generation keeps serving queries until the new
one is indexed and reconciled**, so a re-index is background work rather than downtime (§11.13).

Ollama must be running on the host. If it is not, the events stay `pending`, the new generation stays
`shadow`, the previously active one stays queryable, and `corpus index` resumes with no flag — a path
exercised by a real outage, not only by a test. **Ollama appearing in the process list is not the
same as Ollama serving**; the check that matters is an API call, which is what the indexer makes.

### Pruning superseded points

Activation does not remove the previous generation's vectors, so a re-chunk doubles the collection.
After the first one: 9,836 points, of which 4,969 belonged to a superseded generation and could never
be returned, because retrieval binds every search to the active generations.

```cmd
python -m finsight.cli.main prune
python -m finsight.cli.main prune --confirm
```

Without `--confirm` it reports and changes nothing — the default, because rebuilding costs a 40-minute
re-index. Measured on the first run: **4,969 removed, 4,867 remaining, active points 4,867 before and
after**. That last figure is asserted, not merely printed: a prune that changed the active count
raises rather than reporting success.

This is the only destructive command in the project and it touches **only the derived index**. §29.2
makes Qdrant rebuildable from PostgreSQL, so a wrong prune costs time rather than evidence.
PostgreSQL keeps every superseded row — that is the audit trail the generation exists for, and §29.12
reserves removal for tombstoning. `--include-failed` is opt-in, because a failed generation is
retryable with `index --retry` and pruning it discards work a retry would skip.

### Searching

```cmd
python -m finsight.cli.main search "credit risk concentration" --limit 5
python -m finsight.cli.main search "revenue" --issuer "Infosys Limited" --since 2024
python -m finsight.cli.main search "total deposits" --no-rerank --full
```

Every filter is optional, and every one that is given is a §20.2 **hard** filter — `--issuer`,
`--document-type`, `--basis`, `--section`, `--year`, `--since`, `--evidence-type`. §7 forbids
similarity overriding scope, so a filter is never relaxed to find more results. `--no-rerank` returns
the fused order, which is how the two orderings are compared on identical candidates.

Each result prints the issuer, period, evidence type, pages, heading path, both scores, which
retrievers found it and at what rank, and **the source elements it cites**.

A query takes roughly **2.3 s** of processing warm, of which about **93% is the cross-encoder**;
`--no-rerank` returns in about **175 ms**. Those are in-process figures — a one-shot CLI invocation
adds several seconds loading Python, torch and the model, and the first query after that pays
Ollama's model load too (measured at 6.2 s cold against 337 ms warm). A long-lived process such as
the Phase 9 interface pays both once.

`search` is the one command that prints document text, and deliberately so — §6.8 makes inspecting
the evidence behind an answer a product capability. Nothing it prints is logged, and passages are
truncated to a snippet unless `--full` is given.

If Qdrant is unavailable the command still answers from PostgreSQL full-text search and prints
`DEGRADED: lexical_fallback_postgres_fts` **before** the results, so a reader who stops at the first
passage already knows the ordering is not the intended one.

## The development corpus

The filings FinSight develops and measures against, governed by PROJECT_BLUEPRINT.md §32 and §34.
Acquisition is manual and approval-gated; the procedure is in
[data/corpus/README.md](data/corpus/README.md).

### Two rules that the tooling enforces

**Corpus documents are never committed.** Public availability is not redistribution permission
(§32.8), so the repository holds `data/corpus/manifest.toml` and its checksums while the bytes stay
local. The three split directories are gitignored. The checksum is what makes the corpus
reproducible, not the files.

**Held-out documents are hashed but never read.** Entries in `held_out_pdf_core/` and
`held_out_format_supplement/` carry a `frozen_at` date, and any command that would read their
content is refused. Checksum verification is still permitted: hashing bytes discloses nothing, and
§34.6 forbids tuning against held-out data, not checking that it is intact. A freeze applied after
someone has looked inside protects nothing.

### Commands

```cmd
python -m finsight.cli.main corpus checksum data\corpus\<split>\<file>
python -m finsight.cli.main corpus validate
python -m finsight.cli.main corpus verify [--split <split>]
python -m finsight.cli.main corpus list [--split <split>]
python -m finsight.cli.main corpus ingest [--split development] [--report <path>] [--measure-memory]
```

| Command | Behaviour |
|---|---|
| `checksum` | Prints a paste-ready `[[document]]` block for a freshly acquired file, with split, filename, size, digest and media type filled in. It prints rather than writes, so the manifest stays a reviewed artefact rather than a generated one. |
| `validate` | Checks the manifest against the corpus rules — split names, freeze dates, digests, issuer disjointness across splits (§34.12), unique identifiers. Also reports any entry with unrecorded fields, so a gap stays distinguishable from an oversight. |
| `verify` | Confirms local bytes still hash to what was recorded. Runs across held-out splits too. |
| `list` | Lists recorded documents and marks the frozen ones. |
| `ingest` | Puts documents through intake and extraction. Defaults to the development split; held-out is refused by the store, not by a flag check. Re-running is a no-op. `--measure-memory` records peak Python allocation and inflates the timings, so it is off by default. |

`validate`, `verify`, `list` and `checksum` need no running services. `ingest` needs both
PostgreSQL and the object store.

Measured results from the first real-document run are in
[ENV-005](evaluation/decision_records/architecture/ENV-005-corpus-and-real-document-validation.md).
Three documents are not a basis for quality claims: that run establishes that extraction is
complete, bounded and internally consistent, not that it is accurate.

## Verification

```cmd
scripts\windows\check-environment.cmd
scripts\windows\verify-dependencies.cmd
python -m ruff check .
python -m mypy src
python -m pytest
```

`python -m pytest` runs unit and contract tests only. Tests that require live infrastructure are
marked `integration` and excluded from the default run, so the suite passes without Docker. Run
them explicitly once the database is up:

```cmd
python -m pytest -m integration
```

They fail rather than skip when the database is unreachable.

Two suites **skip** rather than fail: the real embedding model and the real cross-encoder are loaded
from the local Hugging Face cache, which does not exist on a fresh machine or in CI. Deterministic
fakes stand in, so orchestration is covered everywhere and the models are exercised only where they
are staged. The fakes carry **no semantics** by design — similar sentences get dissimilar vectors —
so no test can accidentally assert that retrieval is good.

After moving, renaming or deleting a file, run `python -m ruff clean` before `ruff check .`. Ruff
caches per file, and a cached pass hides an import-order break that CI, which has no cache, fails on.

`check-environment.cmd` and `verify-dependencies.cmd` only inspect and report. They start no
service, install nothing, and download nothing.

## Repository layout

| Path | Contents |
|---|---|
| `src/finsight/` | Application package |
| `tests/` | Test suite, grouped by test type |
| `config/` | Versioned runtime pipeline configuration |
| `migrations/` | Alembic revisions; PostgreSQL schema history |
| `docker/` | Service configuration for the local stack |
| `data/` | Corpus manifest, reference seed data, fixtures, golden sets |
| `evaluation/` | Experiment configurations and decision records |
| `scripts/` | Environment and dependency verification, model staging, evaluation harnesses |
| `model_cache/` | Staged model artifacts (not committed) |
| `artifacts/` | Generated evaluation outputs (not committed) |

Paths appear as their capabilities are implemented.

The retrieval path inside `src/finsight/`, in the order a question travels through it:

| Package | Owns | Must never |
|---|---|---|
| `chunking/` | Blocks into parent and child retrieval units | Import a database, a tokenizer or a model |
| `lexical/` | BM25 term weighting, and the analysis normalisation **both sides share** | Compute IDF — that needs collection statistics Qdrant has |
| `embedding/` | The embedding port, the Ollama adapter, a deterministic fake | Let a caller cross document and query prefixes |
| `vector_index/` | The vector-index port and the one module importing a vector SDK | Return text — a search answers with identifiers (§10.7) |
| `indexing/` | The outbox drain, and the enriched string that gets embedded | Hold a transaction across a model or vector call (§29.7) |
| `retrieval/` | Filters, both retrievers, fusion, allocation, deduplication, the pipeline | Let similarity override a hard filter (§7) |
| `reranking/` | The reranking port, the cross-encoder adapter, a deterministic fake | Be required — §23.1 keeps it optional under degradation |

## Project claims

FinSight is production-inspired. It is not commercially production-certified, hallucination-free,
prompt-injection-proof, or universally accurate, and it does not provide investment advice.
