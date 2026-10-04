# FinSight

FinSight is a self-hosted system for analysing company financial filings. It ingests supported
filings, preserves their source structure, extracts a bounded set of structured financial facts,
and answers natural-language questions with traceable evidence.

Scope, architecture, data strategy, evaluation approach, and known limitations are defined in
[PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Development rules are defined in
[CLAUDE.md](CLAUDE.md). This README describes only how to set up and verify what is currently
implemented.

## Status

Phase 6 — table detection and the completion of the source representation.

Implemented: packaging and tooling, application settings, PostgreSQL with Alembic migrations, an
S3-compatible object store behind a backend-neutral port, health and readiness endpoints, document
intake — validation, content-addressed preservation of originals, and identity recording — PDF
extraction into a citable source representation of pages, blocks, tables, cells and footnotes with
exact coordinates, and a governed development corpus of real filings that the extraction path has
been measured against.

Intake has no HTTP route yet. PROJECT_BLUEPRINT.md §28.2 requires authentication on every
non-health route, so upload is exposed in the authentication phase. Extraction is driven from the
command line in the meantime.

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

Not yet implemented: authentication, processing jobs, chunking, the Fact Ledger, retrieval,
generation, the Evidence Gate, the user interface, and the evaluation harness. The repository grows
one phase at a time; a directory exists only once its capability is implemented.

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

## Project claims

FinSight is production-inspired. It is not commercially production-certified, hallucination-free,
prompt-injection-proof, or universally accurate, and it does not provide investment advice.
