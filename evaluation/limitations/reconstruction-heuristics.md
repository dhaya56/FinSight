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

## 10. Footnote binding

**Rule.** `src/finsight/extraction/tables/adjacency.py`. For each marker a table's
cells carry, scan text blocks below the table — stopping at the next table, bounded
to eight blocks, requiring 30% horizontal overlap — and bind the first line that
*opens* with that marker. The footnote becomes a `footnote` element, a child of the
table, with its text stored verbatim including the marker.

**Binding is by marker, not by distance.** Taking the nearest line below would be
unsafe: ENV-008 §2.5 measured regions mis-bounded by a row or more, so "just below"
is routinely another table's content. Matching the marker means both ends supply
evidence.

**Measured on the twelve judged regions.** Four leading-marker lines sat below a
table; two matched a marker their table used and bound correctly; two did not and
were left alone — both belonged to a table two regions away whose boundary was
wrong. **Under-binding is the designed failure:** an unattached footnote is a
visible gap, a misattached one silently changes what a figure means.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| A footnote separated from its table by another table is never found | The measured case: two real footnotes unbound. Recovering them needs correct boundaries, not a longer scan |
| A footnote printed at the page foot rather than under its table is out of range | Unmeasured frequency. ENV-006 counted ~1,000 leading markers corpus-wide against the handful reachable here |
| Only the first line of a multi-line footnote binds | PyMuPDF blocks usually hold the whole note, but a note split across blocks keeps only its opening |
| The marker vocabulary is the cell-side one | A footnote using a form no cell uses is unreachable by construction, which is the point |

**Measured?** On twelve regions: 2 bound, 2 correctly refused, 0 misbound. Far too
small for a rate, and the denominator — how many footnotes exist on those pages —
was not counted.

---

## 11. Units and captions from adjacent text — measured and not built

**The 88% units gap is not explained by text above the table.** Scanning the three
nearest blocks above each of the twelve judged regions found **zero** units
declarations. Not one. A rule reading units from the line above would never fire on
this evidence, so it was not written.

What sits above a region instead is, repeatedly, **the table's own clipped
content**: `'Particulars Gratuity Pension'` 0.2pt above one region, a header row
41.9pt above another, a data row `'Balance as of March 31, 2025 600 3,348 …'`
34.4pt above a third.

That also rules out caption adjacency for now. In these filings a caption is a
lead-in sentence rather than a labelled title, and the nearest block above is as
likely to be a header the detector clipped off. Attaching it as `caption` would
produce a confident, plausible, wrong value — so `source_tables.caption` stays NULL
and still means "not looked for".

**Both become tractable if region boundaries are fixed, and not before.** The
remaining explanations for the 88% — declared in a distant section heading,
declared inside the table in a form the rule misses, or genuinely absent — need
ground truth to separate, and that is ADR-003's measurement, not a rule to tune.

---

## 12. Table continuation

**Rule.** A table is marked as continuing a statement when the nearest heading
above it on the page contains a *parenthesised* continuation marker — `(contd.)`,
`(cont'd)`, `(continued)`. The heading text before the marker is stored verbatim
as `source_tables.continuation_of`.

**Why the brackets are the whole rule.** Scanning the development split for the
bare word found **215** occurrences, essentially all ordinary prose: "continued to
mature the technology", "our continued commitment". Requiring the parenthesised
form found **98**, and every one was a real continuation heading. No false positive
in either document that carries them.

**A title, not a link.** The document *states* what is continued. Which stored
region holds the earlier part is an inference across regions ENV-008 §2.5 measured
as frequently mis-bounded, and a foreign key would present that inference as
provenance. Joining the content into one logical table is a retrieval
representation (§18.4), not a source one.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| **One development filing declares none at all.** HDFC: 0 markers in 590 pages | NULL is **not** evidence a table stands alone. Whether continuations are declared is a publisher's house style, so recall across issuers is unknown and probably poor |
| The heading is page-scoped, so every table beneath it on that page takes the same title | Correct for a continued statement split into regions; wrong if an unrelated table shares the page. Unmeasured |
| Only the nearest marked heading is used | A table under a continued note inside a continued section records the note, losing the section |
| A continuation declared by a repeated header rather than a marker is invisible | The §12.8 case ENV-006 noted in Ola and never quantified |

**Measured?** The marker's precision, yes — 98 of 98 across two documents. Its
*recall* against tables that genuinely continue, no: that needs the ground truth.

**Two defects this found in the adjacency scan**, both from real pages:

- **Overlap was measured against the table's width alone.** "Balance Sheet
  (contd.)" is about a fifth as wide as the statement it heads, so a correct
  heading failed a test asking it to span a third of the table. Now measured
  against the narrower of the two boxes.
- **A heading was required to end above the table's top.** A clipped region can
  begin partway through its own heading, which is exactly what one real page did.
  Now the heading only has to *start* above.

---

## 13. Heading recovery during chunking

**Rule.** `src/finsight/chunking/headings.py`. §18.2 attaches a heading path to
every chunk, and nothing upstream supplies one — `extraction/contracts.py` states
that a producer "makes no claim about document structure; a heading is a block that
happens to be large, not a heading." So sections are recovered at chunking time
from the numbering filings use. A block is a heading only if **all** of:

1. it matches `N.`, `N.M`, `N.M.K`, or `Item|Note|Annexure|Schedule|Part|Section N`
2. the number carries a **dot** — `3.` or `3.2`, never a bare `3`
3. the title begins with a **capital letter**
4. the collapsed text is **≤120 characters**
5. it does not sit adjacent to another candidate **at the same level**

**Every one of those came from a measured false positive**, in this order:

| Added | Because the development filing produced | Detections |
|---|---|---|
| — | first attempt | 647 |
| the dot | `3 Infosys`, `4 EdgeVerve`, `5 Infosys Public` — 20 consecutive rows of a **subsidiary table** read as sections | 341 |
| run suppression | `1. …`, `2. …`, `3. …` in adjacent blocks — a **CSR projects table** | 264 |
| capitalised title | `7.6 years`, `63.39 64.50`, `4.7 6.1` — **decimal measurements** | **246** |

A separate defect was fixed alongside: the depth formula counted dots, so `7.` and
`7.2` both resolved to level 2 and 280 of 341 candidates were misfiled as
subsections. A trailing dot is punctuation, not depth.

**The bias is deliberate and asymmetric.** Missing a heading merges two sections —
a longer section, slightly less context. Inventing one attaches a *wrong* heading
path to every chunk after it, and a chunk claiming to come from "Risk Factors" when
it comes from the notes is worse than one claiming nothing.

**Known misfires.**

| Misfire | Consequence |
|---|---|
| **A heading merged into the preceding block is invisible.** PyMuPDF yields `3 Michael Gibbs Member 4 4 3. Web link(s) for composition of C…` — a table row and the next heading sharing one block | No rule on whole blocks can recover it; the block genuinely contains both. The section boundary is lost |
| A heading written without a dot, or whose title opens with a digit or lowercase brand name | Not detected. Deliberate — see the bias above |
| An isolated numbered footnote under a table, e.g. `1. Investments exclude investments in subsidiaries` | Still read as a heading. Run suppression needs a neighbour and this has none |
| Table rows escape the `table_derived` guard whenever the detector missed the table | ADR-003 measures the detector at 2 of 6 regions, so this is the normal case, not the exception |

**Measured?** Precision, no — the 246 detections have not been checked one by one
against human judgement. What *is* measured is the removal of three specific
false-positive classes, each against the text that produced it, each pinned by a
test. Heading-path accuracy belongs with the Phase 7 retrieval ground truth.

---

## 14. Boilerplate demotion — measured and deliberately not implemented

§18.8 permits excluding page furniture from ranking "when detection is reliable".
On this corpus it is not, and the measurement is unambiguous. The most repeated
block texts in a 369-page filing are:

| Repeats | Text | What it actually is |
|---|---|---|
| **159** | `(In ₹ crore)` | **the units declaration** |
| 152 | `2025 2024` | column headers |
| 58 | `Particulars As at March 31,` | table header |
| 30 | `Total` | a total row label |
| 29 | `Accounting policy` | a heading |

A repetition rule would demote the **units declaration** — the single value whose
loss puts every figure in the document out by a factor of ten million (§17.3). A
position rule fares no better: `taxation(1)` sits in the top margin and is content.

So nothing is demoted, and §18.8's condition is simply not met. Revisit only with a
signal that separates running headers from repeated financial labels; repetition
and page position are both measured to be insufficient.

### One case where detection *is* reliable, found later and still not acted on

The argument above is about *repetition*. Measured on the indexed corpus, a different
signal separates cleanly — how much of a chunk is letters at all:

| Active child chunks | Count | Share |
|---|---|---|
| Total | 4,969 | — |
| Fewer than 50% letters | 760 | **15.3%** |
| Fewer than 20% letters | 282 | **5.7%** |
| Containing a run of dot leaders (`.........`) | 100 | **2.0%** |

The 5.7% are flattened numeric tables and table-of-contents lines, and the largest are
**384-token chunks of nothing but `.` characters** — embedded into 768 dimensions,
given BM25 weights, and competing in retrieval against real prose. Two verbatim
examples, both at the chunk-size ceiling:

> `......................................................................`
>
> `B Consolidated Balance Sheet..........................................`

Unlike repetition, this signal does not threaten the units declaration: `(In ₹ crore)`
is 62% letters. A rule demoting chunks that are almost entirely punctuation would
catch the dot leaders and leave the financial labels alone.

**Not acted on, and the reason is sequencing rather than doubt.** Demotion is a
*ranking* decision §18.8 governs, the numeric chunks in the same band are real content
that must stay retrievable (§17.8 wants large-table context recovered, not dropped),
and separating "dot leaders" from "a dense numeric table" needs a threshold §4 reserves
for the developer. It is also a re-chunk and a 43-minute re-index. Recorded here with
the measurement so the decision is available rather than rediscovered.

---

## 15. Chunk assembly

**Rule.** `src/finsight/chunking/chunker.py`. Blocks are **joined**, not split —
measured on a development filing the median block is 38 characters and 52% are
under 40, so a block is a line fragment and accumulation is the main path.
Splitting is reserved for the rare block over the budget.

Two invariants are enforced on every run by `verify_coverage`, not merely tested:
**every block reaches exactly one child chunk**, and **text is preserved
verbatim**. Measured on a 369-page filing: 14,669 blocks → 1,303 children, source
chars 1,213,915 → chunk chars 1,227,231, a ratio of **1.0110** which is exactly the
newline join separators.

### Five defects an adversarial audit found after the first pass

All five passed the original suite. Recorded because the pattern matters more than
the individual bugs: worked examples agreed, invariants did not.

| Defect | Why it was invisible |
|---|---|
| **Fabricated adjacency.** Grouping a section by evidence type joined `Intro paragraph` and `Closing paragraph` into one chunk when a table stood between them in the document | The output reads as correct prose. Nothing downstream can detect that the two were not contiguous. Fixed by emitting *runs* of consecutive same-type blocks |
| **Two adjacent headings both suppressed.** `7. Risk factors` then `8. Other matters` — a section whose body starts on the next page — were read as a list and both lost | List-run suppression needed a threshold of three, not two. Every chunk beneath both sections had lost its heading path |
| **Parent cap not enforced** when a single line exceeded it: a parent came back at 50 tokens against a cap of 20 | The code kept the first line unconditionally. A section opening with one long paragraph is the common case, not a corner |
| **A single word longer than the budget** was emitted whole, 500× over | Pathological input only — a URL, an unbroken digit run — and the embedding model truncates it silently, losing the tail |
| **Unordered input silently scrambled.** The document-order contract was documented and unchecked | A caller reading rows without `ORDER BY` would assemble text that reads as prose and is not. Now refused rather than repaired, so the caller's bug stays visible |

A sixth finding was waste rather than error: **527 of 680 parents were
byte-identical to their only child**, so §20.8 expanding from that child returned
the same text. Those parents are no longer emitted; 680 became 153.

### Known limits

| Limit | Consequence |
|---|---|
| `page_numbers` is a sorted set | A chunk spanning pages 7, 8 and back to 7 records `(7, 8)`. The excursion is not recoverable |
| A split block's pieces all cite the **whole** block | Deliberate: the source representation addresses blocks, not sub-blocks, and a narrower citation would name an address §14.9 cannot resolve |
| `verify_coverage` permits repeats for any chunk marked `split_oversize_block` | An unrelated duplicate involving the same element id would be masked. Narrow, and the alternative is tracking piece identity the source representation does not have |
| Child and parent sizes are **unmeasured** | §18.12 requires selection on development data. The current values are starting points carried with a config version, not choices |

---

## 16. Multi-column reading order, and what it does to chunks

**Measured, confirmed, unfixed.** ENV-005 measured positional reading order
diverging from a column-aware ordering on **195 of 1,403 pages (14%)**. That was
recorded as a sensitivity figure. Chunking makes the consequence concrete, because
interleaved blocks are now joined into one embedded string.

A stored chunk from a real filing, pages 19–20:

> `- Insurance Services 2024 in North ■ Recognized as a leader in CapioIT APAC
> Salesforce SI and Solutions Providers Ecosystem Capture Share Report, 2025 ■
> Infosys BPM won the Avasant Digital Masters Award 2024 … Assessment 2024
> Services, Q1 2025 America, A`

"in North" and "America, A" are the two ends of one phrase, separated by a column's
worth of unrelated text. The chunk reads as prose, embeds as prose, and is not.

**The rate is not 14% of chunks and is not yet known.** A crude detector — any
chunk whose source blocks start on both halves of the page — flags 67%, but it
cannot distinguish interleaving from a full-width table, a right-aligned figure or
a legitimately wide layout. The honest figure remains ENV-005's page-level 14%, and
a chunk-level rate needs the column-aware comparison §35.4 describes.

**The fix belongs in extraction, not here.** `reading_order_key` returns
`(y0, x0)` with no column awareness, and `reading_order.py` records why: any
tolerance band is a threshold, and §4 reserves thresholds for development data.
Chunking cannot repair an order it is handed. Recorded as the largest known
quality defect in the retrieval path.

---

## 17. Deterministic enrichment does not reach the embedding

Chunks store `heading_path`, `page_numbers` and `evidence_type` as columns, and
`text` verbatim. §14.4 requires that — no enriched text may be presented as
original evidence, and a citation resolves to the source region.

But §14.2 says a retrieval representation "adds deterministic context useful for
lexical and dense search", and §14.6 permits issuer, document type, heading path,
page, period, basis, currency, scale and caption. **Today the embedded string is
the `text` column alone**, so none of that context reaches the vector. A chunk
reading "This was primarily due to increased cost of goods sold" embeds with no
indication of which company, filing, period or section it belongs to.

**Decision, recorded here before the indexer is written:** the indexer composes the
embedded string from the deterministic context *and* the text, while `text` stays
verbatim in the database for citation. That keeps §14.4 — the enriched form is what
search matches, never what a citation returns — and satisfies §14.2 and §14.6.

Not done in the chunking commit because the composition belongs where embedding
happens, and doing it here would mean storing the enriched string and losing the
verbatim one.

### Implemented — `src/finsight/indexing/enrichment.py`

The embedded string is now labelled context, a blank line, then the chunk verbatim:

```text
Issuer: HDFC Bank Limited
Document: annual report
Period: FY2024-25
Basis: both
Currency: INR
Section: Directors' Report > Capital Adequacy
Page: 42

<chunk text, unchanged>
```

Nothing stores it. It is built, embedded and discarded, so there is no path by
which it can be quoted back to a reader as evidence.

**Two deliberate omissions, both recorded so they are not read as oversights.**

| Omitted | Why |
|---|---|
| `units_as_presented` | §14.6 permits scale, but one corpus entry records it as "INR crore, lakh and million mixed within one document" — a hazard description, not a unit. Embedding that into every chunk of the document would add 60 characters of noise and assert a scale the document does not have. Currency is included; it is a single stable token |
| A value for an absent field | A missing issuer is omitted, not written as "unknown". "unknown" is a token every incomplete document would share, making them measurably similar to each other for a reason unrelated to content |

**Unmeasured, and the measurement is not available yet.** Whether this improves
retrieval cannot be answered without a golden question set, which §34 has not
produced. What is *known* is the asymmetry: documents are embedded with context and
queries are not, so the context contributes a near-constant component to every
document vector. Contextual retrieval is well-attested as a net gain and §14.2 asks
for it, so it is implemented — but the honest status is "required by the blueprint
and plausible", not "measured to help here".

---

## 18. The chunk minimum is a target, not a guarantee

**Measured across all three development filings, with the whole corpus chunked for
the first time.** 4,816 children; **1,164 (24%) fall below the stated
`child_min_tokens` floor of 48**, and 427 are under 10 tokens. The smallest hold a
single token: `held`, `share`, `a`, `Care`, `6`.

**Re-measured under chunking configuration 2** (the parent fix in §19): 4,969
children, **1,212 (24%) under the floor**. Unchanged, as expected — the fix changed
how parents are formed, not how children are accumulated. The two findings are
independent.

| Filing | Children | Under 48 | Under 10 | Of the under-48, their run's only chunk |
|---|---|---|---|---|
| Infosys AR FY2025 | 1,301 | 388 (30%) | 175 | 372 |
| HDFC Bank AR FY2025 | 2,007 | 609 (30%) | 181 | 581 |
| Ola Electric DRHP | 1,508 | 167 (11%) | 71 | 133 |
| **Total** | **4,816** | **1,164 (24%)** | **427** | **1,086 (93%)** |

**`_absorb_short_tail` is not failing — it cannot reach these.** It merges the last
chunk of a run backwards, and 93% of under-floor children *are* their run's only
chunk, so there is nothing before them to merge into. The remaining 78 have a
sibling and stayed short because merging would have breached `child_max_tokens`,
which is the documented and intended trade (an over-budget chunk is silently
truncated by the embedding model; a short one is not).

**Two upstream causes, both already recorded.**

1. **PyMuPDF emits line fragments, not paragraphs** (§15: median block 38
   characters). `held` and `share` are pieces of one line — the same fragmentation
   §16 shows producing interleaved chunk text.
2. **The table-overlap flag splits a section at single-block granularity.** A lone
   block overlapping a detected region becomes a one-block `table_derived` run and
   cuts the narrative run around it in two. 226 of the 1,164 are single-block
   `table_derived` runs (478 are `table_derived` at any size) — and the detector
   behind the flag is the one ADR-003 measures at **2 of 6 regions bounded
   correctly**.

**Why it is not fixed here.** The three available merges are each refused for a
stated reason: across an evidence-type boundary mixes narrative with table-derived
text (§18.4); across a section boundary attaches one heading path to two sections;
dropping the chunk loses its blocks. Changing `table_overlap` (0.5, unmeasured) or
`child_min_tokens` (48, unmeasured) to reduce the count would be tuning by
intuition, which §18.12 reserves for a comparison on development data and CLAUDE.md
§4 reserves for the developer.

**Consequence to carry into retrieval.** A one-token chunk embeds to a vector
dominated by a single word and will match queries it cannot answer. §20.9's dedup
and the reranker reduce the damage; neither removes it. This is the second-largest
known quality defect in the retrieval path, after §16.

---

## 19. A parent that did not contain its children

**Found by re-auditing against the developer's parent-child research once the
corpus had been re-extracted and re-chunked. Fixed.** Recorded because the defect
was invisible to every test that existed, and because the shape of it generalises.

### What was wrong

A parent was capped at `parent_max_tokens` while its children were not
collectively bounded. One run produced one parent, so a long run produced a parent
that was a *prefix* of its children.

| Measured on the development corpus, before | |
|---|---|
| Parents | 524 |
| **Truncated parents** | **143** |
| **Parented children absent from their own parent** | **1,206 of 2,790 (43%)** |
| Worst parent | 1,535 tokens standing for **50 children totalling 17,498** — 46 of them absent |
| Children with no parent at all | 2,026 of 4,816 (42%) |
| **Children with a parent that actually contained them** | **1,584 of 4,816 — 33%** |

§18.5 keeps parents so a retrieved child can be read in context and §20.8 expands
to them for that reason. Expansion would have returned the opening of a section as
"context" for a passage from its middle: text that reads like context and does not
contain the passage. **Worse than returning nothing**, because nothing is visibly
nothing.

The chunker did record `parent_truncated` in the chunk's notes. What was missing
was any check that the truncation broke an invariant two later phases depend on.

### What was done

Runs are partitioned into **windows a parent can hold whole**, each with its own
parent. A wide section now yields several parents instead of one misleading one —
the same reasoning that made `_emit_section` split a mixed section into runs.

| After, on the same corpus | |
|---|---|
| Parents | **790** |
| Truncated parents | **0** |
| Children absent from their parent | **0 of 2,918** |
| Widest parent | exactly 1,536 tokens — the budget held with no overshoot |
| Widest family | 7 children, parent tokens equal to the sum of its children's |
| Children with no parent | 2,051 of 4,969 (41%) — **unchanged**, see §18 |

`verify_parents` now asserts containment on **every** chunking call, not only in
tests, for the same reason `verify_coverage` does.

### A second defect the new invariant immediately found

`_hard_split` and the sentence path divided an oversized block and reassembled the
pieces with `" ".join(...)`, **collapsing every newline and run of spaces inside
them**. Blocks from PyMuPDF are full of newlines, so this was the common case, and
it read identically — the only symptom was that a piece was no longer a substring
of its own source.

That contradicted the chunker's own stated guarantee that "text is preserved
verbatim… the chunker never rewrites, normalises or summarises (§14.4)". Citations
were never wrong — a split piece cites the whole block — but the guarantee was
false. Pieces are now **slices** of the block.

Then a third, narrower one: `_absorb_short_tail` merged two chunks with a newline,
which reproduces the source only for whole blocks. Merging a *piece* of a split
block produced text appearing nowhere in the document. 2 chunks of 2,337 on one
filing. Split pieces are no longer absorbed.

**All three were found by one invariant, in the order the runs crashed.** None was
found by a test, and the first two had survived four audits.

---

## 20. Table handling in chunking, measured for the first time

Every earlier audit of table handling in chunking was answered "not applicable
under ADR-003". That answer was **wrong, and silently so**: `EXTRACTION_CONFIG_VERSION`
had never been bumped after Phase 6, so the stored corpus held **zero** tables and
there was nothing to measure. With the corpus re-extracted — 861 tables, 23,613
cells — the developer's research points become testable. Three were.

### A detected table is often split across chunks

ADR-003 excludes table *cells* from retrieval, but a narrative block overlapping a
detected table region is flagged `table_derived` and indexed. So a table's text
*is* retrievable, as whatever PyMuPDF read it as — and the research's "tables as
atomic units" point applies after all.

| Filing | Tables | In one chunk | **Split across >1** | No overlapping block | Most chunks for one table |
|---|---|---|---|---|---|
| HDFC Bank AR | 485 | 413 | **52 (11%)** | 20 | 25 |
| Infosys AR | 87 | 57 | **23 (26%)** | 7 | 17 |
| Ola Electric DRHP | 289 | 169 | **91 (31%)** | 29 | 10 |
| **Total** | **861** | **639** | **166 (19%)** | **56** | **25** |

**19% of detected tables have their text spread over more than one chunk**, one
across 25. A header row and the figures it labels can therefore land in different
chunks, which is exactly the failure the research describes — a retrieved row of
numbers with no column headings.

**Not fixed, and the reason is ADR-003.** Chunking the table as a unit means
trusting the detector's bounds, and ADR-003 measured those at **2 of 6 regions
bounded correctly**. Grouping on a wrong boundary would merge unrelated content or
cut a real table in half with more confidence than the detector has earned. The
honest state is: table text is searchable, fragmented, and marked
`table_derived` so retrieval can treat it differently.

#### Two fixes attempted and both rejected, on measurement (2026-10-06)

`SourceBlock.region_id` was added so the chunker can tell one table from the next, which
makes per-table grouping possible for the first time. Both forms were measured end to end
against the stored corpus, with the region mapping held identical on both sides so the
comparison is of chunking and not of classification:

| | Baseline | Run per table | Child boundary per table |
|---|---|---|---|
| Children under the 48-token floor | 1,212 | **1,399** | 1,210 |
| Regions split across chunks | 165 | **184** | 163 |
| Regions split across parents | 11 | 11 | 11 |
| Chunks holding two or more tables | 50 | — | 39 |

**A run per table is actively harmful.** 803 regions become 803 runs, each taking its own
parent window, and because merging across runs is refused a small table becomes a fragment
that can never grow — 187 extra children below the floor, which is the §18 defect made
worse. It also breaks a property §20.8 depends on: a table interleaved with prose lands
under two parents, so expanding from one child recovers half the table.

**A child boundary per table is harmless and not useful.** Two fewer split regions and
eleven fewer mixed chunks, out of 1,254 chunks holding table text. Its sign is not
consistent across documents — one of the three filings got worse on both counts — so at
this corpus size it is noise, and a production path should not carry a threshold
interaction for noise.

**What the earlier arithmetic got wrong.** An estimate of 38 recoverable regions was
derived by subtracting "regions currently in one chunk" from "regions whose blocks fit
inside one child". Both figures came from a region mapping that no longer applied, and the
end-to-end measurement puts the achievable gain at **2**. Separately, an initial claim that
no region was split across parents came from a counter that was initialised and never
incremented; the true baseline is 11.

**128 of the 805 regions with text exceed the child budget outright** (median 734 tokens,
max 3,801) and must divide under any grouping. That is the dominant cause of the 19% and no
chunking rule reunites it. Fixing it means repeating the header on later pieces, which
§14.4 forbids, or admitting a detector, which ADR-003 refuses.

The **56 tables with no overlapping block** are a separate gap: a region the
detector found that no narrative block sits inside above threshold. Their content
exists as cells, which are not indexed, so those tables are not reachable at all.

### The 0.5 overlap threshold is insensitive — measured, not assumed

`table_overlap` was carried as an unmeasured parameter. It can now be characterised:

| Filing | Blocks touching a region | Flagged | **Within 0.1 of the threshold** | Fully inside (>0.99) |
|---|---|---|---|---|
| HDFC Bank AR | 4,598 | 4,511 | **19** | 4,320 |
| Infosys AR | 962 | 885 | **40** | 788 |
| Ola Electric DRHP | 3,908 | 3,802 | **58** | 3,678 |

**117 blocks of 9,468 sit within ±0.1 of the line, and 94% are fully inside a
region.** Overlap is overwhelmingly all-or-nothing, so the exact threshold barely
matters — moving it from 0.5 to 0.4 or 0.6 would reclassify about 1% of touching
blocks. This upgrades `table_overlap` from "unmeasured risk" to **measured as
insensitive**, which is a stronger statement than a tuned value would have been.

### The table flag suppresses some real headings

`_assemble` refuses to treat a `table_derived` block as a heading, so a heading
inside an over-large detected region is lost — and with it the heading path of
everything after it in that section.

| Filing | `table_derived` blocks | Of which the heading rule would have fired |
|---|---|---|
| HDFC Bank AR | 4,511 | 32 |
| Infosys AR | 885 | 7 |
| Ola Electric DRHP | 3,802 | **140** |

**179 potential headings across the corpus.** Ola's 140 is the concerning figure
and is consistent with ADR-003's finding about region bounds: an over-large region
swallows the heading above the table. Whether those 179 are real headings or table
row labels is unknown — the suppression exists precisely because numbered table
rows look like headings — so this is recorded as a bounded uncertainty, not a
defect to reverse.

### The enriched embedding string stays clear of the model's bound

| | Characters, including the `search_document: ` prefix |
|---|---|
| Mean | 1,173 |
| p99 | 2,528 |
| **Max** | **3,982** |
| Over the 6,000-character bound | **0** |

**2,018 characters of headroom at the largest chunk.** Enrichment cannot push a
chunk into the silent truncation ADR-004 measured.

---

## 21. What neither retriever can pin: exact figures, fiscal years and scale

**Measured against the running model and the real analysis chain**, prompted by the
developer's hybrid-search and Nomic research. These are the sharpest numbers in this
register, and together they say something specific about what this pipeline can and
cannot be trusted to retrieve.

### The dense model is nearly blind to which number a sentence states

Cosine between unit vectors, from the configured model:

| Pair | Cosine |
|---|---|
| `Revenue was 1,234.56 crore` vs `Revenue was 4,321.65 crore` | **0.9863** |
| `Revenue was 1,234.56 crore` vs `Revenue was 1,234.56 million` | **0.9167** |
| `What was the PAT in FY2024?` vs `…FY2023?` | **0.9492** |
| `Consolidated revenue for FY2024` vs `Standalone revenue for FY2024` | **0.8398** |
| `Revenue for fiscal year 2024` vs `…2015` | 0.7152 |
| `What was the PAT in FY2024?` vs `What was the EBITDA in FY2024?` | 0.7301 |
| `What was the PAT in FY2024?` vs `What was profit after tax in FY2024?` | **0.6581** |
| `What was the PAT in FY2024?` vs `Who are the independent directors?` | 0.4238 |

Three consequences, in order of severity:

1. **Changing the figure barely moves the vector (0.9863).** Dense retrieval cannot
   be used to find *a particular number*. This is independent support for §7's rule
   that the model never performs authoritative arithmetic and for §25's `Decimal`
   path — the retrieval layer cannot even locate a figure reliably, let alone
   compute with it.
2. **Changing the year moves the vector less than changing the metric** (0.9492
   against 0.7301). So the dense side is *more* likely to confuse FY2023 with FY2024
   than to confuse PAT with EBITDA. Research 3.5's warning, quantified.
3. **The model does not know that PAT is profit after tax (0.6581)** — lower than
   two different years of the same metric. An abbreviation and its expansion are
   further apart than two different periods. Nothing in this project currently
   resolves financial abbreviations; §19.5's alias table is for issuers, not metrics.

The basis figure (0.8398) is worth noting on its own: consolidated and standalone
are a hard filter under §20.2, and that is load-bearing rather than tidy — dense
similarity cannot separate them.

### The lexical side splits every comma-grouped figure

PostgreSQL `english`, which is what analysed both the stored lexemes and the query.
Confirmed with `ts_debug` as well as through our own parser, so this is PostgreSQL's
tokenizer and not a bug here:

| Input | Lexemes |
|---|---|
| `1,234` | `1`, `234` |
| `12,345` | `12`, `345` |
| `1,23,456` (lakh grouping) | `1`, `23`, `456` |
| `1,234.56` | `1`, `234.56` |
| `(1,234)` | `1`, `234` |
| `1234` | `1234` |
| `45.6%` | `45.6` |
| **`10,000`** | **`10`, `000`** |
| **`10000`** | **`10000`** |
| `(10,000)` | `10`, `000` |
| `-10,000` | `-10`, `000` |
| `₹10,000`, `Rs 10,000` | `10`, `000` (+`rs`) |
| `$10K`, `10K` | `10k` — these two agree |

**Two spellings of the same value share no lexeme at all.** `10,000` analyses to
`10` and `000`; `10000` analyses to `10000`. A query written one way cannot match a
document written the other, and financial documents mix both freely.

And `000` is a *pathological* token: it is produced by every thousands group in the
corpus, so it carries almost no information while looking like a term. `-10,000`
keeps its sign as `-10` while `(10,000)` loses it entirely, so three ways of writing
related values produce three disjoint token sets.

#### Fixed — `src/finsight/lexical/normalise.py`, chunking configuration 3

**No text-search configuration can fix this**, which is why it needed code.
`ts_debug` confirms the parser emits two separate `uint` tokens, and a configuration
only chooses which dictionary processes tokens the parser has already split. So
comma-grouped digit groups are joined *before* `to_tsvector` is called, on both the
indexing and the query side.

| | Chunks carrying `000` | Chunks carrying a joined figure |
|---|---|---|
| Configuration 2 | **314** | 0 |
| Configuration 3 | **28** | 30 |

A 91% reduction in the junk token. One real chunk's lexemes before and after tells
the story better than the counts: an ESOP table containing `20,00,00,000` analysed to
`20`, `00`, `00`, `000`, and now analyses to one token.

Three properties the fix is bounded by, each with a test:

- **`chunks.text` is untouched.** The normalised string is built for the analyser and
  never stored, because §14.9 resolves citations into the verbatim text.
- **An enumeration keeps its commas.** `notes 1,2,3` must stay three references, so
  the rule requires a full two- or three-digit group — which covers Indian
  `1,23,456` and Western `123,456` while leaving single digits alone.
- **Both sides call the same function.** A query path that skipped it would turn this
  into a regression, so the rule lives in one place rather than in two SQL
  expressions.

Still not fixed, and still for the stated reasons: `(10,000)` loses its sign
(restoring it is an inference), `Ind AS 115` loses `AS` to the stopword list, and
`ESOS` stems to `eso`. The last two need a different text-search configuration.

**A whole comma-grouped figure becomes two very common tokens.** `1,234` searches as
`1` and `234`, neither distinctive. A *decimal* figure keeps its fractional part
(`234.56`) which is rare enough to pin — which is why hybrid works at all here, and
why it works better for precise amounts than for round ones.

`(1,234)` loses its parentheses. In a financial statement those denote a negative,
so **a loss and a profit of the same magnitude are lexically identical.** The source
text is preserved verbatim, so a citation still shows the parentheses and the
Evidence Gate can still read them; what is lost is the ability to *retrieve* on the
sign.

### What survives, which was the other half of the question

Most financial shorthand comes through intact, so the research's broader worry about
the analysis chain is only partly borne out:

`FY2026` → `fy2026` · `FY26` → `fy26` · `Q3 FY26` → `q3`, `fy26` · `YoY` → `yoy` ·
`QoQ` → `qoq` · `EBITDA`, `PAT`, `CAGR` intact · `200Cr` → `200cr` ·
`CIN L85110KA1981PLC013115` intact · `Section 404` → `section`, `404`

And two fiscal years **are** lexically distinguishable (one shared term index of two
for `PAT in FY2024` against `PAT in FY2023`), which is precisely the gap the dense
side cannot cover.

Three real corruptions beyond the numbers:

| Input | Lexemes | Why it matters |
|---|---|---|
| `FY2024-25` | `fy2024`, **`-25`** | Our own `fiscal_period` format. Splits, and emits a junk token |
| `Ind AS 115` | `ind`, `115` | **`AS` is an English stopword.** The accounting-standard reference loses its middle |
| `ESOS 54` | **`eso`**, `54` | The English stemmer strips the trailing `s` from an acronym. HDFC's ESOP tables are full of these |
| `10-K` | `10`, `k` | Relevant to the held-out US filing |

### An acronym cannot reach its own expansion — on either side

The case where both retrievers fail together: BM25 needs a shared lexeme and dense
needs shared meaning, and an acronym against its expansion offers neither.

| Acronym | Shared lexemes with its expansion | Dense cosine | Corpus chunks: acronym / expansion |
|---|---|---|---|
| EBITDA | **0** | 0.5764 | 22 / 2 |
| PAT | **0** | 0.5476 | **8 / 229** |
| EPS | **0** | 0.6405 | 39 / 101 |
| RoNW | **0** | 0.5159 | 3 / 27 |
| CAGR | **0** | 0.5495 | 14 / 4 |
| DRHP | **0** | 0.6169 | **14 / 246** |
| NPA | **0** | 0.6308 | 43 / 51 |

For scale: two *different fiscal years* of the same metric sit at 0.9492, and two
genuinely unrelated questions at 0.4238. **An acronym and its own expansion
(0.52–0.64) are closer to "unrelated" than to "the same thing".**

The corpus column is what makes this concrete rather than theoretical. The filings
mostly write the expansion while a reader would naturally query the acronym: **"PAT"
reaches 8 chunks where "profit after tax" reaches 229**, and "DRHP" 14 where the
expansion reaches 246. Querying by acronym reaches roughly 3–25% of the relevant
chunks.

Nothing in this project resolves financial abbreviations. §19.5's alias table is for
*issuers*, deliberately — folding "Infosys Limited" into "Infosys" is a different
problem from folding "PAT" into "profit after tax". A metric-alias layer, or a
learned sparse model that performs term expansion, would address it; both are
decisions rather than fixes.

### BM25 prefers shorter chunks, and most where candidates are plentiful

Research warns that without tuning `b`, long chunks are penalised for length alone.
Length normalisation is *meant* to do that, so the question is whether it dominates.
Mean child token count of the retrieved set against the matching population:

| Term | Matching children | Population mean | Top-10 mean | Top-10 / population |
|---|---|---|---|---|
| `crore` | 578 | 238.5 | 128.5 | **0.54** |
| `shareholding` | 488 | 279.2 | 129.9 | **0.47** |
| `depreciation` | 115 | 280.2 | 227.6 | 0.81 |
| `revenue` | 271 | 287.6 | 255.3 | 0.89 |
| `credit risk` | 120 | 322.3 | 309.6 | 0.96 |

**The effect tracks candidate count.** Where a term appears in hundreds of chunks,
the top 10 average about half the population's length — length has become the
tiebreaker. Where the term is selective, the effect nearly vanishes.

Two honest qualifications. First, **this characterises and does not judge**: calling
the order wrong needs relevance labels, and a short chunk densely about a term may
genuinely be the better match. Second, the worst cases here are the least realistic
queries — nobody searches for `crore` alone, and a multi-term query's IDF-weighted
sum leaves less room for length to decide. This is the measurement that should inform
`b`, not a verdict on it.

### Neither can the reranker tell a claim from its denial

Added once a cross-encoder existed to test, and it belongs here rather than only in a
measurement record because it is a correctness property, not a performance one.

| Query | Affirmative passage | Negated passage | Separation |
|---|---|---|---|
| "is the company expected to lose market share" | 9.63 | 9.32 | **0.32** |
| "is the company protected from losing market share" | 3.74 | **4.13** | **0.39** |

The two passages differ by one word — *not*. Against a score range spanning roughly 20
points, 0.3 is noise, and on the second query the affirmative **outscores** the negated
passage, which is the wrong way round.

Production practice asserts that cross-encoders excel at negation. **On MiniLM-L-6-v2
they do not**, and the claim was measured rather than accepted.

**The lexical side is worse than "poor at negation" — it is blind by construction.**
Measured through the real analysis chain:

| Sentence | Lexemes |
|---|---|
| "Company X is **not** expected to lose market share" | `compani, expect, lose, market, share, x` |
| "Company X is expected to lose market share" | `compani, expect, lose, market, share, x` |

**Identical.** `not` is an English stopword, so the two sentences are not merely hard to
tell apart — they are *the same document* to BM25 and to the full-text fallback. The same
holds for the commonest financial construction of all: `no material impact` analyses to
`impact, materi`, exactly as `material impact` does. (Not every negator goes: `without`
survives.)

So for "The Company is not expected to breach its covenants" against "The Company is
expected to breach its covenants" — opposite facts with opposite consequences — **all
three stages are blind to polarity.** Dense similarity barely moves, BM25 sees one
document, and the reranker separates them by 0.3 of a 20-point range.

What follows from it: rank is not evidence of polarity, and nothing downstream may treat
a highly ranked passage as supporting the direction of a claim. §27's Evidence Gate
checks that a cited region *contains* what is asserted, which is the right place for this
to be caught — and it makes that check load-bearing rather than belt-and-braces.

### The decision this is evidence for, and why it is not taken here

§9.7 says the text-search configuration is to be "chosen with recorded evidence when
the lexical index is built, not assumed when it is first written", and
`settings.py` already carries it marked **NOT A SELECTED VALUE**. The measurements
above are that evidence, and they point at a configuration that does **not** apply
English stemming or stopwords to alphanumeric and numeric tokens.

Changing it is an architecture decision reserved to the developer (CLAUDE.md §4),
and it is not cheap: the stored lexemes were analysed with the current
configuration, so changing it is a re-chunk and a full re-index under a new
generation — measured at roughly 50 minutes for this corpus. Recorded, not acted on.

**What does *not* follow from this**: that the reranker will fix it. A cross-encoder
reorders candidates it is given. If neither retriever surfaced the chunk holding the
figure, there is nothing to reorder.

---

## 22. Not implemented, and recorded so absence is not read as a finding

| Gap | Consequence if forgotten |
|---|---|
| **Caption derivation** | `source_tables.caption` is always NULL. NULL currently means "not looked for", which is the opposite of what a reader would assume. No consumer may treat it as evidence a table is uncaptioned |
| **Footnote resolution** | `footnote_refs` points at nothing |
| **Figure-region marking** | Chart axis and data labels extract as ordinary text. Measured across the full split: **841 of 1,403 pages (60%)** carry more than 40 drawing items, but only **50 (4%)** also show five or more bare-numeric short blocks. So heavy vector content is the norm and is mostly design furniture; the chart-label signature is real but confined to about 4% of pages |
| **Table-aware reading order** | Measured at 14% page divergence; tables now make a table-aware ordering possible but it is unbuilt |
| **Duplicate text between blocks and cells** | Text inside a table is stored twice, once as a block and once as cells. Deliberate — suppressing the blocks would let a false-positive table delete narrative prose, and §18 requires exclusion to be a reversible ranking decision. §18.4's table-aware chunking must avoid retrieving the same sentence twice |
