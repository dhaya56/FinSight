# ADR-003 — Table Detector: No Admission

- **Status:** accepted
- **Date:** 2026-10-03
- **Phase:** 6 — table detection and the source representation
- **Decision:** **no table detector is admitted to production.** PyMuPDF
  `find_tables(strategy="lines")` continues as the *provisional* detector for
  extraction into storage only. Detected table regions are **excluded from the
  retrieval path**. Admission waits on boundary measurement against real pages.
- **This is deliberately not a selection between the two PyMuPDF strategies.** The
  recorded evidence disqualifies both, so a choice between them would be a choice
  between two inadmissible options.

## Context

PROJECT_BLUEPRINT.md §12.12 reserves parser and detector admission for a recorded
evaluation; CLAUDE.md §4 requires developer approval for it and §8 forbids picking
a winner by intuition. ADR-002 deferred *parser* selection. This record covers
*detection*, which the work showed is a separable decision: one parser can host
several detectors, and the `TableDetector` contract makes that explicit in code.

Evidence: ENV-006 (real-corpus table validation, 1,403 pages) and ENV-007 (Docling
feasibility). Nothing here is measured for the first time; this record decides.

## What the evidence rules out

### The question was framed wrongly, and the measurement corrected it

ADR-003 was originally scoped to pick whichever strategy found more tables. Across
150 pages sampled evenly from three filings:

- `lines` never found a table `text` missed — **0 of 150 pages**.
- `text` fired on **145 of 150 (97%)**.

Financial filings do not carry a table on 97% of their pages. Recall is saturated
and uninformative, and the "complementary, route between them" conclusion drawn
earlier from a synthetic borderless fixture is **contradicted**. The decision
surface is precision.

### Precision disqualifies both strategies

Ten pages, human-annotated, blinded and shuffled with controls, judged on the
*region* rather than the page:

| Verdict | Count |
|---|---|
| **Correctly bounded table** | **0 of 10** |
| Real table inside, wrong boundary — merges several tables, or swallows prose | 6 |
| No table at all — prose, headings, a chart | 4 |

By the rule of three the 95% upper bound on the usable-region rate is ~26%, with
0% observed. Disqualifying wherever in that interval the truth lies. **The control
page failed too**, so the problem is not confined to pages `lines` rejects.

The mechanism was identified during annotation: these tables are ruled under their
headers and between sections, with **columns separated by alignment alone**.
`lines` requires a grid and so finds nothing — on five of nine pages carrying real
financial tables, including one page with four tables where it returned zero.
`text` finds them and cannot bound them.

**The missing capability is boundary segmentation of horizontally-ruled tables.**
It is not grid refinement, and no threshold or parameter on either strategy
supplies it.

### The routing-predicate evaluation is void, not negative

ENV-006 §3 measured cheap pre-filters to skip the detector on table-free pages,
and found the best 100%-recall predicate saved only 17.8% of pages. That was
recorded as a negative result. It is worse than negative: **the predicates were
scored against what `find_tables` detected**, and `find_tables` is what this record
disqualifies. The experiment measured agreement with a bad detector, so it
establishes nothing about routing and must be re-run against a detector that is
admitted. No routing predicate is adopted.

## Why this is not an admission of Docling

Docling supplies precisely the missing capability — explicit `col_span`, explicit
`column_header`, and correct bounds — and ENV-007 established that its empty-cell
omission is losslessly reconstructible because the spans are declared. On the
reconstruction-burden criterion it is a strong result, and it removes an error
class rather than shrinking it: ENV-006 measured the merge-versus-miss ambiguity at
mean 19% / median 11% of grid positions, with **436 of 861 tables (51%) above 10%**,
and that ambiguity is undecidable from a grid.

**But its detection precision on real corpus pages is unmeasured.** Everything
ENV-007 establishes about bounds comes from one synthetic page whose ground truth
we authored.

Admitting it on that basis would repeat a mistake this project has now made twice
and recorded both times: ENV-004 overstated producer throughput by ~3.5× from
synthetic fixtures, and a borderless-table fixture in this phase overstated
strategy complementarity so badly that the first real-document measurement
contradicted the conclusion outright. A third instance would mean the risk was
written down and then ignored.

## Costs recorded now, so admission is never argued on capability alone

| Cost | Figure |
|---|---|
| Pinned dependencies | **73 → 147 packages** |
| New heavyweight runtime | `torch==2.14.1+cpu`, `torchvision==0.29.1+cpu`, requiring a pinned CPU wheel index |
| Model and package footprint | **≈1.9 GB**, against ≈20 MB for the entire current PyMuPDF path |
| Throughput | **≈0.35 pages/s against ≈7.9 — roughly 23× slower.** The 1,403-page split extrapolates to ~68 minutes against ~3 |
| Provisioning | Five obstacles, none resolved by weakening a control. Docling is categorically not a drop-in: `do_ocr=False` and a pre-staged, verified model cache are prerequisites under §12.11, §20.6 and §11.7 |
| Auditability | torch and torchvision cannot be meaningfully audited at this project's level. Stated rather than glossed |

Two findings came out of building the adapter and are recorded here because
neither appears in ENV-007:

**Docling rewrites characters.** On one page, the document's eight U+2013 EN DASHes
came back as ASCII hyphens and both U+20B9 RUPEE SIGNs vanished, leaving no
non-ASCII character in any cell. In a financial table the en dash is the nil
marker, so flattening it to a hyphen destroys the difference between "no such item"
and a negative sign — and §14.9 citations are character offsets into stored text,
which must therefore be the document's own. The adapter takes **structure from
Docling and text from PyMuPDF** for this reason. That is a mitigation, not a free
win: it is a permanent design constraint, and it means two independent PDF engines
read the same bytes (ENV-007 open item 5).

**Docling reports dropped cells only in a log line** — `"N of M pdf cells matched
neither a row nor a column band ... and were dropped"` — with no API surface. The
adapter installs a log handler to capture it. A detector whose content loss is
reported only to a logger is a silent failure by default, and that property is a
cost of the library, not of our wiring.

## What was built so that non-admission is survivable

- **`TableDetector` contract with injection.** `PyMuPdfProducer` takes a detector;
  nothing hard-codes one. Changing detector is a constructor argument.
- **The Docling adapter exists, is tested, and is selected by nothing.** It is
  reachable the moment a measurement justifies it.
- **`source_elements.extraction_method` is per element.** A run routing different
  pages to different detectors is already representable with no schema change.
- **The quality verdict.** Each table carries `accepted` / `review_required` /
  `rejected` with its reasons and signals, so an imperfect detector's output is
  refusable per table instead of trusted wholesale. This is what makes continuing
  to extract with an inadmissible detector defensible rather than reckless.

## Operative constraints while no detector is admitted

1. **Table cells are stored and citable (§17.1) and must not be indexed, embedded
   or ranked in retrieval.** Indexing the current regions would embed chunks that
   swallow paragraphs or bisect tables.
2. **Narrative-first retrieval (§15) is therefore the evidence-backed sequence**,
   not merely the convenient one.
3. **No threshold on the quality signals gates anything** (CLAUDE.md §9). The
   verdict rests only on structurally unambiguous conditions.

## Consequence for the rest of Phase 6

The remaining table-semantics work presupposes that a table's **boundary** is
right, and 0 of 10 measured regions were.

- **Row-role tagging** (subtotals, totals, formula rows) operates inside the
  region. Where a region merged four tables, "the total row" is four total rows.
- **Adjacency** — units above the table, caption above, footnotes below — is the
  clearest case. "The units declaration sits in the line above the table" has no
  meaning when the region's top edge is wrong, and this is the commit aimed at the
  88% of tables with no visible units declaration.
- **Multi-page stitching** joins region to region, so it inherits both regions'
  boundary errors and compounds them.

This does not make that work wrong, and it is not an argument for skipping it. It
is an argument for recording what it rests on: each of those commits is **work on
provisional geometry**, and their output must be re-validated against whichever
detector is eventually admitted. The alternative sequencing — admit a
boundary-capable detector first, then build semantics on it once — is cheaper if
the admission is close and more expensive if it is not. **That sequencing choice is
the developer's and is not decided here.**

## What would trigger admission

Detector precision measured on **real corpus pages against human-verified truth**,
for a detector that reports table bounds. The threshold is not set here: CLAUDE.md
§4 reserves it for developer approval, and inventing a number would be exactly the
intuition §8 forbids. ENV-006 open item 1 and ENV-007 open item 1 own the
measurement.

Admission would also need the §20.6 artifact manifest pinned to commits rather than
a branch, and peak memory measured against §41.11's shared 15.7 GB envelope.

## Relationship to ADR-002

ADR-002 stands. Its provisionality was always that §12.2/§12.4/§12.9 routing did
not exist, not that PyMuPDF was suspect — and as a *text* producer the evidence
strengthened it (184–220 pages/s, clean text, geometry correct after the rotation
fix). What this record disqualifies is its **table detection**, which is a
different capability of the same library. Parser admission remains ADR-004's.
