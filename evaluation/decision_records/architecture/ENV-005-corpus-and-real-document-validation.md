# ENV-005 — Development Corpus and Real-Document Validation

- **Status:** recorded
- **Date:** 2026-09-29
- **Phase:** 5 — development corpus and real-document validation
- **Method:** six documents acquired manually by the developer from official issuer, regulator and exchange repositories; checksummed through `corpus checksum`; recorded in a committed manifest; verified with `corpus verify`; the development split put through the existing intake and extraction path with `corpus ingest`. Document properties were derived with read-only inspection of the development split only. Held-out documents were hashed and never opened.
- **Scope:** measurements and observed behaviour. **No parser, chunking method, threshold or experiment winner is selected here.** Measured results are not written into `PROJECT_BLUEPRINT.md` (§36.16).
- **Sanitization:** no user name, absolute path, credential or network detail. **No document content appears in this record** — only counts, geometry and derived flags. Issuer names and document identities are already public and are recorded in the committed manifest.
- **Relationship to earlier records:** ENV-001 to ENV-004 and ADR-001/ADR-002 are unchanged. Several ENV-004 open items close here; several of its synthetic measurements are superseded by real ones.

## Corpus composition

| Split | Documents | Pages | Bytes |
|---|---|---|---|
| `development` | 3 | 1,403 | 27,173,182 |
| `held_out_pdf_core` | 2 | not inspected | 34,423,131 |
| `held_out_format_supplement` | 1 | n/a (HTML) | 8,585,500 |

Development: Infosys FY2024-25 annual report, HDFC Bank FY2024-25 annual report, Ola Electric DRHP. Held out and frozen: TCS FY2024-25 annual report, Urban Company DRHP, Microsoft Form 10-K. Splits were assigned at acquisition, before any question exists (§32.9, §34.9), and no issuer appears in more than one split (§34.12).

No source document is committed. The repository holds the manifest and checksums only (§32.8).

## The run as a prediction test

`expected_challenges` was written into the manifest **before** extraction, which makes this a test rather than a description. Three predictions were falsifiable:

| Prediction | Outcome |
|---|---|
| HDFC Bank yields **exactly one** coverage gap (exactly one page held no text) | **Confirmed** — 1 gap, run state `partial` |
| Ola Electric yields **no** coverage gaps | **Confirmed** — 0 gaps, `succeeded` |
| Infosys yields **no** coverage gaps | **Confirmed** — 0 gaps, `succeeded` |

Element counts matched the pre-run inspection exactly in all three documents, confirming that extraction lost nothing between inspection and persistence.

Two earlier predictions were falsified **before** the run, during inspection, and corrected in the manifest rather than quietly dropped: Ola presents values in INR **million**, not crore; and Ola has **no** image-only regions, so the predicted coverage gaps from scanned content could not occur.

## Measured extraction

With allocation tracing enabled, which inflates elapsed time:

| Document | State | Pages | Blocks | Gaps | Seconds | Peak Python (MiB) |
|---|---|---|---|---|---|---|
| Infosys | succeeded | 369 | 14,686 | 0 | 11.45 | 47.9 |
| HDFC Bank | partial | 590 | 16,271 | 1 | 11.24 | 48.4 |
| Ola Electric | succeeded | 444 | 9,519 | 0 | 8.10 | 29.4 |
| **Total** | | **1,403** | **40,476** | **1** | **30.80** | |

Producer alone, without tracing or database access:

| Document | Seconds | Pages/s | Elements/s |
|---|---|---|---|
| HDFC Bank | 2.68 | 220 | 6,293 |
| Infosys | 1.78 | 207 | 8,453 |
| Ola Electric | 2.41 | 184 | 4,137 |

**Re-running the split took 0.58s against 30.80s** and created no new runs: intake is content-addressed and extraction is guarded by the partial unique index, so idempotence holds on real documents and not only in tests.

**Ingesting the held-out split is refused** by the store, not by a flag check, and the refusal states that checksum verification remains permitted.

### Two ENV-004 measurements are superseded

**Producer throughput on synthetic fixtures overstated real throughput by roughly 3.5×.** ENV-004 measured ~700–820 pages/s on generated PDFs; real filings run at 184–220 pages/s. The synthetic pages were uniform prose that PyMuPDF merges into two blocks per page; real pages carry 21 to 40 blocks each.

**Element volume was overestimated in the Phase 4 plan by 2.5× to 10×.** That plan projected 25,000–100,000 elements for a 500-page offer document. Ola's 444 pages produced 9,963. The whole development split is 41,879 elements.

### Memory is not the constraint at this scale

Peak Python allocation was 29–48 MiB per document against ENV-003's measured available host memory. The 5–50 MB documents ENV-004 worried about are real — the largest acquired file is 23.9 MB — but the largest *processed* is 10.7 MB and cost under 50 MiB.

**This figure is Python-side only.** `tracemalloc` does not see PyMuPDF's C allocations, so the parser's own buffers are excluded. Resident set size is the number that matters on a constrained host and requires a dependency this project has not admitted.

## A citation defect found and fixed (§13)

Real documents exposed a defect no synthetic fixture had.

| | |
|---|---|
| **Baseline** | 2,401 of 40,476 block bounding boxes fell outside their own page rectangle |
| **Candidate** | Multiply each box by `page.rotation_matrix`, normalise, and apply **before** reading order is decided |
| **Method** | Count blocks exceeding `page.rect` by more than 1pt |
| **Workload** | 3 development documents, 1,403 pages, 40,476 blocks |
| **Before** | 2,401 bad — all on `/Rotate 90` pages, all in one document |
| **After** | **0**, verified through the producer on all three documents |
| **Limitations** | Only one document exercises `/Rotate`; rotations of 180 and 270 remain covered by synthetic fixtures alone |
| **Decision** | Applied unconditionally; the matrix is the identity on unrotated pages |

`page.rect` is the displayed rectangle with rotation applied, while `get_text("blocks")` returns boxes in the unrotated page space. Storing one of each meant every citation on 78 pages — 21% of that document's 369 — pointed somewhere the reader is not looking, with a median displacement of 124pt. Reading order was affected too, since sorting raw coordinates on a rotated page sorts along the wrong axis.

The existing rotation test did not catch it because it asserted that text was *recovered*, never that its box was correct, and its fixture placed text near the origin corner, which lies inside both coordinate spaces. Both faults are now fixed: a test asserts every block box lies within its page, and a second asserts the fixture itself would fail without the transform.

## Observed document properties

| | Infosys | HDFC Bank | Ola Electric |
|---|---|---|---|
| Pages | 369 | 590 | 444 |
| KB per page | 18.6 | 15.6 | 23.6 |
| Distinct page geometries | 3 | 2 | 5 |
| Landscape pages, wide MediaBox | 28 | 7 | 31 |
| Landscape pages, `/Rotate 90` | **78** | 0 | 0 |
| Blocks per page, max | 168 | 155 | 98 |
| Pages with no text | 0 | 1 | 0 |
| Producer | Adobe PDF Library | Acrobat Distiller 11 | Microsoft Word |

### Reading order

Comparing the positional rule against a column-aware ordering, the two disagree on:

| Document | Pages | Share |
|---|---|---|
| HDFC Bank | 145 of 590 | 25% |
| Infosys | 48 of 369 | 13% |
| Ola Electric | 2 of 444 | 0% |

**195 of 1,403 pages, 14% overall.** This measures sensitivity to the choice of rule, not that the current rule is wrong — the column-aware comparison is itself a heuristic. It gives §35.4's chunking comparison a concrete baseline.

An expectation was inverted: the dense offer document is effectively single-column, while the designed annual reports carry the multi-column layouts.

### Checks that found nothing

Clean across all three: `MediaBox` equals `CropBox` on every page; no non-zero or negative box origins; no Type 3 fonts and every font embedded; no `U+FFFD` replacement characters, including in a document with nine fonts declaring no encoding; text is NFC-normalised on all but one page; no embedded files, annotations, forms or XFA; nothing required repair on open.

Devanagari is present — 680 characters in Infosys, 841 in Ola — which substantiates the language-fixture gap recorded below and bears on §9.7's unstated text-search configuration.

3,333 spans are coloured pure white. Given vector art on most pages these are very likely legitimate white-on-colour design, but distinguishing that from white-on-white needs background analysis this project does not perform.

## Metadata provenance, and a schema change that proved unnecessary

An early draft of the manifest could not establish `published_at` for three documents or `period_end` for one, and both fields were made optional so that absence could be recorded rather than estimated. **Exhausting the official sources removed the need, and the change was reverted.**

What closed the gaps:

| Field | Source |
|---|---|
| Publication dates, Indian annual reports | Exchange submission letters filed under Regulation 34, each carrying a stated date |
| Publication dates, offer documents | SEBI filing pages, which state the date in page content |
| Publication date, Form 10-K | EDGAR filing index |
| Urban Company period, basis and units | SEBI-hosted public filing material, which states the restated consolidated information, a nine-month stub ended 31 December 2024, and amounts in INR millions |
| TCS basis and units | NSE-hosted filing material |
| Microsoft basis and units | EDGAR |

Every entry now records both dates, so the flexibility had no case left. `period_end` and `published_at` are mandatory again, alongside `retrieved_at`, `issuer_name`, `source_url`, `selection_rationale` and `expected_challenges`.

The sequencing is the lesson worth keeping: the schema was nearly relaxed to accommodate a limitation that turned out to be a search problem. Exhaust the authoritative sources **before** widening a record's tolerances.

### Nothing remains unrecorded

`corpus validate` reports no unrecorded fields for any of the six entries. Every value came from an official issuer, exchange, regulator or offer-document repository, and **no frozen document was opened to obtain any of them** — the three held-out entries are described entirely from material those repositories publish alongside the documents.

The reporting stays in place regardless. A corpus that grows incrementally (§32.11) will acquire entries whose metadata is harder to source, and a gap must remain distinguishable from an oversight when it appears.

### One provenance caveat on TCS

`reporting_basis` for TCS is recorded as `consolidated`, from NSE-hosted filing material. The two other Indian annual reports in the corpus are recorded as `both`, because inspection confirmed each presents consolidated *and* standalone statements — which Indian listed companies are required to do.

TCS's annual report very probably presents both as well. If the NSE material described a financial-results filing rather than the annual report document, `consolidated` would describe a different artifact than the one held here. This cannot be checked without opening a frozen document, so the recorded value stands with the discrepancy noted: three documents of the same class, one described differently from the other two.

### One provenance caveat

HDFC Bank's publication date of 14 July 2025 is the Regulation 34 submission of the FY2024-25 Integrated Annual Report. A further submission on 25 July 2025 revised leadership-profile information only and did not republish the report, so the earlier date stands.

The acquired file's own internal creation date is 19 November 2025, after both. Our copy is therefore plausibly a later re-save published on the issuer's website. The three fields remain individually correct — `published_at` records when the report was published, `sha256` identifies the copy held, `retrieved_at` when it was taken — but the bytes measured here are not demonstrably identical to the 14 July submission.

## What this closes, and what it does not

**Closed by measurement:** producer throughput on real documents; element volume on real documents; peak allocation above the 380 KB ceiling ENV-004 recorded; idempotence of the corpus path; extraction coverage on real filings.

**Quantified but not resolved:** multi-column reading order, now measured at 14% of pages.

**Still open, unchanged:** parser isolation is not enforced (§11.7), so only trusted development documents may be processed; intra-document checkpointing (§11.9); §30.10 parser resource bounds; the absent `running` state on `extraction_runs`; container resource limits (§38.11), though real load data now exists; `/docs` unauthenticated (§28.2).

**Newly recorded limitations:**

1. **Only §36.1's page coverage, provenance and failure metrics are measurable.** Table, cell, header, row-label, period, value, unit, basis and concept metrics all require capabilities later phases introduce.
2. **Three documents are not a basis for quality claims** (§41.8). Nothing here says extraction is accurate — only that it is complete, bounded, and internally consistent.
3. **The corpus contains no scanned content.** Zero image blocks across 1,403 pages, so the image-only coverage-gap path and any OCR routing are entirely untested by real data.
4. **Resident set size is unmeasured.** Only Python-side allocation is recorded.
5. **Invisible text is undetected.** Render mode 3 is not exposed by the extraction path used, so an OCR layer or deliberately hidden text would be extracted without being flagged — relevant to §30.11.
6. **`CropBox` mismatch and non-zero box origins are untested**, being absent from every acquired document.
7. **Table detection is unvalidated.** On a 40-page sample per document the `lines` strategy found 10 to 26 tables and the `text` strategy 37 to 40 — the latter implausibly close to one per page, indicating over-detection. Neither is trustworthy without the §36.2 metrics.
8. **§32.5 format coverage is partial.** PDF core plus one HTML supplement; XLSX and XML supplements are deferred.
9. **One integration test failed once and has not reproduced.** `test_an_unreadable_document_records_a_failed_run` failed during a full check set, then passed in isolation and across five consecutive full suite runs. No extraction code changed in that window — the edits were manifest data and prose. The assertion uses `scalar_one()` over `extraction_runs` for a single document version, so it fails on either zero or multiple rows, which points at cross-test state rather than logic. Phase 5 is not blocked on it, but it is recorded as **observed and currently unreproducible** rather than dismissed: an earlier bug in this phase had the same shape, a module-scoped fixture disposing shared clients that another module still held, and a failure seen once at roughly one run in seven will eventually surface in CI.

   **Resolved during Phase 6, and it was not a test problem.** The cause was two
   clocks adjudicated by one constraint: `extraction_runs.started_at` defaulted to
   the server's `now()` while `complete_run` set `completed_at` from the
   *application's* clock, and `ck_extraction_runs_completed_after_started`
   compared them. The guess above — cross-test state, zero or multiple rows — was
   wrong; the real failure was an `IntegrityError` on that constraint. A second
   guess, that the test's random `uuid4()` content sometimes let PyMuPDF repair
   the file rather than refuse it, was also wrong: 2,000 random variants all
   raised. Neither guess survived being tested, which is why the traceback was
   worth capturing instead.

   | | |
   |---|---|
   | **Baseline** | Application clock for `completed_at`; **3 of 6** cycles failed, with 1, 3 and 6 test failures in the failing cycles |
   | **Candidate** | `completed_at` from the database clock via `clock_timestamp()` |
   | **Method** | Six cycles of `alembic downgrade -1`, `upgrade head`, then the full integration suite — the sequence that made the failure frequent — plus direct sampling of both clocks |
   | **Workload** | 86 integration tests against PostgreSQL 18.6 and the object store |
   | **After** | **0 of 6** cycles failed, 87 passed each time |
   | **Limitations** | The after-run happened under *favourable* skew (6 of 300 samples inverted, against 277 of 300 during the baseline), so the six green cycles are weak evidence on their own. The load-bearing evidence is the invariant, not the run count |
   | **Decision** | Applied. `clock_timestamp()` rather than `now()` because `now()` is transaction-start time and would make every duration exactly zero |

   Measured skew between the Python process and the PostgreSQL container ranged
   from **5.3ms behind to 13.1ms ahead**, reversing direction within an hour — so
   the old code's correctness depended on an external variable nothing in the
   system controlled. That is what made it intermittent, and why one run in seven
   was the wrong model: the rate tracks drift, not chance. Under a deterministic
   5ms adverse skew, inside the measured range, an application timestamp fails the
   constraint and a database timestamp holds. `clock_timestamp() >= now()` held in
   1,000 of 1,000 samples inside real transactions, because PostgreSQL fixes
   `now()` at transaction start and `clock_timestamp()` advances from there.

   The exposure was never limited to that one test. A second reproduction failed
   `test_a_failed_run_leaves_the_pointer_unset` instead, and the success path is
   equally affected: any extraction completing faster than the prevailing skew
   could have its run rejected. A small filing qualifies, so this was a production
   correctness defect that happened to surface first in tests.

## Proposed additions to §33

Four fixture categories the blueprint does not cover, each needed by an existing requirement:

1. **Citation-boundary fixtures** — §36.6 measures citation accuracy and §27.6/§27.7 require resolution and context validation, with no fixture category attacking them.
2. **Language and script fixtures** — Devanagari is confirmed present, and §9.7 fixes no text-search configuration.
3. **Boilerplate and repetition fixtures** — §18.8 requires demotion and §36.3 measures duplication, with nothing generating controlled repetition.
4. **Hidden-text fixtures** — §30.11 requires prompt-injection structural controls, with no fixture generating invisible text.

None should be built before the phase that consumes them (§33.10).
