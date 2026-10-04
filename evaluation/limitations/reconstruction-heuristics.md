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

---

## 18. The chunk minimum is a target, not a guarantee

**Measured across all three development filings, with the whole corpus chunked for
the first time.** 4,816 children; **1,164 (24%) fall below the stated
`child_min_tokens` floor of 48**, and 427 are under 10 tokens. The smallest hold a
single token: `held`, `share`, `a`, `Care`, `6`.

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

## 19. Not implemented, and recorded so absence is not read as a finding

| Gap | Consequence if forgotten |
|---|---|
| **Caption derivation** | `source_tables.caption` is always NULL. NULL currently means "not looked for", which is the opposite of what a reader would assume. No consumer may treat it as evidence a table is uncaptioned |
| **Footnote resolution** | `footnote_refs` points at nothing |
| **Figure-region marking** | Chart axis and data labels extract as ordinary text. Measured across the full split: **841 of 1,403 pages (60%)** carry more than 40 drawing items, but only **50 (4%)** also show five or more bare-numeric short blocks. So heavy vector content is the norm and is mostly design furniture; the chart-label signature is real but confined to about 4% of pages |
| **Table-aware reading order** | Measured at 14% page divergence; tables now make a table-aware ordering possible but it is unbuilt |
| **Duplicate text between blocks and cells** | Text inside a table is stored twice, once as a block and once as cells. Deliberate — suppressing the blocks would let a false-positive table delete narrative prose, and §18 requires exclusion to be a reversible ranking decision. §18.4's table-aware chunking must avoid retrieving the same sentence twice |
