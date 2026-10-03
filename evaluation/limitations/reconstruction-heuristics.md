# Reconstruction Heuristics — Limitation Register

**Status:** living document. Updated whenever a reconstruction rule changes.

## Why this exists

Table semantics — which rows are headers, which cells are merged, which row states
the units, which characters are a footnote marker, how rows nest — are *not*
supplied by any detector in use. They are reconstructed above the detector layer
in `src/finsight/extraction/tables/structure.py`.

That reconstruction is heuristic. It is deliberately **not** recorded as an
architectural decision: an ADR asserts a choice made on evidence, and these rules
have been tried against synthetic fixtures and one corpus of three documents.
Calling them decisions would claim a confidence they have not earned.

This register is the alternative. Every rule appears here with the misfire it is
known to have and whether that misfire's rate has ever been measured. A rule whose
limitation is unmeasured is not thereby acceptable — it is an open item with a
stated owner phase.

**Two reminders from this project's own history.** A defect can affect thousands
of extracted objects while every test passes — the rotation defect reached 2,401
block boxes that way. And a synthetic fixture can misrepresent real behaviour
while validating the plumbing perfectly: ENV-004 overstated producer throughput by
roughly 3.5x, and a borderless-table fixture overstated strategy complementarity
so thoroughly that the conclusion drawn from it was contradicted by the first
real-document measurement. Nothing in this register should be read as settled
because its tests are green.

---

## 1. Row roles — header, section, units, data

**Rule.** Each row takes a role from its *shape*, because the first column is the
row-label column and a header has no row label:

| Shape | Role |
|---|---|
| first column empty, something beyond it | header continuation |
| first column filled, everything else empty | **section label** |
| first column filled, something beyond it | data — the header block ends |
| matches the units vocabulary | units declaration |

Row 0 is a header unless it is the only row. Section rows are found across the
whole table, not just above the first data row, because a balance sheet alternates
sections and line items all the way down.

**This replaced a numeric rule, and the replacement was forced by the corpus.**
The previous rule required a data row to contain a *magnitude*. That assumption
came from a fixture that happened to be a numeric financial statement, and manual
review of real pages broke it three ways, all common and all silent:

- **A section label row** — "Assets" above the asset line items — holds no figures,
  so it read as a header and was discarded. Every line item beneath it lost the
  statement section it belongs to, which in a balance sheet is the asset/liability
  distinction itself.
- **A wholly non-numeric table** — governance policies and owning committees, lists
  of directors — contains no magnitude anywhere, so **every row** read as a header.
  The table carried no header paths and no row labels at all. Financial filings are
  full of these.
- **A Yes/No or tick-mark compliance table** failed identically.

The shape rule also removes the need for the bare-year test the old rule depended
on: `Particulars | 2025 | 2024` now resolves because row 0 is a header and the row
beneath carries a label *and* values, whatever those values are.

**Known limitation.** A table continued from a previous page (§12.8) opens with
data and no header, and nothing in a grid distinguishes that from a header row.
Joining continued tables is already out of scope, so this is recorded rather than
guessed at.

**Measured?** The three failure shapes are now pinned by tests taken from real
pages. The *rate* at which the continuation limitation bites is unmeasured. Owner:
Phase 6 ground truth.

---

## 2. Merged-cell span inference

**Rule.** A populated cell followed by consecutive *absent* cells spans them.
Absent means the detector reported no cell at that position; an empty cell, which
has a box and empty text, is a different fact and does not trigger a span.

**Known limitation — structural, not a tuning problem.** A genuine merge and a
detector that simply failed to find a cell produce an identical signal. This is
**undecidable** from a grid. No refinement of this rule can separate them.

**Consequence.** A detection failure silently becomes a span, which silently
widens a header's reach to a column it does not describe.

**Mitigation.** Not a better heuristic — a parser that reports spans directly.
This is the clearest single argument for the reconstruction-burden criterion in
ADR-004.

**Measured — and it is not a corner case.** Across the 861 tables the `lines`
strategy found in the development split, the share of grid positions reported as
absent had **mean 19% and median 11%**, and **436 of 861 tables (51%) had more
than 10% of their positions absent**.

So for the majority of real tables this ambiguity applies to a non-trivial part of
the grid. Every one of those positions is either a merge, which the span records
correctly, or a detection failure, which the span silently papers over — and
nothing available at this layer can say which. This is the strongest quantitative
argument in the project for preferring a parser that reports spans directly.

Owner: ADR-004.

---

## 3. Units detection and scoping

**Rule.** A preamble row whose text names a scale word (crore, lakh, million,
billion, thousand) or a currency token, and contains no digits, is a units row and
is recorded verbatim. Scope follows position: a declaration in the label column
governs the table; one above a specific column governs that column and any columns
its span covers; the most specific wins.

**Why position decides scope.** §17.3 requires table-level *and* column-level unit
context. A scale note above one column, applied table-wide, misscales every other
column — and a table mixing crore figures with percentages is ordinary.

**Known misfire.** A **row**-level scale change is not detected. A "Margin %" row
among crore figures inherits the table's declaration, which is wrong for that row.
Detecting it requires reading the cell's own content as a unit, and reading content
is §16's work rather than extraction's — so extraction records the declared scope
and §16 must be able to refuse a value whose scale it cannot place.

**Measured — and the bigger problem is not scoping.** Of the 861 tables found in
the development split, only **104 (12%) carry a units declaration this rule can
see**, and 31 (4%) show a mixed-scale signature.

That 12% is the finding. Either financial tables mostly declare their scale
*outside* the table — in a section heading, a column header above the detected
region, or a page-level note — or this rule misses the forms they use. Both
readings are bad: a cell whose `units` is NULL is a figure whose scale is unknown,
and §16 must refuse to normalise it. 88% of tables currently produce such cells.

**Answered by manual review: the declaration usually sits *outside* the detected
table region.** On the pages inspected, `(In ₹ crore)` appears as a small
right-aligned note *above* the table's first row, as a separate text block, not as
a row of the table. The rule only inspects rows **inside** the detected region, so
it cannot see it — which explains the 12% directly, and means the rule is not at
fault and should not be tuned further.

**Consequence.** Units detection has to look above the table region, not only
within it. That is the same "text adjacent to a table" machinery that caption
derivation and footnote binding need — above, above, and below respectively — so
all three should land together rather than being solved three times.

Until then a cell's `units` is NULL for most tables, and §16 must refuse to
normalise a figure whose scale it cannot establish. Owner: the caption and footnote
commit.

---

## 4. Footnote marker separation

**Rule.** A trailing `(x)` with one to three alphanumeric characters, or a run of
`*`, `†`, `‡`, **preceded by other text**, is a footnote marker. It is recorded in
`footnote_refs` and the cell's `text` is left byte-identical.

**Why "preceded by other text" is load-bearing.** `(45)` and `(1,234)` are
*negative values*. A pattern matching a trailing parenthesis without requiring a
body would read the parentheses as a marker and discard the sign — the worst defect
available in a financial table.

**Why text is never rewritten.** Citations are character offsets into the stored
string (§14.9). Separation is additive metadata, not a transformation.

**Vocabulary: measured, and the first version was wrong.** Marker forms counted
across the whole development split, as trailing markers:

| Form | Count | | Form | Count |
|---|---|---|---|---|
| `(digit)` | 937 | | `**` | 39 |
| `(alpha)` | 450 | | `***` | 6 |
| `*` | 361 | | `##` | 1 |
| `#` | **56** | | `###` | 1 |

**`#` was missed entirely** by the assumed pattern — 56 occurrences silently
dropped. Fixed, with a test that pins every measured form so the set cannot
regress to guesswork. No superscript digits appear anywhere in the corpus, and
neither do `†` or `‡`; those two are retained because other filings use them, but
their validation here is zero.

**Resolution target: confirmed to exist, still unbuilt.** The same pass counted
*leading* markers, which is what a footnote's own text line looks like:
parenthesised letters 472, parenthesised digits 426, `*` 103, `#` 23, `**` 20,
`***` 3 — roughly a thousand candidate footnote text lines.

So the notes are present and mechanically findable. A marker still resolves to
nothing: there is no footnote element, no footnote text, and no link. A number
released without its qualifier is wrong, not merely incomplete. Owner: footnote
commit.

---

## 5. Row-label hierarchy from indentation

**Rule.** Row labels form a path. Nesting comes from `text_left` — where the
label's text begins — using a stack that unwinds when a label starts at or left of
the previous one. Tolerance 1.0 pt. Header rows and units rows are excluded from
the stack.

**Why `text_left` and not the cell box.** In a ruled table every first-column cell
shares the ruling line's left edge. Measured on the project fixture, three label
cells all had box left edge 60.0 while their text began at 64.0, 72.0 and 64.0 —
the 72.0 being the only record that "Of which: term deposits" is a component of the
line above rather than a peer of it. Reading indentation from the box produced a
flat path for a visibly nested table.

**Why units rows are excluded.** A scale note drawn left of the line items would
otherwise become their indentation parent and appear as the outermost label on
every row in the table. This was a real defect, found by a test written to look
for it.

**Known misfires.**

- A table that expresses hierarchy by **typography** rather than indentation — bold
  totals, italic sub-items — reads as flat.
- A label that wraps onto a second line may report a different `text_left`.
- The 1.0 pt tolerance is a guess, not a measurement.

**Measured?** No. Owner: Phase 6 ground truth.

---

## 6. Numeric recognition

**Rule.** Strip currency tokens, then strip separators, signs, percent and
parentheses, including the non-breaking space, Unicode minus and en dash that
filings use. What remains must be digits only.

**Why the homoglyphs are deliberate.** The character class exists *because* filings
use a non-breaking space as a group separator and a Unicode minus where a hyphen is
expected. The lint rule that objects to them is suppressed with that reason
recorded.

**Known misfire.** A date (`31.03.2025`) or a footnote-only cell may read as
numeric. Used only to classify rows, not to extract values, so the consequence is
a misplaced header boundary rather than a wrong number.

**Measured?** No.

---

## 7. The quality verdict

**Rule.** Every assessed table receives `accepted`, `review_required` or
`rejected`, in `src/finsight/extraction/tables/validation.py`. Rejection is
reserved for conditions that are unambiguous whatever one's threshold: no cell
holds text; fewer than two rows or columns; every character sits in a
sentence-shaped cell. `dropped_cells >= 1.0` is review, not rejection. Four
graded signals — `prose_ratio`, `numeric_ratio`, `filled_ratio`,
`unassigned_words` — are measured and stored and **gate nothing**, because
CLAUDE.md §9 keeps threshold gates informational until an approved baseline sets
them.

**Why it is here and not in an ADR.** The verdict is the mitigation that makes
ADR-003's non-admission survivable, but the rules themselves are heuristics with
exactly the status of the five above.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| A region merging several real tables, or one swallowing prose beside a table, is **accepted** — it has text, a grid and numerals | The dominant observed failure (6 of 10 annotated regions) is the one the gate does *not* catch. It catches degenerate and empty regions, which were 4 of 10 |
| `prose_ratio >= 1.0` requires *every* character to sit in a sentence-shaped cell | A region that is 90% prose with one figure in it is accepted. Deliberate: a note disclosure looks the same, and refusing it on an uncalibrated ratio would discard real financial content |
| `numeric_ratio` counts row-label cells in its denominator | It moves with column count as well as numeric density, so a well-formed two-period statement caps near 0.67. Pinned by a test; a calibration must account for it |
| A 12-word prose cutoff is a description of financial row labels, not a measured boundary | A genuinely long row label reads as prose |

**Measured?** Partly, and the result is bad. ENV-008 §2.5 compared the gate's
verdict against human boundary judgement on six real pages:

| Boundary verdict | Gate said |
|---|---|
| **1** — best page of the six | all four regions `review_required` |
| **1** | `accepted` |
| **2** — every outline straddles a table boundary | all four `accepted` |
| **3** — region holds no table, an 18×8 grid over two-column prose | `accepted` |
| **3** — region holds no table, a 14×14 grid over an image | `accepted` |

**On this sample the gate's signal runs against boundary quality.** It accepted
both regions containing no table and flagged only the page judged best.

Why, mechanically: a prose region segmented into an 18×8 grid has short cells, so
no cell reaches the twelve-word prose threshold and `prose_ratio` stays low. The
rule was written for a region that is *one paragraph in one cell*, and a detector
that shreds a paragraph into a grid defeats it completely. The `review_required`
on the best page came from dropped cells, which is honest reporting by a detector
doing hard segmentation well — so the one page where a detector worked hardest
looked worst.

Six pages cannot establish a rate. They are enough to establish direction, and to
retire the hope the gate compensates for a weak detector. **No consumer may read
`accepted` as evidence that a region bounds a table.** The gate's useful scope is
what ADR-003 claims for it and no more: refusing structurally impossible tables,
and carrying signals forward for a calibration that has not happened.

---

## 8. Region support from ruling lines

**Rule.** `src/finsight/extraction/tables/regions.py`. A page's horizontal ruling
lines are grouped into bands separated by more than 40pt. A proposed region
containing none of the page's rules, **on a page that draws rules**, is refused —
`verdict=rejected`, reason `region_has_no_ruling_lines`, and a coverage gap so the
run becomes `partial`. A band of three or more rules that no surviving region
covers is reported as a missed table.

**Why 40pt.** Measured on five real pages carrying 103 rule gaps: within-table row
spacing is **13.9pt at both the median and the 75th percentile**, while gaps
separating two tables measured 44.5pt and up. 40pt sits in the empty space between
the two populations. It is used only to count distinct ruled areas, never to decide
a table's extent.

**What it fixed, measured on the six judged pages (ENV-008 §2.5).** One region
refused — Infosys p.300's 18×8 grid over two-column prose — and three missed ruled
areas reported, including the part of Infosys p.234's second table that the
annotator independently observed was enclosed by nothing. **No change and no false
alarm on either page judged correctly bounded**, and none on the unruled page.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| A region grazing a single rule is supported | HDFC p.279's spurious region clips one rule and survives, while the page's real table goes undetected. A density threshold would catch it and no measurement supports one, so the region stays and the missed band is reported alongside it |
| An unruled page disables every check | Infosys p.140 carries real tables and draws no rules. Correct — the dominant presentation in this corpus is partially ruled — but it means borderless pages get no protection at all |
| A band is not a table's extent | **Deliberate.** An earlier design snapped region edges to contained rules. Measurement killed it: these filings rule under headers and between sections, so Infosys p.234 has a region spanning y 501-587 whose rules occupy only y 562-576. Snapping would have cut the table to a third while looking like a fix |
| Three rules is a floor, not a finding | A reported miss is a prompt to look, not a count of lost tables |

**Measured?** On six pages, against human-verified boundary judgement. Enough to
show it removes a real false positive without disturbing correct regions; far too
small for a rate. It does **not** address the row-level clipping that affects every
region on every page judged, which remains the dominant defect.

---

## 9. Aggregate-row tagging

**Rule.** A row whose first-column label contains `total`, `sub-total`,
`sub total`, `subtotal`, `grand total` or `aggregate` — matched case-insensitively
on word boundaries, longest form first — is tagged `is_total` on **every cell of
the row**, not only its label. Header rows and units rows are excluded, so a
"Total" *column* heading cannot tag its whole row.

**Why every cell.** The consumer that must not double-count holds a *value*. A
total's figure is indistinguishable from a line item's; the distinction is the row.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| **It under-reads badly.** "Profit before tax", "Gross profit", "EBITDA", "Net cash from operating activities" are all aggregates carrying none of the vocabulary | `is_total=False` is **not** evidence that a row is a line item. Any consumer summing a column is only partly protected |
| A ratio row such as "Total debt to equity" is tagged | Tagged as an aggregate when it is a derived ratio. Over-tagging costs a row's exclusion from a sum; under-tagging double-counts, so the asymmetry is deliberate |
| No distinction between a total and a subtotal | A statement with nested aggregates marks them all alike. Separating them needs the arithmetic below |
| English only | A filing in another language is untagged entirely |

**Measured?** No. Prevalence of the vocabulary across the corpus is uncollected,
and the rate at which untagged rows are nonetheless aggregates — the dangerous
direction — is unknown.

**Arithmetic verification is deliberately not implemented.** Checking that a
tagged row equals the sum of the rows above it would resolve both the under-reading
and the total-versus-subtotal distinction, and would do so deterministically with
`Decimal`. It is not done because CLAUDE.md §7 permits a non-ledger number to be
reproduced only when precisely source-bound and forbids normalising, comparing or
calculating it **without an approved structured path**. Summing extracted figures
to classify a row is such a calculation. The capability is worth having and needs
the developer's approval and an approved path, not an implementer's judgement that
it is probably fine.

---

## 10. Not implemented, and recorded so absence is not read as a finding

| Gap | Consequence if forgotten |
|---|---|
| **Caption derivation** | `source_tables.caption` is always NULL. NULL currently means "not looked for", which is the opposite of what a reader would assume. No consumer may treat it as evidence a table is uncaptioned |
| **Footnote resolution** | `footnote_refs` points at nothing |
| **Figure-region marking** | Chart axis and data labels extract as ordinary text. Measured across the full split: **841 of 1,403 pages (60%)** carry more than 40 drawing items, but only **50 (4%)** also show five or more bare-numeric short blocks. So heavy vector content is the norm and is mostly design furniture; the chart-label signature is real but confined to about 4% of pages |
| **Table-aware reading order** | Measured at 14% page divergence; tables now make a table-aware ordering possible but it is unbuilt |
| **Duplicate text between blocks and cells** | Text inside a table is stored twice, once as a block and once as cells. Deliberate — suppressing the blocks would let a false-positive table delete narrative prose, and §18 requires exclusion to be a reversible ranking decision. §18.4's table-aware chunking must avoid retrieving the same sentence twice |
