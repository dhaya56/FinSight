# ENV-004 — Extraction and Source-Representation Validation

- **Status:** recorded
- **Date:** 2026-09-21
- **Phase:** 4 — extraction and the source representation
- **Method:** dependencies installed and verified in the project environment; Alembic applied, reversed and re-applied against the running PostgreSQL container, followed by `alembic check`; producer throughput and peak memory measured with `time.perf_counter` and `tracemalloc`; bulk-insert throughput measured against the live database inside transactions that were rolled back, so no rows were retained; content-type detection probed through the application's own detection function; unit and integration suites run in full.
- **Scope:** measurements and observed behaviour only. **No parser, threshold, or experiment winner is selected here.** The provisional producer decision is recorded separately in [ADR-002](ADR-002-provisional-pdf-producer.md). Measured results are not written into `PROJECT_BLUEPRINT.md`.
- **Sanitization:** no user name, absolute path, credential, proxy setting, or internal network detail is recorded. No document content appears in this record.
- **Relationship to earlier records:** ENV-001, ENV-002, ENV-003 and ADR-001 are unchanged. The Phase 3 residue section below records detection behaviour that previously existed only in a source docstring.

## Dependencies added

| Package | Version | Role |
|---|---|---|
| `pymupdf` | 1.28.2 | Runtime — the provisional PDF producer |
| `reportlab` | 5.0.1 | Test fixtures now; §32.3 makes it runtime at report rendering |
| `pillow` | 12.3.0 | Transitive, required by ReportLab |

`python -m pip check` reported no broken requirements. `python -m pip_audit` reported **no known vulnerabilities**.

Two facts worth recording because both contradicted a reasonable assumption:

- **`import fitz` is deprecated** as of PyMuPDF 1.28 and warns on import. The adapter imports `pymupdf`.
- **PyMuPDF ships `py.typed` but annotates almost nothing.** So it needs no `ignore_missing_imports` override of the kind boto3 required, yet strict mypy still rejects every call into it. `untyped_calls_exclude = ["pymupdf"]` exempts calls to that one library everywhere, rather than relaxing the adapter module, which would also hide untyped calls of our own.

## Schema

| Property | Observed value |
|---|---|
| Migration | `5ebe436c4357`, applied, reversed to `a2d5660001ae`, and re-applied cleanly |
| `alembic check` | "No new upgrade operations detected" — model and applied schema agree |
| Tables added | `extraction_runs`, `source_elements` |
| Column added | `document_versions.current_extraction_run_id`, nullable, `use_alter` foreign key |

### A NULL trap in a CHECK constraint, found by a test

The first constraint on `char_count` read `char_count = char_length(text)`. When `char_count` is NULL that expression evaluates to NULL, and **a CHECK constraint passes on NULL** — so an element could be stored with text and no recorded length, and a citation offset into it would have no bound to validate against. The mirror case, a length with no text, had the same hole.

The constraint now names both NULL conditions explicitly. This was caught by an integration test asserting the rejection, not by review.

## Coordinate convention

PyMuPDF reports geometry with a **top-left origin**, y increasing downward, with page rotation already resolved. Fixtures are written with ReportLab, which uses the PDF format's own **bottom-left** origin, so a flip in either library fails the test rather than cancelling out.

| Measurement | Value |
|---|---|
| Text drawn at | 700pt up from the bottom of an 841.89pt page |
| Baseline expected at | 141.89pt from the top |
| Reported block box | top 128.99pt, bottom 145.48pt — the baseline falls inside it |
| Same box if the axis were flipped | top 696.41pt, bottom 712.90pt — the baseline does not fall inside it |

The assertion needs no tolerance and discriminates: it holds for the real reading and fails for the flipped one, verified by computing both.

## Measured producer throughput

Synthetic documents, forty drawn text lines per page. **These prove the plumbing works and are not evidence about real filings** (CLAUDE.md §8).

| Pages | Size (KB) | Elements | Seconds | Pages/s | Peak memory (MiB) |
|---|---|---|---|---|---|
| 1 | 2 | 3 | 0.008 | 133 | < 0.1 |
| 10 | 8 | 30 | 0.013 | 767 | < 0.1 |
| 50 | 39 | 150 | 0.072 | 693 | 0.2 |
| 200 | 152 | 600 | 0.315 | 636 | 0.7 |
| 500 | 380 | 1,500 | 0.609 | 820 | 1.6 |

Parsing is not the constraint. Peak memory grows roughly linearly and stays small, but that is a property of these fixtures rather than a bound: the producer reads the whole document into memory, so peak use tracks document size, and the largest fixture here is 380 KB.

### The element counts above are unrepresentative, and understated

Forty drawn lines per page produced only **two blocks per page**, because PyMuPDF merges consecutive lines of uniform prose into one block. A real filing — mixed layouts, tables, headers, footnotes — yields far more elements per page than these fixtures do.

This matters for the next section: it means the persistence cost of a real document is **worse** than these measurements suggest, not better.

## Measured persistence throughput

Bulk insert into the live PostgreSQL container, inside rolled-back transactions.

| Elements | Before (s) | Before (elements/s) | After (s) | After (elements/s) |
|---|---|---|---|---|
| 410 | 0.504 | 813 | 0.087 | 4,712 |
| 4,100 | 4.849 | 846 | 0.788 | 5,206 |
| 20,500 | 27.146 | 755 | 5.198 | 3,944 |

### What "before" and "after" mean

Server-side `uuidv7()` identifiers mean the application must read new ids back to link children to parents, and correlating returned ids to the rows that produced them requires `sort_by_parameter_order=True`. That correlation forces much smaller insert batches. Measured directly, on 20,000 rows:

| Strategy | Seconds | Rows/s |
|---|---|---|
| `returning(id, sort_by_parameter_order=True)` | 23.222 | 861 |
| `returning(id)` unsorted | 2.387 | 8,378 |
| no `returning` | 2.522 | 7,930 |

The original implementation requested sorted ids at **every** depth of the element tree. Leaf elements dominate the row count and nothing ever reads their ids, so they were paying a tenfold penalty for nothing. Only a depth that actually has children now asks for its ids back.

The remaining gap between 3,944 and 7,930 elements per second is expected: the page level still pays the sorted cost, correctly, because its ids become parents.

### This remains the bounding constraint

At roughly 4,000 elements per second, a real five-hundred-page filing yielding tens of thousands of elements still spends **seconds to tens of seconds inside one transaction**. §29.7 requires bounded transactions, and this is the boundary. It is acceptable at current scale and is the first thing to re-measure against a real document.

## Phase 3 residue: content-type detection

Phase 3 recorded these findings only in a source docstring. §11.4 anticipates identifying detection limitations, and a verified claim holding up a security decision deserves a record. Re-probed for this entry rather than copied forward.

### A limitation

| Input | Filename hint | Detected as |
|---|---|---|
| XLSX archive | none | `…wordprocessingml.document` (**DOCX — wrong**) |
| XLSX archive | `book.xlsx` | `…spreadsheetml.sheet` (correct) |

OOXML formats are all ZIP containers with the same signature, so without a hint PureMagic cannot tell a workbook from a document. Detection therefore passes the sanitized filename to PureMagic. Without it, every spreadsheet upload would be misdetected and refused, breaking a format §5.2 supports.

True disambiguation requires opening the archive and reading its content types, which belongs to the spreadsheet parser (§13.2), not the router.

### The security property that makes the hint safe

A hint disambiguates *within* the family the bytes already prove, and **cannot promote one format to another**.

| Input | Lying hint | Detected as |
|---|---|---|
| XLSX archive | `evil.pdf` | `…wordprocessingml.document` — not PDF |
| XLSX archive | `evil.html` | `…wordprocessingml.document` — not HTML |
| PDF | `book.xlsx` | `application/pdf` |
| PDF | `page.html` | `application/pdf` |

The content signature wins in every case. This property is pinned by a test, and §30.5's requirement that a declared type agree with the detected signature is enforced separately.

## Recorded deviation from §28.2

§28.2 requires authentication on every non-health route. `/docs`, `/redoc` and `/openapi.json` are **enabled** and unauthenticated.

This is a knowing, recorded deviation taken by the developer, not an oversight. The endpoints carry real weight for endpoint discovery, schema inspection and manual testing while the API surface is still forming, and today they expose nothing beyond health-endpoint shapes.

**It closes at whichever comes first:** authentication landing; the first real non-health route being exposed; or deployment for production or review.

## Test coverage

| Suite | Count |
|---|---|
| Offline (`python -m pytest`) | 272 passed |
| Integration (`python -m pytest -m integration`) | 74 passed |

Integration tests run against the live PostgreSQL and SeaweedFS containers and **fail rather than skip** when infrastructure is unreachable.

Three limitations are asserted by tests rather than left implicit: multi-column reading order is *asserted wrong*; the newline PyMuPDF appends to every block is asserted preserved; and an image-only page is asserted to record a coverage gap rather than vanish.

## Open items

| Item | Owner phase |
|---|---|
| **Parser isolation is not enforced** (§11.7): only trusted development documents may be processed until the restricted parser container exists | Parser worker phase |
| Intra-document checkpointing (§11.9) — a failed page is recorded, but a failed run restarts from the first page | Jobs phase |
| §30.10 resource bounds on parsing remain unenforceable without the parser worker | Parser worker phase |
| Persistence throughput is the bounding constraint at ~4,000 elements/s; re-measure against a real filing | First real document |
| The producer reads whole documents into memory; peak use tracks document size, unmeasured above 380 KB | First real document |
| `extraction_runs` has no `running` state, so the partial unique index only bites at completion; two concurrent workers would both extract and the loser fail late. One additive CHECK line when workers arrive | Jobs phase |
| Reading order is single-column; multi-column layouts read across rather than down | Parser evaluation (§12.9) |
| No table extraction; `find_tables()` unused | Table representation slice (§17) |
| No OCR; image-only pages record a coverage gap | If a scanned corpus is admitted |
| Element counts measured only on synthetic fixtures, which understate real documents | First real document |
| Container resource limits still deferred — idle usage measured in ENV-003, peak under load not | First realistic ingestion workload |
| `/docs`, `/redoc`, `/openapi.json` unauthenticated — see the recorded deviation above | Authentication phase |
