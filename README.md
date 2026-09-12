# FinSight

FinSight is a self-hosted system for analysing company financial filings. It ingests supported
filings, preserves their source structure, extracts a bounded set of structured financial facts,
and answers natural-language questions with traceable evidence.

Scope, architecture, data strategy, evaluation approach, and known limitations are defined in
[PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Development rules are defined in
[CLAUDE.md](CLAUDE.md). This README describes only how to set up and verify what is currently
implemented.

## Status

Phase 1 — project foundation and environment validation.

Implemented: packaging and tooling configuration, the `finsight` package skeleton, and foundation
application settings.

Not yet implemented: document ingestion, parsing, the Fact Ledger, retrieval, generation, the
Evidence Gate, the API, the user interface, and the evaluation harness. The repository grows one
phase at a time; a directory exists only once its capability is implemented.

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

Copy the environment template and adjust values as needed:

```cmd
copy .env.example .env
```

`.env` is never committed.

## Verification

```cmd
scripts\windows\check-environment.cmd
scripts\windows\verify-dependencies.cmd
python -m ruff check .
python -m mypy src
python -m pytest
```

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
