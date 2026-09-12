# FinSight

FinSight is a self-hosted system for analysing company financial filings. It ingests supported
filings, preserves their source structure, extracts a bounded set of structured financial facts,
and answers natural-language questions with traceable evidence.

Scope, architecture, data strategy, evaluation approach, and known limitations are defined in
[PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Development rules are defined in
[CLAUDE.md](CLAUDE.md). This README describes only how to set up and verify what is currently
implemented.

## Status

Phase 2 — PostgreSQL infrastructure and authoritative database connectivity.

Implemented: packaging and tooling configuration, the `finsight` package, application settings
including database configuration, and a local PostgreSQL service.

Not yet implemented: database migrations, document ingestion, parsing, the Fact Ledger, retrieval,
generation, the Evidence Gate, the user interface, and the evaluation harness. The repository grows
one phase at a time; a directory exists only once its capability is implemented.

## Prerequisites

- Windows with Command Prompt
- Python 3.12
- Docker Desktop with the WSL 2 backend and Linux containers (verified, not used yet)
- Ollama running on the Windows host (verified, not used yet)

Measured host details are recorded in
[ENV-001](evaluation/decision_records/architecture/ENV-001-environment-validation.md).

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

PostgreSQL runs in a container and is authoritative for all application state. Docker Desktop must
be running with the WSL 2 Linux-container backend.

```cmd
docker compose config
docker compose up -d
docker compose ps
```

`docker compose config` fails with a named variable if anything required is missing from `.env`.
The database is published on the loopback interface only. Data lives in the named volume
`finsight-postgres-data` and survives `docker compose down`.

```cmd
docker compose down
```

`docker compose down -v` additionally destroys the volume and every row in it.

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
| `data/` | Corpus manifest, reference seed data, fixtures, golden sets |
| `evaluation/` | Experiment configurations and decision records |
| `scripts/` | Environment and dependency verification helpers |
| `artifacts/` | Generated evaluation outputs (not committed) |

Paths appear as their capabilities are implemented.

## Project claims

FinSight is production-inspired. It is not commercially production-certified, hallucination-free,
prompt-injection-proof, or universally accurate, and it does not provide investment advice.
