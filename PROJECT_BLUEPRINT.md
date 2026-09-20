# FinSight: An Evidence-Grounded Company Financial Filing Analyst Powered by Hybrid RAG

## 1. Background

### 1.1 Company Financial Filings

Companies publish annual reports, financial results, offer documents, DRHPs, and regulatory or exchange filings to disclose business performance, risks, financial position, governance information, and material events. These filings are primary evidence sources for financial research, due diligence, credit analysis, and regulatory review.

### 1.2 Information Contained in Financial Filings

A single filing may combine:

- narrative sections such as business descriptions, risk factors, management discussion, governance, litigation, and use of proceeds;
- primary financial statements such as the Balance Sheet, Statement of Profit and Loss, and Cash Flow Statement;
- detailed financial and operational tables;
- notes, footnotes, accounting policies, and auditor observations;
- comparative values across periods, reporting bases, currencies, and units;
- segment, geography, product, or business-line disclosures.

### 1.3 Current Filing-Review Process

Reviewers must manually navigate large documents, locate relevant sections, interpret table structures, preserve financial context, compare values across periods or issuers, and return repeatedly to the source to verify findings. The work becomes harder when values are restated, reclassified, reported at different scales, or spread across multiple documents.

### 1.4 Challenges of Financial Document Analysis

Financial filings are difficult for automated systems because they contain multi-column pages, nested and borderless tables, repeated headers, multi-page tables, footnotes, mixed narrative and numerical content, and issuer-specific presentation conventions. Correct interpretation requires more than text similarity.

### 1.5 Limitations of Generic RAG Systems

A generic PDF-to-vector-store pipeline may destroy hierarchy, separate numbers from row or column labels, retrieve a semantically similar but contextually wrong passage, mix fiscal periods or reporting bases, and allow a language model to generate unsupported numerical claims. FinSight addresses these risks with structure-aware ingestion, deterministic financial processing, hybrid retrieval, source-level citations, and post-generation validation.

## 2. Problem Statement

### 2.1 Manual Information Discovery

Important disclosures are distributed across lengthy filings. Locating the correct passage or table requires repeated manual search and cross-checking.

### 2.2 Complex Document Layouts and Tables

Plain-text extraction can scramble reading order, lose heading hierarchy, flatten tables, and disconnect values from headers, units, or footnotes.

### 2.3 Financial Context Ambiguity

A value is unusable without knowing the issuer, fiscal period, reporting basis, currency, presentation scale, metric concept, and provenance. Similar-looking values may represent different quantities.

### 2.4 Unsupported Numerical Answers

Language models can reproduce, transform, or invent numbers without a deterministic link to source evidence. Numeric correctness cannot be delegated to generation alone.

### 2.5 Multi-Document Comparison Challenges

Comparisons require compatible currencies, periods, reporting bases, concepts, and definitions. Missing values, nil markers, explicit zeroes, conflicts, and restatements must be distinguished.

### 2.6 Source Verification and Traceability Gap

An answer is not useful for financial review unless every released claim can be traced to permitted source evidence and the system can explain how the evidence was selected.

## 3. Proposed Solution

### 3.1 FinSight Overview

FinSight is a self-hosted, production-inspired financial filing analysis system. It ingests supported company filings, preserves their source structure, creates searchable retrieval representations, extracts a bounded set of structured financial facts, and answers natural-language questions with traceable evidence.

### 3.2 Full-Document Narrative RAG

All successfully extracted filing content remains searchable through Narrative RAG. This path supports questions about risks, business descriptions, management explanations, auditor observations, governance, litigation, use of proceeds, and other filing disclosures.

### 3.3 Structured Financial Fact Ledger

Selected financial-statement concepts are normalized into a Fact Ledger with explicit issuer, period, basis, currency, scale, value state, provenance, and source location. The ledger is the authoritative path for supported numeric lookup, calculation, and comparison.

### 3.4 Hybrid Retrieval and Reranking

FinSight combines lexical and dense retrieval under the same hard metadata scope, fuses ranked results using Reciprocal Rank Fusion, and reranks candidates with a local cross-encoder before evidence assembly.

### 3.5 Deterministic Financial Reasoning

Calculations use exact decimal arithmetic and formula-specific compatibility guards. The language model does not perform authoritative arithmetic or choose financial values.

### 3.6 Evidence-Grounded Answer Release

Generation uses evidence-bound placeholders. A post-generation Evidence Gate validates factual numerals, citations, qualitative evidence alignment, conflicts, and final answer decisions before release.

### 3.7 Combined Narrative and Numerical Analysis

A query may combine Fact Ledger values with narrative evidence. For example, FinSight can calculate a reported revenue change deterministically and retrieve management's explanation for that change from the filing.

## 4. Project Objectives

### 4.1 Primary Objective

Build a self-hosted system that reduces manual filing-review effort while improving contextual correctness, source traceability, and reproducibility.

### 4.2 Document Understanding Objectives

- Preserve document hierarchy, tables, pages, headings, and source locations.
- Handle PDF, XLSX, HTML, and XML through format-specific extraction paths.
- Record extraction gaps instead of silently presenting incomplete coverage.

### 4.3 Retrieval Objectives

- Support exact-term and semantic retrieval.
- Apply issuer, period, basis, document, generation, and security constraints as hard filters.
- Evaluate retrieval configurations rather than selecting them by intuition.

### 4.4 Financial Reasoning Objectives

- Normalize selected financial concepts.
- Preserve units, currencies, periods, bases, and value states.
- Support guarded deterministic calculations and bounded comparisons.

### 4.5 Evidence and Citation Objectives

- Link every released claim to permitted source evidence.
- Prevent citations to enriched retrieval text or generated summaries.
- Preserve complete source-to-answer lineage.

### 4.6 Experimentation Objectives

Evaluate parsers, chunking approaches, embedding models, sparse and dense retrieval alternatives, rerankers, context expansion, and local generation models through versioned configurations and shared metrics.

### 4.7 Evaluation and Reproducibility Objectives

Record corpus, golden-set, model, configuration, split, code revision, sample size, and run date for every publishable result. Keep development and held-out data separate.

### 4.8 Security and Reliability Objectives

Treat documents as untrusted, isolate parsing, validate uploads, restrict model capabilities, design dependency degradation, and support reconciliation and rebuild of derived state.

## 5. Project Scope

### 5.1 Supported Document Categories

- Annual reports
- DRHPs and offer documents
- Periodic financial results
- Regulatory and exchange filings
- Supporting company financial disclosures in approved formats

Earnings-call audio and transcript-specific speaker processing are outside Version 1.

### 5.2 Supported File Formats

- PDF: full narrative and selected structured financial extraction
- XLSX: sheet and cell extraction with applicable ledger mapping
- HTML: structural narrative extraction with source references
- XML: safe structured extraction; XBRL/iXBRL support is conditional on corpus validation

### 5.3 Full-Document Retrieval Scope

Every successfully extracted narrative region remains eligible for search unless blocked by security controls. The system does not reduce a filing to the Fact Ledger alone.

### 5.4 Financial Fact Ledger Scope

Version 1 targets a bounded catalogue of high-value concepts from primary statements and selected segment tables. The ledger prioritizes correctness and lineage over universal financial taxonomy coverage.

### 5.5 Supported Query Categories

- Narrative lookup
- Single financial fact lookup
- Multi-metric lookup within one context
- Deterministic calculation
- Cross-period comparison
- Cross-issuer comparison
- Reporting-basis comparison
- Clarification-required, refused, partial, and abstained outcomes

### 5.6 Supported Comparisons and Calculations

Supported calculations include period growth, margins, ratios, and CAGR where formula-specific guards pass. Comparisons are bounded by the approved matrix size and require compatible context.

### 5.7 Version 1 Boundaries

Version 1 is a single-node, low-concurrency, self-hosted project. It does not include universal table understanding, OCR-first processing, chart interpretation, multi-tenancy, document-level access control, multi-node scaling, or cloud-only dependencies.

## 6. Intended Users and Use Cases

### 6.1 Intended Users

- Financial research analysts
- Credit and risk analysts
- Due-diligence reviewers
- IPO and offer-document reviewers
- Audit and financial-review teams
- Students and researchers using public filings

### 6.2 Filing Research and Evidence Discovery

Locate specific disclosures, policies, risks, explanations, and source passages across long filings.

### 6.3 Financial Fact Lookup

Retrieve supported financial-statement values with period, basis, currency, unit, and cell-level provenance.

### 6.4 Period and Company Comparison

Build bounded comparison matrices across selected companies, fiscal periods, or reporting bases without silently mixing incompatible contexts.

### 6.5 Deterministic Financial Calculations

Calculate supported growth, ratios, margins, and CAGR from authoritative ledger facts.

### 6.6 Risk and Narrative Disclosure Analysis

Retrieve and summarize filing-grounded risk factors, management discussion, litigation descriptions, and auditor observations.

### 6.7 IPO and Offer-Document Review

Search business information, risk factors, use of proceeds, historical financial information, and restated disclosures in DRHPs and offer documents.

### 6.8 Source Verification

Inspect exact pages, spans, tables, and cells supporting an answer and review the recorded QueryTrace.

## 7. Product Boundaries and Non-Goals

### 7.1 Permanent Product Non-Goals

FinSight does not provide forecasts, price targets, investment recommendations, portfolio construction, valuation opinions, credit ratings, trade execution, real-time market-data services, or automated professional legal, regulatory, accounting, or audit conclusions.

### 7.2 Version 1 Non-Goals

- Universal financial concept extraction
- Full XBRL taxonomy normalization
- OCR-first scanned-document support
- Chart and figure interpretation
- Semantic answer caching
- Long-term conversational memory
- Multi-agent orchestration
- Model fine-tuning
- Multi-tenancy and enterprise identity
- High availability and autoscaling

### 7.3 Unsupported Financial Operations

The system refuses unsound operations such as cross-currency arithmetic without an approved rate source, incompatible-period calculations, and comparisons exceeding the supported matrix.

### 7.4 Unsupported Document Capabilities

Image-only pages, handwritten content, complex charts, unsupported encryption, corrupt files, and some highly irregular tables may produce recorded coverage gaps or controlled failure.

### 7.5 Sector-Specific Limitations

FinSight is cross-sector at common financial-statement and narrative-retrieval level. It does not deeply normalize all banking, insurance, telecom, energy, ESG, or other sector-specific concepts in Version 1.

### 7.6 Honest Product Claims

FinSight is production-inspired, not claimed to be commercially production-certified, hallucination-free, prompt-injection-proof, universally accurate, or a provider of investment advice.

## 8. Core Design Principles

### 8.1 Evidence Before Fluency

A shorter supported answer is preferable to a fluent unsupported answer.

### 8.2 Deterministic Numbers

Financial values and calculations originate from source-bound records and deterministic code, not free-form generation.

### 8.3 Mandatory Financial Context

Issuer, period, basis, currency, unit, concept, and provenance are required for authoritative financial facts.

### 8.4 Explicit Ambiguity Handling

The system asks for clarification when a user-resolvable ambiguity remains.

### 8.5 Disclosure of Conflicts and Degradation

Relevant source conflicts, partial coverage, and unavailable components are visible.

### 8.6 Correct Abstention and Refusal

Refusal represents a prohibited or unsound operation. Abstention represents insufficient or unresolved evidence.

### 8.7 Preservation of Original Evidence

Canonicalization and enrichment never overwrite the original source representation.

### 8.8 Structure Before Segmentation

Sections, headings, paragraphs, tables, rows, captions, and source boundaries are respected before token limits.

### 8.9 Simplest Sufficient Architecture

Complexity is admitted only when a measured failure demonstrates the need.

### 8.10 Experiment-Based Technique Selection

Parser, chunker, retriever, model, and parameter choices are selected through reproducible development-set evaluation.

### 8.11 Production-Inspired, Self-Hosted Design

All committed components are locally deployable and open-source or open-weight within documented licensing constraints.

## 9. Technology Stack

| Layer | Production candidate or role |
|---|---|
| Language | Python 3.12 |
| Backend | FastAPI, Uvicorn, Pydantic v2, Pydantic Settings |
| Frontend | Streamlit |
| Authoritative database | PostgreSQL with SQLAlchemy, Psycopg, and Alembic |
| Dense vector index | Qdrant |
| Object storage | S3-compatible object storage behind an object-store abstraction; SeaweedFS is the current local backend; portability risk documented |
| Model runtime | Host-native Ollama accessed through HTTPX |
| PDF fast path | PyMuPDF and pdfplumber |
| Layout-aware PDF candidate | Docling |
| Table fallback/comparator | Camelot |
| Spreadsheet | pandas and openpyxl |
| HTML | Beautiful Soup, lxml, and nh3 |
| XML | lxml and defusedxml |
| XBRL candidate | Arelle, conditional on real XBRL/iXBRL corpus |
| Chunking | FinSight-owned structure-aware pipeline with `langchain-text-splitters` utility only |
| Lexical retrieval | PostgreSQL full-text search |
| Dense retrieval | Qdrant |
| Fusion | Reciprocal Rank Fusion |
| Embedding candidates | Nomic Embed Text baseline; BGE-M3 challenger |
| Reranking candidates | MiniLM cross-encoder baseline; BGE reranker challenger |
| Generation | Small Ollama-compatible instruct model selected by evaluation |
| Authentication | PyJWT and Argon2 |
| Logging and metrics | Structlog and Prometheus client |
| Domain trace | QueryTrace |
| Report export | ReportLab |
| Testing and quality | Pytest, Hypothesis, Ruff, MyPy, pytest-cov, pip-audit |
| Evaluation plotting | Matplotlib |
| Deployment | Docker Desktop, WSL 2, Docker Compose; Ollama host-native |
| Background execution | PostgreSQL polling initially; Celery and Redis only after validation |
| CI | GitHub Actions, introduced incrementally |

### 9.1 Programming Language

Python 3.12 is used for ingestion, extraction adapters, domain logic, retrieval, evaluation, the API, and background work.

### 9.2 Backend API and Validation

FastAPI exposes the REST interface. Pydantic validates settings and application contracts. Generated OpenAPI becomes the machine-readable API schema.

### 9.3 Analyst Interface

Streamlit presents upload, query, comparison, evidence, status, and administrative views. It accesses application data only through the API.

### 9.4 Authoritative Relational Storage

PostgreSQL owns documents, processing jobs, source structure, chunks, facts, conflicts, queries, answers, traces, audits, and evaluation records.

### 9.5 Object Storage

The object store holds immutable originals and approved artefacts through an abstraction that permits later replacement. SeaweedFS is the current local backend, reached only through the S3 API so that AWS S3, Cloudflare R2, or another S3-compatible service can replace it by configuration. A filesystem adapter implements the same port as the documented alternative path. The deployment pins the chosen image, records its resolved digest, and documents its portability considerations. ADR-001 records the current selection and the evidence behind it.

### 9.6 Dense Vector Storage

Qdrant is the rebuildable vector index. It stores vectors and filter payloads, not authoritative financial state.

### 9.7 Runtime Lexical Search

PostgreSQL full-text search is the committed lexical path. It is not described as BM25. `rank-bm25` remains an offline evaluation baseline.

### 9.8 Local Model Runtime

Ollama serves generation and embedding models on the Windows host. Application containers access it through a configured local endpoint.

### 9.9 Document-Parsing Technologies

PyMuPDF and pdfplumber form the fast native-text path. Docling is evaluated for layout-aware extraction. Camelot remains a conditional table fallback or comparator.

### 9.10 Chunking Utilities

FinSight owns structure-aware chunking. LangChain is limited to `langchain-text-splitters` as a low-level utility.

### 9.11 Embedding Model Candidates

Nomic is the lightweight baseline. BGE-M3 is the higher-capability challenger. Heavy 4B to 8B embedding models are excluded from the current hardware target.

### 9.12 Reranking Model Candidates

MiniLM provides the lightweight baseline. A BGE reranker is evaluated as the stronger challenger. FlashRank is an optional latency experiment only if the baseline is too slow.

### 9.13 Authentication and Security Libraries

PyJWT supports short-lived bearer tokens. Argon2 provides one-way password hashing. File and parser controls are implemented directly at application boundaries.

### 9.14 Logging, Metrics, and Query Tracing

Structlog provides structured logs, Prometheus client provides metrics, and QueryTrace records the complete domain decision path. OpenTelemetry remains optional and experimental.

### 9.15 Testing and Code Quality

Pytest and Hypothesis cover deterministic and generated cases. Ruff, MyPy, coverage, and pip-audit provide quality and dependency checks.

### 9.16 Docker and Local Deployment

Docker Compose runs application and infrastructure services. Ollama remains host-native to avoid container accelerator and memory complexity.

The current target environment is a Windows laptop with Python 3.12, Docker Desktop using WSL 2, approximately 16 GB of system memory, and no assumed dedicated GPU.

CPU model, available cores, usable memory under the complete stack, available disk space, and accelerator availability are measured during the environment and model validation phase. Parser and model selection must fit the measured resource envelope.

### 9.17 GitHub and Continuous Integration

Feature branches and pull requests protect `main`. GitHub Actions grows from static checks and unit tests to available integration and regression checks.

### 9.18 Deferred and Experimental Technologies

The following require validation or evaluation before runtime admission:

- Celery and Redis as the background execution layer;
- Qdrant-native sparse retrieval;
- BGE-M3 learned sparse retrieval;
- Prompt Guard;
- generated table summaries;
- OpenTelemetry;
- narrative query expansion.

PostgreSQL polling is the initial background execution path and remains the fallback if Celery and Redis are not justified.

### 9.19 Explicitly Excluded Technologies

Hosted parsing services, Cohere reranking, full LangChain or LlamaIndex orchestration, Agentic RAG, GraphRAG, VLM-first parsing, 70B local models, vLLM, Kubernetes, service mesh, OPA, Presidio, NeMo Guardrails, MongoDB, Milvus, and audio transcript processing are excluded or deferred beyond Version 1.

### 9.20 Dependency Specification

The project uses one pinned `requirements.txt` containing the complete verified Python environment.

Dependency versions are frozen only after installation and compatibility checks succeed in a clean Python 3.12 virtual environment.

The file must remain installable at all times. Placeholder versions are prohibited. Dependency changes require `pip check`, `pip-audit`, relevant import checks, and clean-environment installation verification.

## 10. High-Level System Architecture

### 10.1 System Context

Users interact with the Streamlit client or API. The API coordinates authentication, query planning, retrieval, financial reasoning, generation, validation, and response assembly. Workers handle ingestion, indexing, reconciliation, and evaluation.

### 10.2 Main Application Components

- API
- Analyst UI
- Restricted parser worker
- Processing worker
- Validation and format-specific extraction adapters
- Normalization and Fact Ledger
- Chunking and indexing
- Query understanding and retrieval
- Financial analysis
- Generation and Evidence Gate
- Audit, observability, and evaluation

### 10.3 Infrastructure Components

PostgreSQL, Qdrant, the S3-compatible object store (currently SeaweedFS), and an optional validated execution-layer broker run as local infrastructure. Ollama runs on the host.

### 10.4 Host-Native Model Runtime

Embedding and generation requests use local HTTP calls. The model has no direct access to files, stores, networks, or tools.

Ollama serves the selected generation and compatible embedding models.

Cross-encoder reranking, BGE-M3 experiments, Prompt Guard experiments, and qualitative-alignment models run through local Python model adapters using the approved machine-learning libraries. They do not run through Ollama unless the selected model is explicitly supported and produces equivalent reproducible behavior.

Runtime reranking initially executes in the query-processing boundary. Experimental models execute through the evaluation harness and do not become runtime dependencies until selected.

### 10.5 Custom Python Orchestration Pipeline

FinSight directly owns query classification, hard filters, ledger-first lookup, retrieval, fusion, reranking, calculations, evidence assembly, generation contracts, and final decisions.

### 10.6 End-to-End Data Flow

```text
Upload
→ validate and preserve original
→ format-specific extraction
→ source representation
→ Fact Ledger and retrieval representations
→ lexical and dense indexing
→ query understanding and hard filters
→ ledger-first lookup and hybrid retrieval
→ RRF and reranking
→ evidence assembly and context expansion
→ deterministic reasoning
→ constrained generation
→ Evidence Gate
→ cited answer and QueryTrace
```

### 10.7 Authoritative and Derived State

PostgreSQL is authoritative. Qdrant is derived and rebuildable. The object store holds immutable source objects. No vector-store record can become financial truth.

### 10.8 Trust Boundaries

Trust boundaries exist at upload, parser execution, retrieval-to-prompt assembly, model output, telemetry, authorization, and source deletion.

### 10.9 Dependency Classification and Degradation

Essential, degradable, and optional dependencies have explicit behavior. PostgreSQL gates readiness; Qdrant can degrade to lexical retrieval; object-store loss disables source-object operations; generation loss enables deterministic fallback where possible.

## 11. Document Ingestion Architecture

### 11.1 Format-Specific Multi-Engine Ingestion

A file router selects the safe format-specific pipeline rather than forcing all formats through one parser.

### 11.2 File Routing

Routing uses detected content type and structural validation, not filename extension alone.

### 11.3 Upload and Initial Validation

Validation applies size limits, signature checks, declared-type agreement, allow-listing, and format-specific structural limits before expensive parsing.

### 11.4 Content-Based File-Type Detection

PureMagic is the initial local candidate. The component remains replaceable if the environment and model validation phase identifies unacceptable detection, compatibility, or maintenance limitations.

### 11.5 Document Identity and Duplicate Detection

The source-byte hash identifies a document version. Re-uploading identical bytes does not create duplicate facts, chunks, or vectors.

### 11.6 Immutable Source Preservation

Original bytes are stored before processing and are never modified. Sanitization applies only to derived representations.

### 11.7 Restricted Parser Execution

The intended deployment separates two worker responsibilities:

1. **Parser worker**
   - reads staged source objects;
   - performs file validation and format-specific extraction;
   - runs as a non-root process with bounded resources;
   - has restricted filesystem access and no Docker socket;
   - has no outbound internet access during parsing;
   - writes structured extraction results through an approved controlled boundary.

2. **Processing worker**
   - performs normalization, Fact Ledger construction, chunking, embedding, outbox processing, indexing, reconciliation, and evaluation work;
   - may access PostgreSQL, the object store, Qdrant, and configured local model endpoints as required.

During early local development, parser isolation may not yet be technically enforced. Only trusted development documents are processed until the restricted parser container exists, and this limitation is recorded.

Docling and other required parser-model artifacts are downloaded and verified before parser network isolation is enabled. Approved model artifacts are then supplied through a read-only local cache or mounted volume.

### 11.8 Processing Jobs and Background Execution

PostgreSQL owns processing-job identity, state, retry history, and results. Celery and Redis remain a candidate execution layer; PostgreSQL polling is the fallback.

Celery with Redis and PostgreSQL polling are implementations of one execution-layer interface.

The polling executor is implemented first to minimize early infrastructure complexity. Celery and Redis are evaluated only after one complete ingestion job works through the polling executor.

The Celery validation must demonstrate:

- PostgreSQL remains authoritative for job state;
- tasks carry only the job identifier;
- duplicate task delivery is harmless;
- worker interruption is resumable;
- broker loss does not lose jobs;
- stage timeout behavior is enforceable;
- additional memory and operational cost remain acceptable.

If the validation fails or adds unjustified complexity, PostgreSQL polling remains the Version 1 executor.

### 11.9 Persisted Checkpoints and Resumability

Source storage, extraction, normalization, chunking, outbox persistence, and index synchronization are checkpointed so interrupted work can resume.

### 11.10 Retry and Failure Handling

Only transient failures are retried. Validation and deterministic parsing failures reach controlled terminal states with recorded reasons.

### 11.11 Partial Document Processing

Page or table failures may produce partial readiness when remaining evidence is usable. Coverage gaps are recorded and disclosed.

### 11.12 Reprocessing and Shadow Generations

Reprocessing creates a shadow generation while the active valid generation remains queryable.

A generation is the unit of reprocessing. It spans extraction, normalization, chunking, and index synchronization for one document version, and it is the granularity at which retrieval filters evidence (§20.2). A generation is assembled from component runs — an extraction run, a chunking run, an indexing run — each recording its own producer, configuration version, and outcome. A component run completing does not make a generation active.

### 11.13 Atomic Generation Activation

The active pointer switches only after required indexing and validation succeed. Failed shadow runs never replace active evidence.

Component runs carry their own pointers identifying the current output of that stage. Those pointers record what exists; only generation activation determines what is queryable. The distinction matters because a stage may complete correctly while the generation it belongs to remains incomplete.

## 12. PDF Parsing and Layout Analysis

### 12.1 Challenges in Financial PDFs

Multi-column pages, borderless tables, repeated headers, wrapped labels, footnotes, and continued tables make reading order and structure critical.

### 12.2 Native-Text Fast Path

Clean native-text PDFs should use lightweight extraction when quality signals indicate reliable reading order and coverage.

### 12.3 PyMuPDF and pdfplumber Extraction

PyMuPDF provides page and block extraction. pdfplumber contributes layout and table signals. Both preserve page references and coordinates where available.

### 12.4 Docling Layout-Aware Candidate

Docling is evaluated as the layout-aware adapter for difficult pages and tables. Its output is mapped into FinSight’s source representation rather than becoming the source of architecture or business logic.

### 12.5 Reading-Order and Multi-Column Recovery

The parser must maintain block order and prevent text from separate columns from being interleaved incorrectly.

### 12.6 Table Detection and Bounding Regions

Tables remain explicit source objects with captions, bounding regions, cells, row and header paths, page spans, and extraction provenance.

### 12.7 Camelot Table-Extraction Fallback

Camelot lattice and stream modes remain conditional alternatives where available. Attempts and outcomes are recorded.

### 12.8 Continued and Multi-Page Tables

Continued tables must preserve or reconstruct header context without altering original evidence. Page-specific source cells remain addressable.

### 12.9 Parser Routing Signals

Routing may use native-text coverage, suspected multi-column layout, table density, reading-order quality, image-only pages, and parser confidence signals. Thresholds are established on development data.

### 12.10 Extraction Quality and Failure Signals

Quality signals are warnings and routing inputs, not proof of correctness. Ground-truth evaluation determines actual extraction performance.

### 12.11 OCR and Vision-Based Parsing Boundary

OCR and VLM-based parsing are not committed Version 1 defaults. They may be evaluated for bounded negative or scanned cases only after the text and layout paths are stable.

### 12.12 Parser Evaluation and Selection

PyMuPDF/pdfplumber, Docling, and relevant Camelot strategies are compared on structure, values, periods, units, lineage, latency, memory, and failure behavior.

## 13. Spreadsheet, HTML, XML, and XBRL Parsing

### 13.1 XLSX and Spreadsheet Challenges

Cell meaning depends on sheet, row label, column header, merged ranges, formulas, units, and period context.

### 13.2 pandas and openpyxl Processing

Openpyxl preserves workbook structure and cell references. pandas may support tabular transformations but is not the source of lineage.

### 13.3 Row, Column, Sheet, and Cell Context

Every extracted cell retains sheet, coordinate, row path, column path, value, and relevant annotations.

### 13.4 Spreadsheet Retrieval Representations

Sheets and tables may produce header-aware row records and deterministic summaries; authoritative source remains the workbook cell structure.

### 13.5 HTML Structural Parsing

HTML extraction preserves semantic headings, paragraphs, lists, tables, and DOM paths while excluding scripts and remote loading.

### 13.6 HTML Sanitization and Source Preservation

The original HTML bytes remain immutable. nh3 sanitizes only the derived representation used for display or retrieval.

### 13.7 Generic XML Parsing and Entity Protection

External entities, DTD loading, and unsafe expansion are disabled. Generic XML produces path-addressable narrative or structured reference content.

### 13.8 XBRL and iXBRL Detection

Real XBRL/iXBRL files are distinguished from generic XML before parser selection.

### 13.9 Arelle as an XBRL Candidate

Arelle is introduced only if the selected authoritative corpus contains relevant XBRL/iXBRL. It remains an adapter into FinSight’s context and provenance model.

### 13.10 Format-Specific Source Locations

PDF uses pages and regions, XLSX uses sheets and cells, HTML uses DOM elements, and XML/XBRL uses node or fact paths.

### 13.11 Format Coverage and Limitations

The formats have different supported depths. Results must not imply identical ledger coverage across all formats.

## 14. Information Representation Model

### 14.1 Source Representation

The source representation preserves extracted evidence with exact source coordinates and remains the only citable content.

### 14.2 Retrieval Representation

Retrieval representations add deterministic context useful for lexical and dense search while retaining links to source regions.

### 14.3 Canonical Fact Representation

Canonical facts store normalized financial values, concepts, context, provenance, states, and qualifiers.

### 14.4 Separation Between Representations

Source, retrieval, and canonical fact representations are independently stored and addressable. No enriched text is presented as original evidence.

### 14.5 Unified Structural Element Model

Format adapters emit a common set of page, section, block, table, cell, and span concepts without erasing format-specific source locations.

The records of this model are source elements. Where later sections refer to source regions, they mean one or more source elements.

### 14.6 Contextual Metadata Enrichment

Issuer, document type, heading path, page, period, basis, currency, scale, caption, and source location may be added deterministically to retrieval text.

### 14.7 Source-to-Retrieval Mapping

Every chunk identifies the source regions from which it was constructed.

### 14.8 Source-to-Fact Provenance

Every Fact resolves through provenance to a cell or span, extraction run, document version, and original object.

### 14.9 Citation Boundaries

Citations point to exact supporting source spans or cells, not broad enriched parents or generated summaries.

### 14.10 Representation Versioning

Parser, chunking, enrichment, embedding, and retrieval configuration versions are recorded for reproducibility and safe re-indexing.

## 15. Full-Document Narrative RAG

### 15.1 Narrative Content Coverage

Successfully extracted filing narratives remain searchable even when they contain no approved ledger concept.

### 15.2 Searchable Filing Sections

Business descriptions, risk factors, MD&A, governance, litigation, use of proceeds, audit observations, and notes are examples of supported narrative content.

### 15.3 Narrative and Qualitative Queries

Narrative answers use source passages and claim-level citations. Qualitative support is evaluated separately from numeric correctness.

### 15.4 Source-Bound Narrative Numbers

A number outside the Fact Ledger may be reported exactly when bound to a precise source span or cell. It must not receive unsupported normalization, calculation, or cross-document comparison.

### 15.5 Narrative Evidence Assembly

Retrieved and expanded regions are deduplicated, budgeted, and recorded. Interpretive context remains distinct from the exact supporting span.

### 15.6 Narrative Citation Requirements

Every factual narrative claim requires a valid citation to permitted source evidence for the current query.

### 15.7 Narrative Retrieval Limitations

Retrieval cannot recover content that failed extraction, is image-only without OCR, or was blocked for security. These gaps are disclosed.

## 16. Financial Fact Ledger

### 16.1 Purpose of the Fact Ledger

The ledger provides stronger guarantees for selected financial values than narrative retrieval alone.

### 16.2 Fact Ledger Coverage Boundary

Version 1 maps primary financial statements and selected segment tables rather than every table and every number.

### 16.3 Supported Financial Concepts

- Revenue from operations
- Total income
- Total expenses
- Profit before tax
- Profit after tax
- Total assets
- Total equity
- Total borrowings
- Cash from operating activities
- Segment revenue

### 16.4 Financial Context

Financial context includes issuer, fiscal period, reporting basis, currency, presentation unit, and default assurance and presentation statuses.

### 16.5 Fact Provenance

Provenance records document version, extraction run, source cell or span, and extraction method and version.

### 16.6 Financial Value States

Numeric, explicit zero, nil marker, missing, not applicable, and parse failure remain distinct.

### 16.7 Currency and Presentation Scale

Currency and scale are resolved from bounded source scope. Source-displayed and canonical values are both retained.

### 16.8 Fiscal Period and Period Nature

Periods preserve start, end, type, and whether a concept is instant or duration.

### 16.9 Consolidated and Standalone Reporting

Reporting basis is a hard context dimension and is not treated as a conflict.

### 16.10 Assurance and Source-Presentation Status

Audited, unaudited, reviewed, and unknown assurance remain separate from as-reported, restated, and unknown source-presentation status.

### 16.11 Segment-Revenue Qualification

Segment revenue requires a normalized segment-name qualifier.

### 16.12 Fact Conflict Detection

Facts with matching conflict keys and different canonical values after canonicalization form a conflict.

### 16.13 Conflict Classification and Materiality

Classification explains why values differ. Materiality determines whether the difference affects selection, calculation, or disclosure. Rounding differences remain recorded and are normally non-material.

### 16.14 Relationship Between Narrative RAG and the Fact Ledger

The ledger supplies authoritative supported values. Narrative RAG supplies explanations, surrounding context, and content outside the ledger.

## 17. Table Representation and Retrieval

### 17.1 Complete Structured Table Preservation

The full structured table remains preserved as a source object, regardless of how it is represented for retrieval.

### 17.2 Header and Row-Label Preservation

Cells and row groups retain caption, header path, row-label path, and footnote references.

### 17.3 Period, Basis, Currency, and Unit Context

Table-level and column-level context remains attached to values and retrieval text.

### 17.4 Deterministic Table Summary

A deterministic table summary lists source-derived information such as statement type, caption, periods, basis, units, and row labels. It contains no generated financial conclusion.

### 17.5 Header-Aware Row-Group Representations

Large tables generate manageable row-group retrieval units that repeat necessary header context.

### 17.6 Table Parent and Child Relationships

Row or cell retrieval units point to a broader table parent without losing exact source-cell references.

### 17.7 Table Retrieval and Source-Cell Citations

Retrieval may use a summary or row group, but released citations resolve to supporting cells or source table regions.

### 17.8 Large-Table Context Recovery

The evidence assembler retrieves the minimum structured context needed for interpretation instead of inserting every full table by default.

### 17.9 Generated Table Summaries as an Experimental Representation

LLM-generated search summaries may be evaluated only as non-authoritative, non-citable derived representations.

### 17.10 Prohibition on Generated Numerical Authority

No generated table description can establish a financial value, calculation, comparison, or conflict classification.

## 18. Structure-Aware Chunking

### 18.1 Structure Before Token Limits

Token limits apply within structural units rather than overriding source hierarchy.

### 18.2 Heading and Section Preservation

Heading paths are attached to chunks and used in contextual enrichment.

### 18.3 Paragraph and Sentence Boundaries

Narrative units split first at paragraph and sentence boundaries before fallback token splitting.

### 18.4 Table and Row-Group Boundaries

Narrative and table-derived content remain distinguishable. Chunks do not cross unrelated statement or table boundaries.

### 18.5 Parent and Child Chunks

Focused child units support retrieval precision; broader parents support interpretation.

### 18.6 Neighboring Source Regions

Approved neighboring chunks may be recovered when the retrieved unit lacks necessary context.

### 18.7 Contextual Metadata Enrichment

Only deterministic source-derived metadata is added.

### 18.8 Boilerplate Detection and Demotion

Page furniture may be excluded when detection is reliable. Repeated substantive content is demoted or deduplicated rather than universally removed.

### 18.9 Source Attribution Preservation

Each chunk retains explicit links to source regions and to the generation it belongs to.

### 18.10 Chunk Configuration Versioning

Every chunk records the configuration that created it.

### 18.11 Re-Chunking and Re-Indexing

Configuration changes create a shadow generation and activate atomically after successful indexing.

### 18.12 Baseline-Driven Chunk Parameters

Child size, parent size, overlap, context budget, and neighbor count are selected on development data.

## 19. Query Understanding and Routing

### 19.1 Deterministic-First Query Understanding

Issuer aliases, known periods, reporting basis terms, concept synonyms, and document types are resolved deterministically first.

### 19.2 Issuer Resolution

Issuer surface forms resolve through authoritative aliases. Multiple valid matches require clarification.

### 19.3 Fiscal Period Resolution

Explicit user periods are validated against available documents and facts. Missing periods with several candidates require clarification.

### 19.4 Reporting-Basis Resolution

Explicit basis terms are preserved. Missing basis where consolidated and standalone both exist requires clarification.

### 19.5 Financial Concept Resolution

Supported concepts resolve through curated synonyms and table context. Internal extraction ambiguity is not delegated to the user.

Issuer aliases and financial-concept synonyms are maintained as versioned, reviewable seed-data files in the repository. They are imported into PostgreSQL through an idempotent administrative command or migration-controlled seed step.

PostgreSQL is authoritative at runtime. The repository seed files provide reviewability, reproducibility, and controlled updates.

### 19.6 User-Resolvable Ambiguity

Only choices the user can meaningfully settle produce clarification.

### 19.7 Query Classes

- Fact lookup
- Multi-metric lookup
- Derived calculation
- Comparison
- Narrative lookup

### 19.8 Clarification Behavior

Clarification stops before retrieval and returns available choices with the applicable reason.

### 19.9 Deterministic Comparison Decomposition

Comparison requests decompose into explicit issuer-period-basis cells.

### 19.10 Query Planning

The plan selects ledger, retrieval, calculation, comparison, and presentation paths without agentic loops.

### 19.11 Experimental Narrative Query Expansion

Narrative-only expansion may be evaluated when baseline retrieval failures justify it.

### 19.12 Query Transform Restrictions

Transforms preserve the original query and all resolved hard filters and never apply to ledger-backed numeric or deterministic calculation queries.

### 19.13 Rejection of Unconstrained Agentic Routing

Control flow remains deterministic and traceable. An LLM does not autonomously choose tools, repeat searches, or alter financial scope.

## 20. Hybrid Retrieval Pipeline

### 20.1 Ledger-First Lookup

Supported fact and calculation queries consult the ledger before narrative retrieval.

### 20.2 Hard Metadata Filtering

Issuer, period, basis, document scope, active generation, security state, and authorization scope constrain both lexical and dense retrieval.

### 20.3 PostgreSQL Full-Text Lexical Retrieval

Persistent lexical retrieval captures exact concepts, names, years, labels, clauses, and codes.

### 20.4 Qdrant Dense Retrieval

Dense vectors support semantic matching over context-enriched retrieval representations.

### 20.5 Candidate Allocation by Evidence Type

Narrative and table-derived candidates use explicit allocation so one type cannot crowd out the other.

### 20.6 Reciprocal Rank Fusion

RRF combines ranked lists without assuming comparable raw scores.

### 20.7 Cross-Encoder Reranking

The cross-encoder scores query and candidate together. Inputs include permitted context tokens such as heading, period, and basis.

### 20.8 Context Expansion

Selected child chunks may recover a parent or bounded neighboring regions.

### 20.9 Evidence Deduplication

Overlapping source regions are collapsed while preserving strongest retrieval provenance.

### 20.10 Evidence Token Budget

Budgets prevent oversized prompts and reserve evidence for each requested comparison cell.

### 20.11 Per-Cell Comparison Evidence Allocation

Each comparison cell receives independent filters, ledger lookup, and evidence allocation.

### 20.12 Retrieval Degradation

Dense, lexical, and reranking failures have explicit fallback paths and degradation flags.

### 20.13 Retrieval Traceability

Candidate identifiers, ranks, fusion, reranker scores, expansions, and selected evidence are recorded in QueryTrace.

## 21. Alternative Hybrid Retrieval Evaluation

### 21.1 Purpose of the Architectural Comparison

Determine whether consolidating sparse and dense retrieval in Qdrant improves quality or operations enough to replace the independent PostgreSQL lexical path.

### 21.2 PostgreSQL FTS and Qdrant Dense Baseline

This remains the production candidate because it provides independent lexical degradation and keeps authoritative lexical records in PostgreSQL.

### 21.3 Qdrant-Native Sparse and Dense Retrieval

Evaluate Qdrant sparse vectors or BM25-style sparse retrieval under the same metadata scope.

### 21.4 Qdrant-Native Reciprocal Rank Fusion

Compare Qdrant-native fusion with application-level RRF for ranking reproducibility and traceability.

### 21.5 BGE-M3 Learned Sparse Retrieval

Evaluate learned sparse output only after BGE-M3 dense evaluation demonstrates acceptable resource use.

### 21.6 Exact-Term and Numerical Retrieval Tests

Use questions containing concept names, years, codes, clauses, and exact source numbers.

### 21.7 Availability and Degradation Trade-Offs

Consolidation removes the independent lexical fallback when Qdrant is unavailable. This operational consequence must be measured and documented.

### 21.8 Selection Criteria

Quality, latency, indexing cost, memory, filtering correctness, traceability, rebuild behavior, and degraded operation determine selection.

### 21.9 Runtime Admission Decision

The current baseline changes only after a reproducible result and explicit architecture decision.

## 22. Embedding Model Evaluation

### 22.1 Embedding Requirements

Models must run locally, fit the resource envelope, support relevant English financial terminology, and produce reproducible vectors.

### 22.2 Nomic Embed Text Baseline

Nomic is the lightweight initial candidate for early integration and baseline measurements.

### 22.3 BGE-M3 Challenger

BGE-M3 is evaluated for dense retrieval and may later enable learned sparse experiments.

### 22.4 Optional BGE Large English Challenger

A smaller English-only BGE model may be tested if BGE-M3 is too heavy and Nomic underperforms.

### 22.5 Excluded Heavyweight Embedding Models

Large 4B to 8B embedding models are excluded from the current laptop target.

### 22.6 Dense Retrieval Quality

Compare Recall@k, MRR, and nDCG under identical chunks and hard filters.

### 22.7 Long-Context Retrieval Behavior

Long input support is evaluated only where retrieval representations genuinely require it; large chunks are not preferred by default.

### 22.8 Latency and Memory Evaluation

Measure indexing throughput, query latency, peak memory, and coexistence with infrastructure and generation.

### 22.9 Vector Dimension and Storage Cost

Report Qdrant storage footprint and rebuild time.

### 22.10 Final Embedding Selection

Select the smallest model that provides acceptable retrieval quality and operational behavior.

## 23. Reranking Model Evaluation

### 23.1 Reranking Requirements

The reranker must improve ordering under the local latency and memory budget and remain optional under degradation.

### 23.2 MiniLM Cross-Encoder Baseline

MiniLM is the lightweight baseline for candidate reranking.

### 23.3 BGE Reranker Challenger

A BGE reranker is tested against the same candidate lists and query classes.

### 23.4 Optional FlashRank Evaluation

FlashRank is tested only if reranker latency prevents interactive use.

### 23.5 Candidate Depth and Final Evidence Count

Input depth and final evidence count remain baseline-driven.

### 23.6 Context-Augmented Reranker Input

The reranker sees deterministic heading, period, basis, and table context without receiving untrusted generated summaries as fact.

### 23.7 Latency and Memory Evaluation

Measure per-query reranking latency and peak resource use.

### 23.8 Reranking Failure and Degradation

Failure returns fused order plus a degradation flag.

### 23.9 Final Reranker Selection

Selection balances quality improvement against latency, memory, and failure complexity.

## 24. Local Generation Model Evaluation

### 24.1 Generation Model Requirements

The model must run through Ollama, fit the local resource envelope, follow typed output contracts, and operate without tools.

### 24.2 Ollama-Compatible Candidate Selection

Two or three small current instruct models are shortlisted during the environment and model validation phase based on:

- Ollama availability;
- open-weight licensing suitability;
- measured memory use;
- acceptable local latency;
- typed-output support;
- context-window suitability.

Exact model identifiers are not fixed in advance. The shortlist and final selection are recorded with model version or digest and evaluation evidence.

### 24.3 Structured-Output Compliance

Measure success in producing the expected typed intermediate response.

### 24.4 Typed-Placeholder Compliance

Measure correct use of fact, derived, period, and citation placeholders.

### 24.5 Raw Unsupported-Numeral Behavior

Record unsupported numerals before the Evidence Gate.

### 24.6 Qualitative Evidence Alignment

Human-labelled claims measure whether generated prose remains aligned to evidence.

### 24.7 Latency and Memory Usage

Measure response latency, model memory, and coexistence with embedding and reranking.

### 24.8 Answer Coverage

Report coverage beside safety improvements so abstaining on everything cannot appear successful.

### 24.9 Rejection of Heavyweight 70B Deployment

A 70B model and vLLM are not appropriate for the current single-laptop, low-concurrency environment.

### 24.10 Final Generation Model Selection

Select by structured compliance, evidence behavior, latency, memory, and answer coverage rather than leaderboard position alone.

## 25. Financial Reasoning and Calculations

### 25.1 Deterministic Calculation Boundary

Every calculation is a pure versioned function over authoritative inputs.

### 25.2 Exact Decimal Arithmetic

Financial arithmetic uses exact decimal values.

### 25.3 Supported Formula Categories

Period growth, margin, ratio, and CAGR are supported where inputs and guards permit.

### 25.4 Period Growth

Growth records both period values and handles sign transitions explicitly.

### 25.5 Margins and Ratios

Denominators must be present, resolved, non-nil, and non-zero.

### 25.6 Compound Annual Growth Rate

CAGR requires compatible, coterminous endpoints and a valid interval.

### 25.7 Currency Compatibility

Cross-currency magnitude operations are refused without an approved rate source.

### 25.8 Reporting-Basis Compatibility

Different bases are compared only when the user explicitly requests a basis comparison.

### 25.9 Period Compatibility

Duration values require compatible period lengths; instant values use reporting dates.

### 25.10 Nil, Missing, Zero, and Not-Applicable Handling

These states propagate separately and are never coerced into one null or zero behavior.

### 25.11 Sign-Transition Handling

A transition through zero or across signs is described explicitly rather than as a misleading percentage.

### 25.12 Derived Calculation Provenance

Each result records formula identifier, version, inputs, guards, and output.

### 25.13 Comparison Matrix Limits

The plan counts issuer, period, and basis combinations and refuses requests above the approved cell limit.

## 26. Answer Generation

### 26.1 Evidence-Restricted Prompt Construction

The prompt contains resolved context, an approved evidence set, identifiers, and strict output rules.

### 26.2 Untrusted Content Isolation

Document content is delimited and labelled as data, never as executable instruction.

### 26.3 Typed Internal Generation Contract

The model produces a structured intermediate representation containing claim text and evidence-bound placeholders.

### 26.4 Pydantic Validation

Pydantic validates the intermediate contract before substitution and Gate processing.

### 26.5 Numeric and Period Placeholders

Literal authoritative values and periods are supplied through bound placeholders.

### 26.6 Citation Placeholders

Citation placeholders reference evidence-set identifiers only.

### 26.7 Deterministic Placeholder Substitution

Code formats and substitutes values consistently.

### 26.8 Response Presentation Planning

The deterministic query plan selects paragraph, bullet, table, or mixed presentation.

### 26.9 Paragraph, Bullet, and Table Responses

Narrative questions use paragraphs or bullets. Comparison and financial results use deterministic tables where appropriate.

### 26.10 Generation-Unavailable Fallback

Fact, calculation, context, and citation records can be rendered deterministically without prose.

### 26.11 Rejection of Chain-of-Thought Storage

FinSight stores explicit plans, evidence, calculations, and decisions rather than model chain-of-thought.

## 27. Evidence Gate

### 27.1 Purpose of the Evidence Gate

The Gate separates untrusted model output from released answers.

### 27.2 Claim Decomposition

Generated output is segmented into atomic numeric, qualitative, and connective claims.

### 27.3 Unbound Factual Numeral Detection

Any factual numeral not produced through an approved placeholder is removed or causes a non-answer outcome.

### 27.4 Deterministic Numeric Verification

Numeric claims are checked against facts or derived calculations.

### 27.5 Qualitative Evidence Alignment

Similarity-based signals label qualitative claims as aligned, weak, or unsupported. The result is not called entailment or proof.

### 27.6 Citation Resolution

Every citation resolves to a permitted source region in the current evidence set.

### 27.7 Citation Context Validation

Issuer, period, basis, concept, and source context must match the claim.

### 27.8 Conflict Disclosure

Material or unresolved conflicts affecting released content are disclosed.

### 27.9 Degradation Disclosure

Unavailable retrieval, reranking, generation, or partial-document capabilities are reported separately from answer reasons.

### 27.10 Evidence-Support Bands

Evidence-support bands are rule-based, inspectable categories selected through evaluation. They are not confidence probabilities.

### 27.11 Answer Decisions and Reason Codes

Every answer receives exactly one decision and applicable reason codes.

### 27.12 Partial Answers, Abstention, and Refusal

Primary support determines answered versus abstained; removed secondary content may produce partial; prohibited operations produce refusal.

### 27.13 Prohibition on Unsupported Confidence Scores

No LLM or evaluation framework may emit a 0-to-1 faithfulness score and present it as calibrated correctness.

## 28. API and Analyst Interface

### 28.1 API Responsibilities

The API owns authentication, validation, query orchestration, results, administration, and health behavior.

### 28.2 Authentication and Authorization

All non-health routes require authentication. Version 1 defines two roles:

- **analyst:** upload permitted documents, inspect processing state, submit queries, respond to clarification, review evidence, and export answers;
- **administrator:** perform all analyst operations plus reprocessing, deletion, reconciliation, retention execution, evaluation execution, and user administration.

The first administrator is created through a one-time local bootstrap CLI command using environment-supplied credentials. Default or example credentials are prohibited.

Version 1 uses short-lived access tokens without refresh-token infrastructure. The Streamlit interface retains the access token only in its session state. Token expiry requires the user to authenticate again.

Authentication and authorization are enforced by the API, not by Streamlit.

### 28.3 Document Upload

Upload validates and stores one document, returns identity and status, and processes asynchronously.

### 28.4 Processing-State Retrieval

Users can inspect lifecycle state, coverage gaps, and controlled failures.

### 28.5 Query Submission

Queries may include explicit issuer, period, and basis to suppress unnecessary clarification.

### 28.6 Clarification Responses

Clarification returns machine-readable choices without answer content.

### 28.7 Answer and Evidence Responses

Responses include decision, reasons, degradation flags, context, citations, conflicts, and support band where applicable.

### 28.8 Deterministic Comparison Tables

Comparison tables are assembled from structured cells rather than authored freely by the model.

### 28.9 Administrative Operations

Reprocessing, deletion, reconciliation, retention, evaluation, and user management require administrative authorization.

### 28.10 Health and Dependency Endpoints

Liveness, readiness, and dependency-detail surfaces remain distinct.

### 28.11 Generated OpenAPI

Generated OpenAPI owns request and response shapes. This blueprint owns behavior, not exact payload schemas.

### 28.12 Streamlit Analyst Interface

The UI supports upload, query, clarification, comparisons, evidence inspection, and administrative views without direct data-store credentials.

### 28.13 Evidence and QueryTrace Views

Users can inspect exact source context and the retrieval and validation path where authorized.

### 28.14 PDF Report Export

An authenticated user may export an existing validated answer as a PDF containing:

- the original question;
- the released answer;
- deterministic comparison tables where applicable;
- reporting context;
- validated citations;
- disclosed conflicts;
- degradation warnings;
- answer decision and reason codes;
- evidence-support band where applicable.

The PDF is generated from the persisted validated answer and does not invoke the generation model again. ReportLab is the selected PDF-rendering library.

### 28.15 Rate Limiting

Version 1 applies bounded per-process rate limiting to authentication and query endpoints to protect the API and local generation runtime.

Rate limiting is enforced at the API boundary. A shared rate-limit store is introduced only if the validated deployment uses multiple API processes.

Rate-limiter failure must not silently block valid evidence access. The operational degradation is recorded, while authentication and authorization remain enforced.

## 29. Data Storage and Consistency

### 29.1 PostgreSQL Authority

All authoritative business state is stored transactionally in PostgreSQL.

### 29.2 Qdrant as a Derived Index

Qdrant stores vectors and filter payloads that can be rebuilt from PostgreSQL.

### 29.3 Object-Storage Abstraction

Application code uses an object-store interface so the backend can be replaced without changing domain logic. The interface, not the backend, is the architectural boundary: adapters are named for the protocol they speak rather than the vendor they address, and no module outside the adapter imports a storage SDK. ADR-001 records the occasion this was tested — a backend replacement that touched configuration and one adapter flag while the port, the key scheme, the domain model, and every unit test stayed unchanged.

### 29.4 Object-Store Deployment and Portability Risk

The chosen object-store deployment is documented and pinned by tag, with its resolved digest recorded. The project avoids proprietary coupling and preserves a filesystem or compatible S3 alternative path. This risk has materialised once: the originally selected backend was archived upstream and its container images were withdrawn, and the recovery cost stayed within configuration because the abstraction held. Backend selection is revisited when upstream maintenance or image publication stops.

### 29.5 Immutable Source Storage

Original objects are content-addressed and never overwritten.

### 29.6 Database Migration Strategy

Alembic migrations are versioned, reviewed, and validated before readiness.

### 29.7 Transaction Boundaries

Transactions remain bounded and exclude long parser, model, object-store, broker, and vector calls.

### 29.8 Transactional Outbox

Chunk and index-event records commit together.

### 29.9 Deterministic Vector Identifiers

Identifiers incorporate chunk and relevant configuration identity to make replay idempotent.

### 29.10 Index Synchronization

An indexer consumes outbox events and records completion.

### 29.11 Reconciliation

Administrator-triggered reconciliation detects and repairs missing or orphaned Qdrant and object state.

### 29.12 Deletion and Tombstoning

Deletion removes active retrieval and objects according to policy while preserving tombstoned history required for audit and historical citations.

### 29.13 Retention

Query, answer, source-object, generated-report, and audit retention policies are independently configurable.

Version 1 provides an audited administrator-triggered retention operation. Scheduled retention enforcement is deferred hardening.

Audit retention remains independent of document deletion. A document deletion must not silently remove required security or administrative audit history.

### 29.14 Backup, Restore, and Index Rebuild

Backups protect authoritative PostgreSQL data and immutable source objects. Qdrant is not authoritative and is rebuilt from PostgreSQL after restoration.

Recovery validation restores the authoritative stores into a controlled environment, rebuilds the derived index, and runs the approved smoke checks before the recovery is considered successful.

## 30. Security Architecture

### 30.1 Untrusted Document Model

Every uploaded file and extracted instruction-like string is untrusted.

### 30.2 Restricted Parser Worker

Parsing occurs in a constrained worker boundary.

### 30.3 Non-Root and Least-Privilege Execution

Parser and application containers run without root and unnecessary capabilities where practical.

### 30.4 Parser Network Isolation

Document parsing does not require outbound internet access.

### 30.5 File-Type Spoofing Protection

Detected signature and declared type must agree.

### 30.6 XML Entity and Expansion Protection

DTD loading, external entities, and unsafe expansion are disabled.

### 30.7 Archive and Workbook Protection

Macro-enabled and suspicious archive structures are rejected under bounded rules.

### 30.8 HTML Sanitization

Scripts, active content, and remote-resource loading are excluded from derived content.

### 30.9 Unsafe Filename Handling

Filenames remain metadata; object paths derive from safe internal identifiers or hashes.

### 30.10 Parser Resource Limits and Timeouts

CPU, memory, temporary storage, and elapsed time are bounded.

### 30.11 Prompt-Injection Structural Controls

Model isolation, hard evidence scope, typed placeholders, and the Evidence Gate provide the primary controls.

### 30.12 Injection Classification

Rule-based classification marks clear, suspicious, and blocked regions and propagates warnings.

### 30.13 Prompt Guard as an Evaluation Candidate

Prompt Guard may be compared with the rule-based classifier for held-out detection, false positives, latency, memory, and license suitability.

### 30.14 Model Isolation

The model can only transform a supplied prompt into text.

### 30.15 Authentication and Role Enforcement

Authorization occurs at the API boundary, not in the UI.

### 30.16 Secret Management

Secrets are environment-supplied, never committed, and rejected when known default values are detected.

### 30.17 Telemetry Privacy

Logs and metrics exclude document content, questions, values, credentials, and high-cardinality identifiers.

### 30.18 Authorized Deletion

Deletion is an audited administrative action and converges across authoritative and derived stores.

### 30.19 Deferred Fine-Grained Authorization

OPA and document-level ACLs are deferred until multi-tenant or restricted private-document scope exists.

### 30.20 Residual Security Limitations

Detection is heuristic, test suites are bounded, and no immunity or formal compliance claim is made.

## 31. Reliability and Observability

### 31.1 Dependency Health Model

Every dependency has a health state and essential, degradable, or optional classification.

### 31.2 Liveness and Readiness

Liveness checks the process. Readiness checks essential dependencies and schema compatibility.

### 31.3 Full and Degraded Operating Modes

Degradable loss keeps sound capabilities available and records the reduced mode.

### 31.4 Component Failure Behavior

Parser, retriever, reranker, model, object store, index, broker, and limiter failures each have explicit behavior.

### 31.5 Processing Resumability

Persisted checkpoints allow interrupted jobs to continue safely.

### 31.6 Idempotency

Duplicate upload, task delivery, outbox replay, reprocessing, deletion, and rebuild are safe.

### 31.7 Structured Logging

Logs use correlation identifiers, stages, durations, and controlled diagnostics.

### 31.8 Metrics

The API, parser worker, and processing worker expose Prometheus-format metrics covering latency, failures, queue and outbox state, answer decisions, degradations, indexing, resource use, and evidence validation.

A Prometheus server is an optional Docker Compose observability profile used for local inspection and demonstrations. The metrics endpoints remain useful without a continuously running Prometheus server.

Prometheus server availability is optional and never affects readiness or operating mode.

### 31.9 QueryTrace

QueryTrace persists resolved slots, query class, filters, candidates, fusion, reranking, expansions, facts, calculations, conflicts, Gate verdicts, citations, decisions, and configuration versions.

### 31.10 Optional Distributed Tracing

OpenTelemetry remains optional and must not duplicate domain trace or affect readiness.

### 31.11 Administrator-Triggered Reconciliation

Reconciliation is mandatory as an administrative operation; scheduling is optional hardening.

### 31.12 Recovery Validation

Restore and index rebuild are exercised and measured before any verified-recovery claim is made.

## 32. Dataset Strategy

### 32.1 Minimum Development Corpus

Before the first retrieval slice, use two authoritative PDFs with different layouts, one deterministic synthetic financial fixture, and one bounded negative fixture.

### 32.2 Real-Document Corpus

The committed corpus expands incrementally to approved annual reports, offer documents, periodic results, and format supplements.

### 32.3 Authoritative Source Selection

The initial corpus is India-first because DRHPs, SEBI-linked public issues, and Indian company filings are central use cases.

Use official issuer, stock-exchange, regulator, or offer-document repositories. Limited non-Indian company filings may be added to test currency, fiscal-year, and layout diversity, but results must be reported separately where regulatory structures differ.

### 32.4 Document and Layout Diversity

Select different statement layouts, issuers, periods, currencies, fiscal year-ends, reporting bases, and at least one domain-shift issuer.

### 32.5 Format Coverage

PDF forms the core. XLSX, HTML, and XML form separately reported supplements.

### 32.6 Corpus Manifest

The manifest records document identity, source, issuer, type, period, format, publication and retrieval dates, checksums, redistribution status, split, and rationale.

### 32.7 Checksums and Reproducibility

Byte checksums establish corpus identity. A changed download is a new candidate version.

### 32.8 Licensing and Redistribution

Public availability is not redistribution permission. Source files remain outside Git unless permission is explicit.

### 32.9 Development and Held-Out Assignment

Assign documents to splits before authoring questions against them.

### 32.10 Negative Documents

Scanned, malformed, encrypted, or unsupported examples verify controlled failure and are not counted as normal extraction accuracy.

### 32.11 Incremental Corpus Expansion

Dataset preparation grows alongside implementation rather than preceding the entire codebase.

## 33. Synthetic and Adversarial Fixtures

### 33.1 Purpose of Synthetic Fixtures

Synthetic data provides exact, reproducible coverage of edge cases unlikely to appear reliably in a small real corpus.

### 33.2 Financial Value Traps

Zero, nil, missing, not applicable, negatives, Unicode signs, grouping, footnotes, percentages, and wrapped values.

### 33.3 Currency and Unit Traps

Scope inheritance, overrides, consistent declarations, ambiguity, conflict, differing scales, and cross-currency refusal.

### 33.4 Fiscal Period and Reporting-Basis Traps

Multiple periods, instant versus duration, non-coterminous periods, consolidated, standalone, and undetermined basis.

### 33.5 Restatement and Conflict Traps

Explicit restatement, reclassification, rounding difference, extraction discrepancy, unresolved conflict, and materiality.

### 33.6 Table and Layout Traps

Borderless tables, merged headers, continued tables, repeated headers, empty cells, notes, and parser-specific failures.

### 33.7 Retrieval Traps

Boilerplate collisions, abbreviations, parent-context dependence, dense-only wins, lexical-only wins, type crowding, and multi-cell comparisons.

### 33.8 Clarification and Non-Answer Fixtures

Ambiguous issuer, period, or basis; missing evidence; incompatible operations; comparison limits.

### 33.9 Security and Malformed Fixtures

Unsafe XML, archive ratios, macros, active HTML, remote references, path traversal, injections, encryption, truncation, and corruption.

### 33.10 Incremental Fixture Development

Fixtures are written test-first or alongside the code path they exercise. The entire catalogue is not generated in advance.

### 33.11 Synthetic Data Labeling

Synthetic results are always reported separately from real-filing results.

## 34. Golden Set and Evaluation Splits

### 34.1 Golden-Set Purpose

The golden set defines expected decisions, facts, calculations, evidence, citations, conflicts, and non-answer behavior.

### 34.2 Question Categories

Questions cover the approved query classes, contexts, calculations, conflicts, retrieval challenges, and controlled non-answers.

### 34.3 Annotation Requirements

Each item records context, expected decision and reasons, facts or source evidence, permitted citations, calculation expectations, split, and verification.

### 34.4 Source Verification

A human verifies every expected value and source reference.

### 34.5 Development Split

Development data supports configuration and threshold selection.

### 34.6 Held-Out PDF Core

The PDF core measures final primary-format performance and is never used for tuning.

### 34.7 Held-Out Format Supplement

XLSX, HTML, and XML results are reported separately from PDF.

### 34.8 Security Development and Held-Out Partitions

Detection rules use a development partition; published security performance uses frozen held-out fixtures.

### 34.9 Split Assignment Before Question Authoring

No question is authored against a document whose split remains undecided.

### 34.10 Freeze and Contamination Rules

Held-out labels and documents are frozen at declared points. Corrections trigger documented contamination handling.

### 34.11 Annotation Quality and Self-Consistency

A delayed blind second pass over a sample measures single-annotator consistency.

## 35. Experiment-Driven Configuration Selection

### 35.1 Experiment Principles

Each experiment names the baseline, failure, changed factor, metrics, cost, data split, and runtime admission rule.

### 35.2 Production Candidate Baseline

The current candidate is hard-filtered PostgreSQL lexical retrieval plus Qdrant dense retrieval, application RRF, cross-encoder reranking, and bounded context expansion.

### 35.3 Parser Comparison

Compare lightweight and layout-aware PDF paths against manually verified tables and pages.

### 35.4 Chunking Configuration Comparison

Compare structure-aware baseline, recursive control, overlap, parent-child retrieval, and contextual enrichment.

### 35.5 Preprocessing Variant Comparison

Evaluate only corpus-dependent variants such as hyphenation repair, paragraph reconstruction, heading attachment, enrichment, boilerplate handling, and deduplication.

### 35.6 Lexical-Only and Dense-Only Ablations

These quantify each retriever’s contribution and verify degradation paths.

### 35.7 Hard-Filter Ablation

An intentionally unsafe unfiltered run shows the contribution of financial metadata scope.

### 35.8 Fusion Evaluation

RRF is the production candidate; its constant is selected on development data.

### 35.9 Reranking Evaluation

Measure ranking gain against latency and memory.

### 35.10 Contextual Enrichment Evaluation

Compare enriched and unenriched chunks under identical retrieval settings.

### 35.11 Parent-Context Evaluation

Measure whether expansion adds required attribution or structural context without excessive noise.

### 35.12 Boilerplate-Handling Evaluation

Ensure page furniture is reduced without making substantive evidence unreachable.

### 35.13 Exact and Approximate Vector Search

Compare recall, latency, and resources before selecting Qdrant search policy.

### 35.14 Embedding Model Selection

Compare Nomic and BGE candidates under identical representations and queries.

### 35.15 Generation Model Selection

Compare local instruct models for contract compliance, unsupported numerals, qualitative alignment, coverage, latency, and memory.

### 35.16 Security Classifier Evaluation

Compare deterministic rules and optional Prompt Guard on held-out attacks and clean filing content.

### 35.17 Experimental Query Transformation

Narrative-only transformations are evaluated after baseline failure analysis and remain disabled by default.

### 35.18 Generated Table-Summary Experiment

Generated summaries are compared with deterministic summaries only as non-authoritative retrieval aids.

### 35.19 Negative Result Preservation

Experiments that fail remain recorded so rejected complexity is not reintroduced without evidence.

### 35.20 Runtime Admission Criteria

A technique enters runtime only after reproducible benefit, acceptable cost, preserved evidence integrity, and explicit approval.

## 36. Evaluation Methodology

The evaluation harness is a dedicated command-line application that can also be dispatched through an administrator-controlled background job after the execution layer is available.

PostgreSQL stores evaluation-run identity, configuration, provenance, status, and compact metric records. Versioned JSON, CSV, Markdown, and plot artifacts are written to the approved artifact store. Measured results are never embedded directly into the project blueprint.

### 36.1 Parsing and Extraction Metrics

Page coverage, table detection, cell accuracy, headers, row labels, period alignment, value parsing, unit resolution, basis resolution, concept mapping, conflicts, materiality, provenance, and failures.

### 36.2 Table-Extraction Metrics

Table completeness, numeric cells, headers, units, lineage, strategy wins, processing time, and failures.

### 36.3 Retrieval Metrics

Recall@k, MRR, nDCG, evidence-region precision, per-cell evidence recall, context usefulness, duplication, tokens, latency, slots, and query-class accuracy.

### 36.4 Financial Fact Accuracy

Exact match under declared tolerance, context accuracy, canonicalization, and error-cause taxonomy.

### 36.5 Calculation and Guard Accuracy

Formula reproducibility and correct refusal or abstention when guards fail.

### 36.6 Citation Accuracy

Citation validity, recall against permitted evidence, source resolution, and context match.

### 36.7 Answer-Decision Accuracy

Correct answer decision and reason codes.

### 36.8 Raw and Released Unsupported Numerals

Measure model behavior before and after the Gate, with coverage reported alongside.

### 36.9 Answer Coverage and False Abstention

Report supported coverage, correct abstention, and false abstention together.

### 36.10 Security Measurements

Injection attack success, clean false positives, detection precision and recall, authorization cases, malformed handling, and guard coverage.

### 36.11 Performance and Resource Measurements

Ingestion, query, stages, generation, embeddings, reranking, memory, rebuild, recovery, and outbox convergence.

### 36.12 Baseline-Driven Parameter Selection

Pending quality and retrieval parameters are selected through named development-set procedures. Plateau-based choices are preferred over unstable peaks.

Security and resource limits, such as upload size, structural bounds, parser timeouts, archive limits, evidence budgets, and comparison limits, are initially proposed from safe operational constraints and verified through bounded tests. They are not optimized against held-out data.

Exact values must not be invented silently. Every selected value records its purpose, evidence, validation method, fallback, and configuration version.

### 36.13 Regression Gates

Immutable invariants enforce immediately. Threshold-bearing gates remain informational until baselines establish thresholds.

### 36.14 Result Reproducibility

Each run records corpus, golden set, model versions, configuration, split, code revision, sample size, and date.

### 36.15 Permitted Project Claims

Only claims with predeclared evidence paths and recorded results may be published.

### 36.16 Evaluation Harness and Result Storage

The evaluation harness is a dedicated command-line application. After the background execution layer exists, the same harness may also be dispatched through an administrator-controlled background job.

PostgreSQL stores evaluation-run identity, configuration, provenance, status, compact metric records, and links to artifacts.

Versioned JSON, CSV, Markdown, and plot artifacts are written to the approved artifact store. Large traces and intermediate files remain outside source control.

Measured results are never written into this project blueprint.

## 37. Testing Strategy

### 37.1 Unit Testing

Deterministic domain behavior, context resolution, calculations, decisions, and identifiers.

### 37.2 Property-Based Testing

Numeric grammar, scales, canonicalization, and formula invariants.

### 37.3 Contract Testing

API behavior, authentication, decisions, codes, idempotency, and generated schema compatibility.

### 37.4 Integration Testing

Real PostgreSQL, Qdrant, object storage, and selected model or stub boundaries.

### 37.5 Parser Comparison Testing

Ground-truth tables and pages exercise every candidate parser configuration.

### 37.6 Evidence Gate Mutation Testing

Deliberately corrupt values, citations, contexts, offsets, and generated numerals.

### 37.7 Security Fixture Testing

Bounded malformed and adversarial test files assert guard activation and controlled failure.

### 37.8 Dependency Failure Testing

Stop degradable components and assert mode, flags, and fallback output.

### 37.9 Idempotency and Resume Testing

Duplicate ingestion, task delivery, index replay, worker interruption, deletion, and rebuild remain safe.

### 37.10 Retrieval Evaluation Testing

The same harness executes every retrieval configuration and ablation.

### 37.11 Test Data Separation

Development, held-out, synthetic, security, and restricted source artifacts remain separated.

## 38. Docker Deployment Architecture

### 38.1 Windows and WSL 2 Environment

Docker Desktop uses the WSL 2 Linux-container backend and works from the VS Code Command Prompt terminal.

### 38.2 Docker Compose Topology

The final local topology includes the API, restricted parser worker, processing worker, Streamlit UI, PostgreSQL, Qdrant, the S3-compatible object store, and an optional validated Redis broker. Ollama remains on the Windows host.

The Redis broker is included only if the Celery validation succeeds. PostgreSQL polling remains the fallback and requires no broker.

### 38.3 Containerized Infrastructure

PostgreSQL, Qdrant, and the object store are introduced before application containerization.

### 38.4 Restricted Parser Worker

The worker uses least privilege, resource limits, bounded temporary storage, and no parse-time outbound network.

### 38.5 Host-Native Ollama

Containers communicate with the configured host endpoint.

### 38.6 Local Application Development

Early Python services run locally against containerized infrastructure for easier debugging.

### 38.7 Application Containerization

API, restricted parser worker, processing worker, and UI are containerized after their local paths work.

### 38.8 Container Networking

Only required service ports and networks are exposed.

### 38.9 Persistent Volumes

PostgreSQL, Qdrant, and object-store data use named persistent volumes.

### 38.10 Health Checks and Start-Up Ordering

Services wait for health and schema readiness rather than mere process start.

### 38.11 Resource Constraints

Limits prevent parser or infrastructure workloads from starving local models.

### 38.12 Final Single-Node Deployment

The system remains reproducible through Docker Compose and documented host prerequisites.

## 39. Continuous Integration and Delivery

### 39.1 Branch and Pull-Request Workflow

Implementation occurs on focused branches and merges through reviewed pull requests.

### 39.2 Initial GitHub Actions Checks

Dependency installation, Ruff, MyPy, unit tests, coverage, and dependency audit are introduced when their configurations exist.

### 39.3 Test and Quality-Gate Expansion

Contract, fixture, invariant, regression, integration, and container checks are added with the corresponding code.

### 39.4 Development-Split Regression Checks

Development regression runs use cached extraction and controlled model outputs where appropriate.

### 39.5 Docker Image Build Validation

Image builds and Compose configuration are validated before release preparation.

### 39.6 Model-Dependent Manual Evaluation

Large local model runs remain manual or explicitly scheduled.

### 39.7 Held-Out Release Evaluation

Held-out evaluation runs only at deliberate tagged revisions and never in routine CI.

### 39.8 Continuous Delivery Boundary

CD does not mean automatic production deployment. It may package validated images and release artifacts.

### 39.9 Final Release Workflow

A final tag requires clean tests, image builds, approved evaluation, verified setup instructions, and no restricted corpus data or secrets.

## 40. Deferred and Rejected Alternatives

### 40.1 Hosted Parsing Services

LlamaParse, Azure Document Intelligence, Google Document AI, and Adobe extraction APIs violate the fully self-hosted requirement.

### 40.2 LlamaIndex and LangChain Orchestration

They would hide domain routing, filtering, and audit behavior that FinSight intentionally demonstrates.

### 40.3 Agentic RAG

Unconstrained agent loops reduce determinism, security, traceability, and evaluation clarity.

### 40.4 GraphRAG

The main challenge is evidence and financial context, not massive relationship traversal.

### 40.5 Vision-First Parsing

VLM-first parsing is too resource-heavy and non-deterministic for the Version 1 default.

### 40.6 ColPali and Visual Retrieval

Visual late-interaction retrieval requires new representations, hardware, storage, citations, and evaluation.

### 40.7 Heavyweight Embedding Models

Large embedding models cannot coexist reliably within the current laptop resource envelope.

### 40.8 70B Generation Models and vLLM

They target hardware and throughput beyond the local low-concurrency deployment.

### 40.9 Cohere and Other Hosted Rerankers

Hosted rerankers violate the open-source, self-hosted condition.

### 40.10 OPA and Fine-Grained Authorization

Two-role Version 1 authorization does not justify a separate policy engine.

### 40.11 Presidio and Private-Document Redaction

Public filing scope does not justify automatic PII redaction that could remove legitimate evidence.

### 40.12 NeMo Guardrails

FinSight uses domain-specific structural controls and the Evidence Gate rather than a second generic guardrail framework.

### 40.13 Kubernetes and Service Mesh

Single-node deployment does not require Kubernetes, Istio, Linkerd, or mTLS service-mesh complexity.

### 40.14 MongoDB, Milvus, and pgvector

These alternatives add duplicate storage or replace an already justified PostgreSQL and Qdrant boundary without measured need.

### 40.15 Earnings-Call Audio and Speaker Diarization

Audio transcription and speaker attribution are outside the committed document scope.

### 40.16 RAGAS-Based Confidence Scores

LLM-judge scores are not calibrated correctness probabilities and cannot replace deterministic validation.

## 41. Known Risks and Limitations

### 41.1 Limited Fact Ledger Coverage

Only the approved concept catalogue receives full structured guarantees.

### 41.2 Sector-Specific Concepts

Specialized banking, insurance, telecom, energy, and ESG measures may remain narrative content.

### 41.3 Parser and Table Variability

Complex issuer layouts may reduce extraction coverage or require fallback processing.

### 41.4 Scanned Documents and OCR

Image-only documents are negative or roadmap cases unless a bounded OCR extension is approved.

### 41.5 Charts and Figures

Chart interpretation is outside Version 1.

### 41.6 Qualitative Alignment Limitations

Similarity-based alignment can miss contradiction despite vocabulary overlap.

### 41.7 Prompt-Injection Detection Limitations

Detection can produce false positives and false negatives and is reported through measured outcomes.

### 41.8 Small Evaluation Corpus

Intervals may be wide and per-class results may be coverage-only.

### 41.9 Single-Developer Annotation

A delayed blind self-consistency check reduces but does not eliminate annotation risk.

### 41.10 Local Model Quality and Performance

Small models may provide weaker prose or structured compliance than hosted frontier models.

### 41.11 Docling and Reranker Resource Usage

Layout and reranking models may compete with generation and infrastructure for memory.

### 41.12 Object-Store Ecosystem and Portability Risk

Object storage is abstracted and the selected deployment is documented to reduce coupling. This risk is realised rather than theoretical: the originally selected backend entered maintenance mode, stopped publishing container images, and was archived, which forced a replacement during Phase 3. The abstraction contained the cost. Self-hosted object storage remains a component whose upstream viability must be monitored rather than assumed.

### 41.13 Single-Node Deployment

No high availability or multi-node failover is provided.

### 41.14 Administrator-Triggered Reconciliation

Reconciliation is available but not necessarily scheduled in the committed baseline.

## 42. Expected Outcomes

### 42.1 Functional Outcome

A user can ingest supported filings, ask natural-language questions, compare supported facts, inspect evidence, and receive controlled answer outcomes.

### 42.2 Parsing and Layout Outcome

The project demonstrates measured format-specific and layout-aware extraction rather than assuming one parser succeeds everywhere.

### 42.3 Retrieval Evaluation Outcome

The project reports the contribution of hard filters, lexical and dense retrieval, fusion, reranking, chunking, and context expansion.

### 42.4 Financial Correctness Outcome

Supported financial values and calculations preserve context, value state, compatibility, conflicts, and provenance.

### 42.5 Evidence and Citation Outcome

Released claims resolve to source evidence, unsupported factual numerals are blocked, and QueryTrace explains the decision path.

### 42.6 Security and Reliability Outcome

The system treats documents as untrusted, designs parser isolation and degradation, and validates idempotency, reconciliation, and recovery behavior.

### 42.7 Reproducibility Outcome

Every publishable result identifies its corpus, models, configuration, split, code revision, sample size, and date.

### 42.8 Portfolio and Learning Outcome

FinSight demonstrates end-to-end system design, financial-domain modeling, RAG evaluation, deterministic reasoning, security, Docker deployment, CI, and human-reviewed production-style development.
