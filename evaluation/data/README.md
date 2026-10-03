# Evaluation data

Measured evidence that a decision record depends on, preserved so a conclusion
can be re-tested rather than re-derived.

Nothing here contains source-document text, financial values, or any other filing
content (CLAUDE.md §10). These are structural page statistics only: counts of
ruling segments, drawing items, text blocks and detected table cells. Source
documents themselves are never committed and remain gitignored.

---

## `routing-features-development.tsv`

Per-page features and detection ground truth for the whole development split.
Collected for ENV-006 §3, the routing-predicate negative result.

| | |
|---|---|
| **Rows** | 1,403 — one per page, plus a header line |
| **Bytes** | 77,012 |
| **SHA-256** | `3e4e2409ebf9885f92b72569662bcd538819920cfaa51a950ce15c3c7eb423f9` |
| **Collected** | 2026-10-02, in a single pass taking 264 s |
| **Corpus** | `development` split — Infosys 369 pages, HDFC Bank 590, Ola Electric 444 |
| **Producer** | PyMuPDF 1.28.2, `find_tables(strategy="lines")` |

### Columns

| Column | Meaning |
|---|---|
| `doc` | Document short name, matching ENV-005 and ENV-006 |
| `page` | One-based page number |
| `h2` … `h40` | Count of **horizontal** ruling segments at least 2, 5, 10, 20 or 40 pt long |
| `v2` … `v40` | The same for **vertical** segments |
| `items` | Total drawing items on the page |
| `objects` | Number of drawing objects (an object may hold several items) |
| `blocks` | Text block count |
| `numeric_blocks` | Text blocks where more than 30% of characters are digits |
| `chars` | Total characters across the page's text blocks |
| `tables` | Tables detected by the `lines` strategy — **the ground truth column** |
| `cells` | Non-absent cells across those tables |

Segment counting uses a 3.0 pt axis tolerance and covers stroked line items,
rectangles thin enough to be a rule, and degenerate quads — because a table ruled
with any of those looks the same to a reader and must look the same to a
predicate. An earlier 20 pt minimum with a 1.5 pt tolerance produced a misleading
result, which is why five thresholds are stored rather than one.

### Why it is kept

The expensive column is `tables`: `find_tables` runs at roughly 7.9 pages/s, so
re-collecting means another multi-minute pass over the corpus. With these features
stored, evaluating a new routing predicate is an offline scan of 1,403 rows.

ENV-006 concluded that cascaded routing does not pay — the best 100%-recall
predicate skips only 17.8% of pages. That conclusion is checkable against this
file, and a better signal can be tested against it without touching the corpus.

### Consistency check

The file independently reproduces ENV-006's headline figures:

| Figure | From this file | ENV-006 |
|---|---|---|
| Table pages | 504 of 1,403 | 504 of 1,403 |
| Tables | 861 | 861 |
| Cells | 23,613 | 23,613 |
| Infosys | 369 p / 87 t / 1,226 c | same |
| HDFC Bank | 590 p / 485 t / 10,923 c | same |
| Ola Electric | 444 p / 289 t / 11,464 c | same |

### Limitation

**The `tables` column is a detector's output, not human-verified truth.** A
predicate scoring 100% recall against it still misses any table the `lines`
strategy itself misses. ENV-006 §2 measures that strategy firing on only 31% of
pages where `text` fires on 97%, and neither is validated. Human-verified ground
truth is outstanding and is ADR-003's prerequisite.

So this file supports the *routing* conclusion, which is about relative cost at
fixed detector behaviour. It does not support any claim about detection quality.

---

## `detector-precision-annotation.tsv`

The human-verified table-presence truth behind ENV-006 §2.1 and ADR-003 — **the
only human-verified detection truth this project has.**

| | |
|---|---|
| **Rows** | 10 — one per annotated page, plus a header line |
| **Bytes** | 1,341 |
| **SHA-256** | `ad392cea240448014743d7af5c2d33bf37905eea4852dbee72b9465665e6cdf1` |
| **Annotated** | 2026-10-03, one annotator, from rendered page images with the detector's claimed region outlined |
| **Corpus** | `development` split |
| **Detector under test** | PyMuPDF 1.28.2, `find_tables(strategy="text")`, with `strategy="lines"` as the paired comparison |

### Why it exists separately from the record that cites it

ENV-006 §2.1 published the tallies but not the page identities, so the conclusion
could be read and not re-tested. Thirty pages were rendered and ten annotated; the
blind key lived only in gitignored `artifacts/`, which means the asset was one
cleanup away from being unreproducible. §8 requires experiments to use shared
evaluation data, and tallies alone do not meet it.

Recovering it was possible here. It would not have been later, and re-annotating
costs the annotator's time rather than compute.

### Columns

| Column | Meaning |
|---|---|
| `n` | Position in the blinded, shuffled rendering the annotator saw |
| `doc` | Document short name, matching ENV-005 and ENV-006 |
| `page` | One-based page number |
| `stratum` | `A` = `text` claims a table and `lines` finds none, the stratum that decides ADR-003. `control` = both strategies agree a table exists |
| `text_regions` | Regions the `text` strategy claimed on that page |
| `lines_regions` | Regions `lines` claimed — `0` throughout stratum A by construction; empty for the control, which was not separately recorded |
| `verdict` | **1** the outline bounds one real table, roughly right. **2** a real table is inside but the outline merges several tables and/or swallows prose. **3** no table in the region at all |
| `real_tables` | Real tables the annotator counted on the page: a number, `many`, or `unknown` where the render showed only part of the page |
| `note` | The annotator's structural description of the page and the outline |

### What it reproduces

| Figure | From this file | ENV-006 §2.1 |
|---|---|---|
| Correctly bounded (`1`) | 0 of 10 | 0 of 10 |
| Wrong boundary (`2`) | 6 | 6 |
| No table at all (`3`) | 4 | 4 |
| Document spread | Infosys 5 / Ola 3 / HDFC 2 | same |
| Strata | 9 stratum A, 1 control | same |
| Control outcome | verdict `2` — failed | "the control failed too" |

### The `real_tables` column is the reusable part

`verdict` scores one detector's regions and is spent once. `real_tables` is truth
about the **document**, so any later detector is scorable against it without new
annotation: four pages carry no table and a correct detector must return nothing
there, and four more have an exact count. That is a necessary condition, not a
sufficient one — a detector can return the right number of regions with the wrong
boundaries — so count agreement screens candidates and `verdict`-style annotation
still decides.

### Round two lives in `region-boundary-review.tsv`

This file's `verdict` scores PyMuPDF's regions. The follow-up scores *both*
detectors on the six table-bearing pages, blind and paired.

### Limitations

Ten pages, one annotator, three documents, and **drawn almost entirely from one
stratum** chosen because it was maximally informative about `lines` versus `text`.
It is sufficient to reject a detector — 0 of 10 rejects at any plausible rate — and
**not** sufficient to rank candidates, estimate corpus-wide precision, or
generalise to a fourth issuer. Two pages rendered incompletely, so their
`real_tables` is unknown rather than zero.

Contains no filing text, financial values or quoted document content: the `note`
column describes page structure only (CLAUDE.md §10).

---

## `region-boundary-review.tsv`

Blind paired judgement of **two** detectors' table regions on the same pages —
the measurement ADR-003 named as its admission prerequisite, recorded in ENV-008
§2.5.

| | |
|---|---|
| **Rows** | 12 — six pages, each rendered once per detector |
| **Bytes** | 2,175 |
| **SHA-256** | `a2ac6f255237919e8cc461857f9aa811741a5c9f504d48d0a920ccfc7935feb5` |
| **Annotated** | 2026-10-04, same annotator as `detector-precision-annotation.tsv` |
| **Shuffle seed** | 20261004, as `scripts/render_table_regions.py` records |
| **Detectors** | docling 2.133.0 (TableFormer ACCURATE, layout-heron) and PyMuPDF 1.28.2 `find_tables(strategy="text")` |

### Why blinded and paired

Every page appears twice, once per detector, shuffled into one unlabelled set.
The candidate under test was the one the project would have preferred to succeed,
and an unblinded check in that situation is worth very little. It also re-tests
the annotator against their own earlier verdicts on the same pages: all six
PyMuPDF judgements came back identical to round one.

Pages holding no table are excluded. A detector returning nothing there has no
boundary to get wrong, and one returning a region is wrong by construction — both
already settled by the count screen.

### Columns

Same `verdict` scale as `detector-precision-annotation.tsv`. `regions` is how many
outlines that image carried; `real_tables` is carried over from round one.

### Result

| Detector | Correctly bounded (1) | Wrong boundary (2) | No table in region (3) |
|---|---|---|---|
| docling | **2 of 6** | 2 | 2 |
| pymupdf-text | **0 of 6** | 6 | 0 |

### What it overturned

A count-based screen scored Docling 8 of 8 against PyMuPDF's 2 of 8. **Three of
the four positive pages it passed had wrong regions**, including two where a
single region matched a single real table and yet contained no table at all —
prose in one case, an image in the other — while the real table went undetected.

Region count is a cheap filter for obviously-wrong candidates and **not evidence
about a plausible one**. Any later screen built on counts inherits that caveat.

### Limitations

Six pages, one annotator, two documents, one stratum. The verdicts are a
categorisation of the annotator's written descriptions rather than codes they
entered directly. Enough to decide against admission, not enough to rank
candidates or estimate any rate.

Contains no filing text or financial values: the notes describe page structure
and where outlines fall (CLAUDE.md §10).
