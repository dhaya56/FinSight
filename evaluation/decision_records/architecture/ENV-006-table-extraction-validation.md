# ENV-006 — Table Extraction Validation

- **Status:** complete for the measurements it covers; ground truth outstanding
- **Date:** 2026-10-02
- **Phase:** 6 — table detection and the completion of the source representation
- **Scope:** real-corpus measurement of table detection, structure reconstruction,
  persistence cost and the routing hypothesis. **Not a strategy selection.**

## What this is

The development split — three real filings, 1,403 pages — measured after Phase 6
widened the element model to tables and cells. Every figure here comes from the
real corpus unless marked otherwise.

It records three results that contradicted expectations, including two of this
assistant's own claims, and one negative result that closes a line of enquiry.

Admission of a detection strategy remains ADR-003's decision; parser selection
remains ADR-004's.

---

## 1. Element volume: the growth was modest

| Document | Pages | Blocks | Tables | Cells | Total |
|---|---|---|---|---|---|
| Infosys | 369 | 14,686 | 87 | 1,226 | 16,368 |
| HDFC Bank | 590 | 16,271 | 485 | 10,923 | 28,269 |
| Ola Electric | 444 | 9,519 | 289 | 11,464 | 21,716 |
| **Total** | **1,403** | **40,476** | **861** | **23,613** | **66,353** |

**Growth over ENV-005's 41,879 elements: 1.58×** — far less than the several-fold
expansion the Phase 6 plan warned of. Cell-level elements are affordable.

**Blocks came back at exactly 40,476**, ENV-005's figure to the row. That is the
useful consistency check: adding table detection left the block path untouched.

Table density varies by a factor of five between documents — Infosys 87 tables,
HDFC 485 — which matters because sampling one document to project the others, as
was done once during this phase, produces a badly wrong estimate.

---

## 2. The two strategies are not complementary

A synthetic fixture suggested `lines` and `text` were complementary, because
`lines` found **zero** tables on a fully borderless copy of a table it read
correctly when ruled. That conclusion was wrong, and the real corpus contradicted
it on the first measurement.

150 pages sampled evenly across the three documents, both strategies on each:

| Document | Both | **`lines` only** | `text` only | Neither |
|---|---|---|---|---|
| Infosys | 7 | **0** | 41 | 2 |
| HDFC Bank | 17 | **0** | 31 | 2 |
| Ola Electric | 23 | **0** | 26 | 1 |
| **Total** | **47 (31%)** | **0 (0%)** | **98 (65%)** | **5 (3%)** |

**`lines` never once found a table that `text` missed.** On real filings `text` is
a strict superset.

And `text` fires on **145 of 150 pages — 97%**. Financial filings do not carry a
table on 97% of their pages. That is not recall; it is a near-total false-positive
rate, consistent with ENV-005's 37–40 detections per 40-page sample.

**Why the fixture misled.** It was *fully* unruled. Real financial tables are
almost always at least partially ruled, so `lines` finds them. The fixture
manufactured a condition the corpus barely contains.

**Consequence for ADR-003.** The decision was framed as recall — which strategy
finds more tables. Recall is saturated and uninformative. **The binding constraint
is precision**, and precision remains unmeasured because it requires the ground
truth. A detector claiming a table on a narrative page produces a chunk that
presents as structured financial data and is not.

This is the second time in this project that a synthetic fixture overstated real
behaviour: ENV-004 overstated producer throughput by ~3.5×. The pattern is
structural, not incidental.

---

## 3. The routing hypothesis: a negative result

**Hypothesis.** A cheap classifier could skip the expensive detector on pages that
hold no table. Motivated by measured cost: `find_tables` runs at **7.9 pages/s**
against `get_text("blocks")` at **350 pages/s — 44×** — and only 36% of pages hold
a table.

**Constraint.** A pre-filter that skips a page holding a table loses it silently.
So recall is binding; precision only costs time.

**Method.** Per-page features for all 1,403 pages — horizontal and vertical ruling
segment counts at five minimum lengths (2, 5, 10, 20, 40 pt) with a 3.0 pt axis
tolerance, covering stroked lines, thin rectangles and degenerate quads; drawing
item and object counts; text block and numeric-block counts — captured alongside
`find_tables` ground truth, then ~150 candidate predicates evaluated offline.

**504 of 1,403 pages (36%) hold at least one table.**

| Recall | Best predicate at that tier | Pages skipped | Table pages lost | **Cells lost** |
|---|---|---|---|---|
| **100%** | drawing items ≥ 2 | **17.8%** | 0 | **0** |
| 99% | drawing items ≥ 4 | 19.4% | 2 | 9 |
| 98% | drawing items ≥ 6 | 24.8% | 6 | 28 |
| 95% | ≥1 horizontal rule ≥40 pt | 30.7% | 25 | 746 |
| 90% | ≥6 horizontal rules ≥20 pt | 47.8% | 46 | **1,245** |
| 80% | ≥15 horizontal rules ≥20 pt | 57.2% | 96 | 1,998 |

**The best safe predicate saves 17.8%.** Reaching the 40–50% skip that would
materially change the throughput picture requires dropping to 90% recall, which
loses 46 table pages and **1,245 cells — 5.3% of the corpus — silently.** That is
precisely the failure class this project is built to avoid.

### Why an intuitive predicate failed, and why fixing it did not help

An early attempt used "≥2 horizontal and ≥1 vertical rule, segments ≥20 pt" and
scored **38.9% recall**, which looked like an implementation fault. It partly was.
Table pages carrying **no** vertical rule:

| Minimum segment length | Table pages with no vertical rule |
|---|---|
| ≥2 pt | **155 (31%)** |
| ≥20 pt | 295 (59%) |
| ≥40 pt | 374 (74%) |

So the 20 pt threshold made 59% of table pages look vertically unruled, and
loosening to 2 pt recovered real signal. **Most financial tables in this corpus
are ruled horizontally only** — row separators, no column lines — which is how
Schedule III statements are typically typeset.

But horizontal ruling is near-universal on table pages: only **21–25 of 504
(4–5%)** lack a horizontal rule at any threshold. Those few pages are the binding
constraint, and no horizontal predicate reaches 100% recall because of them.

**Conclusion: cascaded routing works, and does not pay here.** Recorded as a
closed line of enquiry rather than a half-remembered dead end.

**The evidence is stored, not merely described.** The per-page features and the
detection ground truth for all 1,403 pages are committed at
`evaluation/data/routing-features-development.tsv`, with provenance, column
definitions and a SHA-256 in `evaluation/data/README.md`. The expensive column is
the ground truth — `find_tables` at ~7.9 pages/s means re-collecting costs another
multi-minute corpus pass — so evaluating a new predicate is an offline scan rather
than a re-measurement. The conclusion above is therefore checkable, and a better
signal can be tested without touching the corpus.

That file supports the *routing* conclusion only, which concerns relative cost at
fixed detector behaviour. Its ground-truth column is a detector's output, not
human-verified truth, so it supports no claim about detection quality.

---

## 4. The merge-versus-miss ambiguity is not a corner case

Span inference reads "populated cell followed by an absent cell" as a merge. A
detector that simply failed to find a cell produces an identical signal. The
distinction is **undecidable** from a grid.

Across the 861 detected tables, the share of grid positions reported absent:

- **mean 19%, median 11%**
- **436 of 861 tables (51%) above 10% absent**

For the majority of real tables this ambiguity applies to a non-trivial part of
the grid. Every such position is either a merge the span records correctly or a
detection failure the span silently papers over.

This is the strongest quantitative argument in the project for the
reconstruction-burden criterion, and ENV-007 establishes that Docling reports
spans explicitly, removing the error class rather than shrinking it.

---

## 5. Units are unknown for most tables

Of 861 tables, only **104 (12%)** carry a units declaration the detection rule can
see. **31 (4%)** show a mixed-scale signature.

Either financial tables mostly declare scale *outside* the table — a section
heading, a column header above the detected region, a page-level note — or the
rule misses the forms they use. **Both readings are bad**: a cell whose units are
unknown is a figure whose scale is unknown, and §16 must refuse to normalise it.
88% of tables currently produce such cells.

Separating "declared elsewhere" from "declaration missed" needs the ground truth,
and is now among its highest-value questions.

Units were also implemented at the wrong scope initially. §17.3 requires
"table-level **and column-level**" context; the first implementation made units a
table attribute, which would apply one scale to a table mixing crore figures with
percentages. Corrected to cell scope with position deciding scope. A **row**-level
scale change — a ratio row among absolute rows — remains undetected and is
recorded in the limitation register.

---

## 6. Footnote forms: the assumed vocabulary was wrong

Marker forms counted across all 1,403 pages. **Trailing** markers are what the
cell extractor targets; **leading** markers are what a footnote's own text line
looks like.

| Form | Trailing | Leading |
|---|---|---|
| `(digit)` | 937 | 426 |
| `(alpha)` | 450 | 472 |
| `*` | 361 | 103 |
| **`#`** | **56** | **23** |
| `**` | 39 | 20 |
| `***` | 6 | 3 |
| `##` | 1 | — |
| `###` | 1 | — |

**`#` was missed entirely** by the pattern written from assumption — 56
occurrences silently dropped. Fixed, with a test pinning every measured form.

No superscript digits appear anywhere in the corpus, and neither do `†` or `‡`.
Those two are retained because other filings use them, but their validation here
is zero. No Devanagari blocks either, so the multi-script concern does not apply
to this corpus.

**The resolution target exists.** Roughly a thousand leading markers means
footnote text is present and mechanically findable. A marker still resolves to
nothing: no footnote element, no text, no link. A number released without its
qualifier is wrong, not incomplete.

---

## 7. Rotated-page tables: validated on real data

ENV-005 fixed a coordinate defect affecting 2,401 block boxes on the 78
`/Rotate 90` pages. Cells inherit the same trap, and Phase 6 initially verified
cell geometry only on a *synthetic* rotated fixture — a gap in its own testing.

Closed with real data:

| Rotation | Pages | Table pages | Tables | Cells |
|---|---|---|---|---|
| 0 | 1,325 | 493 | 850 | 23,342 |
| **90** | **78** | **11** | **11** | **271** |

271 cells on rotated pages now carry coordinates transformed into displayed
space. A test also asserts cell containment across all four rotations.

---

## 8. Table shape and corpus characteristics

**Largest tables** (cells / rows × columns):

| Cells | Shape | Document | Page |
|---|---|---|---|
| 308 | 62 × 5 | Ola Electric | 71 |
| 287 | 58 × 5 | Ola Electric | 73 |
| 272 | **12 × 26** | HDFC Bank | 348 |
| 266 | 54 × 5 | Ola Electric | 72 |
| 245 | 14 × 19 | HDFC Bank | 353 |

A 26-column table will stress §17.8's large-table context recovery, which is
currently unexercised.

**Figure-origin signals.** **841 of 1,403 pages (60%)** carry more than 40 drawing
items, but only **50 (4%)** also show five or more bare-numeric short blocks — the
signature of chart axis and data labels extracting as context-free numbers. So
heavy vector content is the norm and mostly design furniture; the chart-label risk
is real but confined to about 4% of pages. §41.5 correctly excludes chart
*interpretation*; marking figure-origin text remains unbuilt.

**Zero image blocks across all 1,403 pages**, re-confirming ENV-005: no scanned
content, so the OCR and image-only paths remain untestable with this corpus.

---

## 9. Persistence: the cell-id strategy, measured

`source_table_cells` is keyed on cell ids, but cells are leaves. ENV-004
established that correlating returned ids to their parameters costs roughly ten
times the plain insert rate, so the previous strategy avoided it for leaves.
Tables broke that twice over: cells are leaves *and* need ids, and a page holding
both blocks and tables puts leaves and parents on one depth, sending the whole
depth down the slow path.

20,000 cells with their extension rows, two runs each:

| Strategy | Run 1 | Run 2 |
|---|---|---|
| Insert returning ids | 1,302 cells/s | 1,152 cells/s |
| **Pre-allocate from one `uuidv7()` batch** | **4,539 cells/s** | **3,878 cells/s** |

**≈3.4×**, and 5 s against 16 s inside a transaction §29.7 wants bounded. Adopted;
the `RETURNING` path was removed entirely. Ids now come from one
`SELECT uuidv7() FROM generate_series` per tree, after which every depth inserts
through the plain path.

---

## 10. Throughput, and a correction

**Table detection dominates extraction cost.** Measured per layer on real
documents:

| Stage | Ola | HDFC Bank | Infosys |
|---|---|---|---|
| `find_tables` | 157.4 s | 84.8 s | 85.1 s |
| Span gathering | 1.8 s | 1.3 s | 0.3 s |
| `text_left` | **0.5 s** | **0.5 s** | **0.0 s** |
| `derive` | 0.5 s | 0.2 s | 0.0 s |
| `to_element` | 0.1 s | 0.1 s | 0.0 s |
| **Reconstruction total** | **2.9 s** | **2.1 s** | **0.3 s** |

**Reconstruction is 1–3% of extraction cost.** A concern that `text_left` might be
an expensive O(cells × spans) scan was unfounded: **1,929,950 comparisons in
0.5 s**. No optimisation is warranted.

### The correction

An earlier figure of 798 s for the corpus, reported as a 26× slowdown against
ENV-005's 30.8 s, was **inflated by this assistant's own instrumentation** —
`tracemalloc` was active to capture a memory figure, and it instruments every
allocation. Re-measured without it, `produce()` totals 471 s across the three
documents, and a separate pass measured 312 s.

**Corrected claim: roughly 300–500 s for the corpus against ENV-005's 30.8 s — a
10–16× slowdown, not 26×.** A range, because a single number would be false
precision.

### A methodology finding that applies to every timing in this project

The same work measured twice disagreed by up to **±50%**: Ola's `produce()` came
in *below* its own `find_tables` on a separate run, which is impossible as a cost
breakdown. ENV-001 records 4 physical cores, and these runs sustained full load
for twenty-minute stretches; thermal throttling and background load dominate at
this granularity.

**Any throughput figure from this host needs repeated runs with the machine
otherwise idle, and should be reported as a spread.** That caveat applies
retroactively to ENV-005's 184–220 pages/s and its 30.8 s corpus total, both
single runs.

Peak traced Python allocation was 79 MiB. That counts Python allocations only —
not PyMuPDF's C-side or process RSS — so it is not a §30.10 resource bound.

---

## 11. Limitations

- **No ground truth yet.** Detector precision, header-boundary misfire rate,
  span-inference accuracy and units-detection recall are all unmeasured against
  human-verified truth. Everything in §2 and §4 bounds the *size* of a problem
  without measuring the error rate.
- **`find_tables` is its own ground truth in §3.** The routing predicates were
  scored against what `lines` detected, not against real tables. A predicate with
  100% recall against a detector that misses tables still misses them.
- **Three documents, three issuers, two document types.** A fourth issuer's
  presentation conventions are unknown.
- **Partially ruled tables unmeasured** — ruled border, unruled interior. The
  realistic hard case, and neither fixture nor measurement covers it.
- **Timing variance ±50%**, as above.
- **Chart-label detection uses a crude proxy** — short bare-numeric blocks on
  drawing-heavy pages.

---

## 12. Open items

| # | Item | Owner |
|---|---|---|
| 1 | **Detector precision against human-verified truth.** The binding constraint for ADR-003 | Phase 6 ground truth |
| 2 | Units: whether 88% is "declared elsewhere" or "rule misses it" | Phase 6 ground truth |
| 3 | Header-boundary misfire rate — a bare four-digit magnitude reads as a year | Phase 6 ground truth |
| 4 | Partially ruled tables | Phase 6 ground truth |
| 5 | Footnote resolution — marker to text, unsolved by either parser | footnote commit |
| 6 | Figure-origin marking for the ~4% of pages at risk | figure commit |
| 7 | Row-label hierarchy from typography rather than indentation | limitation register |
| 8 | Table-aware reading order — now possible, still unbuilt; ENV-005's 14% divergence is its baseline | Phase 7 |
| 9 | Duplicate text between blocks and cells — deliberate, but §18.4 chunking must not retrieve the same sentence twice | Phase 7 |
| 10 | §17.8 large-table context recovery, unexercised against a 26-column table | retrieval phase |
| 11 | Processing jobs: ENV-005 deferred them on 1.8–2.7 s per document. At 116–219 s per document that evidence is obsolete | Phase 7 |

Item 11 is the consequence most likely to be overlooked. Phase 5 closed the jobs
question *on evidence*, and that evidence no longer holds. The bounded-transaction
design absorbed the change without breaching §29.7 — extraction already runs
outside the transaction — but a synchronous command that takes minutes on one
filing is a different proposition from one that takes seconds.
