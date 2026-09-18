# FinSight Claude Code Instructions

## 1. Role and source of truth

Claude Code is the implementation assistant for FinSight. The developer is the human approver and operator.

Before planning or changing the project:

1. Read this file.
2. Read `PROJECT_BLUEPRINT.md` when project context is required.
3. Inspect only the repository areas relevant to the current task.

`PROJECT_BLUEPRINT.md` defines what FinSight is: its scope, architecture, technology stack, data strategy, experiments, security model, Docker deployment, evaluation approach, risks, and expected outcomes.

Do not reinterpret, broaden, or silently contradict the blueprint. If code and the blueprint conflict, report the exact conflict and wait for a decision.

Implementation state must be determined from repository files, tests, and command results, not from planned content in the blueprint.

## 2. Working environment

Repository:

```text
C:\Users\T9949\Desktop\Projects\FinSight
```

Expected terminal state:

```text
(.venv) C:\Users\T9949\Desktop\Projects\FinSight>
```

Use Windows Command Prompt semantics for project commands. Do not use Ubuntu or WSL paths for project files.

When Claude Code's internal shell is not Command Prompt, use:

```cmd
cmd.exe /d /c "<command>"
```

For Python-dependent commands, use the project virtual environment in the same invocation when required:

```cmd
cmd.exe /d /c "call .venv\Scripts\activate.bat && <command>"
```

Before Python work, verify that `python` resolves inside `.venv`. Docker commands may run from Windows Command Prompt while Docker Desktop uses its WSL 2 Linux-container backend.

Claude Code can read only commands it executes and outputs supplied by the developer. Do not assume access to unrelated terminal history.

## 3. Plan and approval workflow

For every new phase or task:

1. Inspect only relevant files.
2. State the goal and applicable blueprint areas.
3. List files to create, modify, move, or delete.
4. List dependencies, services, commands, tests, assumptions, risks, and exclusions.
5. Suggest a focused branch name.
6. Wait for approval before editing unless immediate implementation was explicitly authorized.

After approval:

- implement only the approved scope;
- create files when their capability is introduced;
- do not scaffold unrelated future modules;
- run relevant approved checks;
- read failures, fix the current scope, and rerun the checks;
- stop before Git-changing actions.

The complete repository structure may be maintained as a conceptual plan, but the physical repository must grow phase by phase and represent actual implemented work.

Stop and ask for direction when a task requires an unapproved dependency, model, dataset, architectural change, destructive migration, security exception, held-out-data access, or broader scope.

Never invent credentials, URLs, checksums, licences, controlled values, thresholds, measurements, results, or implementation completion.

## 4. Human approval boundaries

Obtain approval before:

- creating the first foundation structure;
- adding, removing, installing, upgrading, or downgrading dependencies;
- changing `requirements.txt`;
- downloading models or corpus documents;
- choosing dataset sources or split assignments;
- starting or stopping a multi-service Docker stack;
- applying migrations;
- changing environment variables or secrets;
- deleting files, data, containers, images, or volumes;
- using administrator privileges;
- making unapproved external network requests;
- changing architecture, CI/CD, deployment, or security behavior;
- selecting production models, parsers, chunking methods, retrieval methods, thresholds, or experiment winners.

## 5. Git and GitHub

Do not execute Git commands.

When Git work is required, provide the exact commands for the developer to copy and run. Explain what each command will do and wait for the developer to report the result before continuing.

Never claim that a branch, commit, push, pull request, or merge succeeded without the developer's output.

Before proposing implementation on `main`, warn the developer to create a focused branch.

At task completion, summarize changed files and recommend:

- branch name;
- files to stage;
- commit message;
- push command;
- pull-request next step.

## 6. Architecture constraints

Use the detailed architecture in `PROJECT_BLUEPRINT.md`. Preserve these non-negotiable rules:

- Python 3.12 is the implementation language.
- FastAPI and Pydantic v2 provide the API and validation layers.
- Streamlit is an API-only frontend and holds no data-store credentials.
- PostgreSQL is authoritative for application, processing, financial, query, audit, and evaluation state.
- Qdrant is a rebuildable derived vector index.
- Object storage is accessed only through the object-store abstraction and preserves immutable originals. SeaweedFS is the current local backend; adapters are named for the S3 protocol rather than the vendor, and no module outside the adapter imports a storage SDK.
- Ollama runs on the Windows host and is accessed through HTTPX.
- PyMuPDF and pdfplumber form the native PDF path; Docling is an evaluated layout-aware candidate; Camelot is a conditional table comparator or fallback.
- pandas and openpyxl handle spreadsheets; Beautiful Soup, lxml, defusedxml, and nh3 handle HTML/XML safely; Arelle is conditional on genuine XBRL/iXBRL data.
- FinSight owns orchestration, query planning, retrieval, calculations, evidence validation, and evaluation.
- Only `langchain-text-splitters` may be used as a low-level utility. Transitive LangChain packages must not enable tracing or orchestration.
- PostgreSQL full-text search is the current lexical path; Qdrant provides dense retrieval.
- Hard filters, RRF, cross-encoder reranking, bounded context expansion, and QueryTrace form the production-candidate retrieval path.
- Nomic versus BGE-M3, MiniLM versus a suitable BGE reranker, local generation models, and alternative sparse retrieval are selected through evaluation.
- Financial arithmetic uses `Decimal` and never authoritative model arithmetic.
- Typed placeholders, deterministic substitution, and the Evidence Gate protect released answers.
- Docker Compose provides the single-node deployment; Ollama remains host-native.
- GitHub Actions is introduced incrementally after equivalent local commands work.

Do not introduce excluded or deferred technologies unless their blueprint activation condition is satisfied and the developer approves them.

## 7. Evidence and financial correctness

- Preserve source, retrieval, and canonical fact representations separately.
- Cite only exact source regions, spans, tables, or cells.
- Never cite enriched retrieval text or generated summaries as source evidence.
- Keep deterministic table summaries free of generated financial conclusions.
- Keep all successfully extracted filing content searchable through Narrative RAG unless security rules block it.
- Use the Fact Ledger only for its approved bounded concept catalogue.
- Do not discard non-ledger content.
- A non-ledger number may be reproduced only when precisely source-bound; do not normalize, compare, or calculate it without an approved structured path.
- Preserve issuer, period, period nature, reporting basis, currency, unit, value state, assurance, source-presentation status, and provenance.
- Hard filters cannot be overridden by semantic similarity or reranking.
- The model has no tools, filesystem, database, object-store, or external-network capability.
- Unsupported factual numerals must not be released.
- Evidence-support bands are not correctness probabilities.
- Reprocessing uses shadow generations and atomic activation while valid active evidence remains available.
- PostgreSQL transactions remain bounded and exclude long network or model calls.
- Index synchronization uses the transactional outbox.

## 8. Dataset and experiment discipline

Before the first retrieval slice, prepare development data consisting of two authoritative PDFs with different layouts, one deterministic synthetic financial fixture, and one bounded negative fixture.

- Use official issuer, exchange, regulator, or offer-document sources.
- Do not infer redistribution permission from public availability.
- Assign documents to development or held-out splits before writing golden-set questions.
- Tune only on development data.
- Keep real, synthetic, format-supplement, security, and held-out results separate.
- Develop fixtures test-first or alongside the code path they exercise.
- Do not generate the complete fixture catalogue in advance.
- Never expose held-out answers for tuning.

Experiments must use versioned configurations and shared evaluation data. Record the baseline, changed factor, corpus, split, model versions, configuration, code revision, sample size, date, quality metrics, resource costs, and negative results.

Claude Code may implement experiment infrastructure but must not choose a winner by intuition. Production admission requires recorded evidence and developer approval.

## 9. Docker, workers, and CI/CD

Develop Docker and CI alongside the application:

1. Validate Docker Desktop, WSL 2, Linux containers, and Compose.
2. Introduce PostgreSQL, Qdrant, and S3-compatible object-storage infrastructure.
3. Run early Python services locally against containerized infrastructure and host-native Ollama.
4. Containerize the API, restricted parser worker, processing worker, and UI after their local paths work.
5. Add health checks, volumes, networking, and resource limits incrementally.
6. Validate the final Compose topology before release.

The restricted parser worker runs non-root, without parse-time outbound internet, and with bounded filesystem, CPU, memory, temporary storage, and time. The processing worker performs normalization, embeddings, indexing, reconciliation, and evaluation work with approved service access.

PostgreSQL polling is implemented first. Celery and Redis remain conditional on their blueprint validation. OpenTelemetry and optional observability services remain disabled by default.

CI begins only with locally validated commands and grows with the implementation. Do not put held-out evaluation, unrestricted model runs, large downloads, or complete real-document evaluation in routine pull-request CI. Threshold gates remain informational until an approved baseline defines them.

Continuous delivery is deferred until builds, images, tests, evaluations, and setup instructions are reproducible and verified. Do not create automatic production deployment.

## 10. Dependencies, security, and privacy

The project uses one pinned `requirements.txt`, created only from a clean verified Python 3.12 environment.

Dependency changes require approval, clean installation verification, relevant import tests, `pip check`, and `pip-audit`. Keep the file installable and never use placeholder versions.

Treat every document as untrusted. Preserve the blueprint controls for signature validation, structural limits, safe XML, sanitization, archive and workbook safety, parser isolation, prompt-injection handling, authentication, authorization, secrets, telemetry, deletion, tombstoning, reconciliation, and retention.

Never disable TLS or certificate validation, authorization, security scanning, or safety controls to force success.

Never expose or commit credentials, tokens, source-document contents, user questions, financial values, restricted documents, sensitive paths, or company-network details.

Do not claim hallucination freedom, prompt-injection immunity, universal accuracy, formal compliance, commercial production readiness, or investment-advice capability.

## 11. Coding and validation

- Write clear, typed Python 3.12 using canonical project terminology.
- Keep domain logic independent of FastAPI, Streamlit, PostgreSQL, Qdrant, object storage, Ollama, and other adapters where practical.
- Add abstractions only for a current boundary, approved alternative, or experiment.
- Do not add placeholder implementations for future phases.
- Use explicit errors, bounded transactions, deterministic identifiers, and idempotent background operations.
- Add the smallest relevant tests with every change.
- Do not weaken, delete, or skip a valid test merely to make code pass.

Run only checks that exist and are relevant, such as:

```cmd
python -m pytest
python -m ruff check .
python -m mypy src
python -m pip check
python -m pip_audit
docker compose config
```

Report every command as passed, failed, or skipped. Do not claim success when required checks remain failing.

## 12. Completion report

After an approved task, stop and provide:

1. summary of implemented scope;
2. files created, changed, moved, or deleted;
3. commands and checks with exact results;
4. errors encountered and corrections made;
5. remaining assumptions, risks, and deferred work;
6. suggested branch, staging, commit, push, and pull-request commands for the developer.

Distinguish planned, implemented, tested, measured, approved, experimental, deferred, and complete work.

File creation alone does not complete a phase.
