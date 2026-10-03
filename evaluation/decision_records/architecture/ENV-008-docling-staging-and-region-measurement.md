# ENV-008 — Docling Artifact Staging and Region Measurement

- **Status:** complete, boundary confirmation included
- **Date:** 2026-10-04
- **Phase:** 6 — table detection and the source representation
- **Scope:** making Docling reproducible on this host, and scoring its table
  regions against the human-annotated pages. **Not a parser selection.**

## What this is

ADR-003 admitted no table detector, on 0 of 10 correctly bounded PyMuPDF regions,
and named one blocking measurement: detector precision on real corpus pages
against human-verified truth. This record runs the cheap half of that measurement
and fixes the provisioning defect that blocked it.

Admission remains the developer's under CLAUDE.md §4.

---

## 1. The artifacts were lost, and the loss was structural

ENV-007 measured Docling against a model cache staged interactively. That cache —
`~/.cache/docling` plus the HuggingFace hub cache — no longer existed. The first
attempt to re-run Docling failed with `LocalEntryNotFoundError` after a
certificate verification failure, because it tried to fetch a model mid-parse.

**ENV-007 recorded a manifest, not a procedure.** It named both repositories and
their resolved commits, which is what made recovery possible at all, but nothing
in the repository re-staged them. The five-obstacle provisioning sequence survived
only as prose in a decision record, so two of those obstacles had to be rediscovered.

Both findings below are about the *category*, not about Docling specifically.
PyMuPDF's wheel contains its whole algorithm; Docling's contains inference code
and fetches ~530 MB of trained weights separately. Every layout-aware candidate in
this field is in the second category, because learned layout understanding is data
rather than code. The reproducibility cost is the price of the capability.

### Obstacle 3 recurred verbatim

ENV-007 obstacle 3 was a chunked-transfer backend corrupting downloads with
non-sequential byte ranges. It happened again, identically, failing partway
through the layout model:

```
RuntimeError: Task error: File reconstruction error:
Internal Writer Error: Byte range not sequential:
expected start at 171658996, got 256000000
```

Resolved as before, by forcing the classic HTTP path. It is now set in the staging
script rather than in an operator's session, so it cannot be hit a third time.

### Obstacle 2 is solved properly rather than per-machine

The host's network presents an issuer absent from the pinned `certifi==2026.7.22`
bundle for `huggingface.co`, though not for PyPI — `pip` reaches PyPI with the
default bundle and no proxy or certificate environment variables are set, so the
interception is selective.

**`truststore==0.10.4` is added**, making Python use the host's own certificate
store. Verified with verification fully enabled:

```
verify_mode: CERT_REQUIRED (2)   check_hostname: True
```

Chosen over an exported CA bundle behind `SSL_CERT_FILE` because that is
per-machine setup which works today and blocks the operator later. **Nothing was
disabled**, and the staging script has no flag to disable it (§10).

It is a staging-time dependency only: once artifacts are staged and
`artifacts_path` is set, no parse reaches the network.

### Both pins were still valid

| Artifact | Revision Docling asks for | Resolved commit | ENV-007 |
|---|---|---|---|
| `docling-project/docling-layout-heron` | `main` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | identical |
| `docling-project/docling-models` | `v2.3.0` | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | identical |

The layout model's branch had not moved since the spike, so pinning that commit is
safe against docling 2.133.0. `scripts/stage_docling_models.py` now downloads the
**commit**, not the branch, closing ENV-007 open item 8.

Staged size: **530 MB**. The configured pipeline needs exactly these two, with OCR
disabled; docling 2.133.0 references roughly sixty model repositories across
optional VLM, OCR and figure-classification paths that this project has not adopted.

### A correction

The first reading of the failing traceback concluded that the required model set
had changed between 2.132.0 and 2.133.0, because the fetch passed through
`docling/models/inference_engines/vlm/_utils.py`. **That was wrong.**
`resolve_model_artifacts_path` is a shared helper that happens to live there, and
`hf_vision_base` is the common base for HuggingFace vision models, so the layout
model — an object detector — resolves through both. The artifact set is unchanged.
Recorded because the alarming reading was the first one available, which is how
ENV-007 §3's `row_header` result also went.

### `artifacts_path` is a control, not a convenience

With it set, Docling raises `FileNotFoundError` for a missing model and lists what
is present. With it `None` — the previous default — it downloads. The §20.6 and
§11.7 guarantees therefore now rest on configuration rather than on the network
happening to be unreachable.

Wired through `Settings.docling_artifacts_path` and
`build_docling_table_detector()`. Pinned by
`tests/unit/extraction/test_docling_artifacts.py`, which asserts the staged folder
names against Docling's *own* declared constants — because a wrong folder name
does not error, it silently becomes a download.

---

## 2. Region measurement: 8 of 8

### The truth was already paid for

`evaluation/data/detector-precision-annotation.tsv` holds the ten annotated
pages. Its `verdict` column scores one detector and is spent, but `real_tables`
is truth about the **document** and scores any detector with no new annotation:
four pages hold no table, and four more have an exact count.

### Pre-committed before running

Fixed in the measurement script before the data existed (CLAUDE.md §8):

> Four pages hold no table: a correct detector returns zero regions. Four have an
> exact count: a correct detector returns that many. Two have no exact count and
> are reported, not scored.
>
> This decides **whether to keep measuring**, not whether to admit. §4 reserves an
> admission threshold for the developer. ≥6 of 8 warrants the full measurement;
> ≤3 of 8 fails as decisively as PyMuPDF; 4–5 is ambiguous.

### Result

| n | doc | page | real tables | Docling | `text` | Docling regions |
|---|---|---|---|---|---|---|
| 1 | Infosys | 300 | 1 | **1** | 1 | 18×8 @34.5% accepted |
| 2 | Infosys | 246 | many | 4 | 1 | four regions, all review_required |
| 3 | Ola | 152 | **0** | **0** | 1 | — |
| 4 | Infosys | 140 | unknown | 3 | 1 | one rejected (1×2), two accepted |
| 5 | HDFC | 225 | **0** | **0** | 1 | — |
| 6 | Infosys | 325 | 2 | **2** | 1 | 9×6, 9×6, both accepted |
| 7 | Ola | 58 | **0** | **0** | 1 | — |
| 8 | Infosys | 234 | 4 | **4** | 1 | 3×5, 4×7, 4×7, 9×5, all accepted |
| 9 | Ola | 176 | **0** | **0** | 1 | — |
| 10 | HDFC | 279 | 1 | **1** | 1 | 14×14 @34.2% accepted |

**Docling 8 of 8. PyMuPDF `text` 2 of 8**, and those two by count only — ENV-006
recorded both as wrong boundaries.

Of Docling's eight, **four are fully decided**: a page holding no table has no
boundary to get wrong, so returning nothing is correct outright. `text` claimed a
table on all four. The other four are count-correct with boundaries unverified.

### A page that looked decisive, and was not

Infosys 234 carries four separate financial tables. `text` returned **one** outline
enclosing all four plus prose and footnotes; `lines` returned nothing. Docling
returned **four regions**, 3×5, 4×7, 4×7 and 9×5, all accepted by the quality gate.

**On the counts alone this was read here as the boundary segmentation ENV-006
asked for. §2.5 shows it is not.** The four regions each straddle a table
boundary. The claim is retracted below rather than edited out, because the
sequence — a count-based screen producing a conclusion the eyes then reversed — is
the finding.

---

## 2.5 Boundary confirmation: the screen was misleading

The six table-bearing pages were re-rendered with **both** detectors' regions
outlined, shuffled and blinded, and judged on ENV-006 §2.1's three-category scale.
Same annotator, seed 20261004, recorded in
`evaluation/data/region-boundary-review.tsv`.

| Detector | Correctly bounded (1) | Wrong boundary (2) | No table in region (3) |
|---|---|---|---|
| **Docling** | **2 of 6** | 2 | 2 |
| PyMuPDF `text` | **0 of 6** | 6 | 0 |

**Count agreement was wrong on three of the four positive pages it passed.**

| Page | Truth | Regions | Screen | Boundary verdict |
|---|---|---|---|---|
| Infosys 325 | 2 | 2 | OK | **1** — both tables bounded, prose above correctly excluded |
| Infosys 246 | many | 4 | not scored | **1** — four tables each bounded |
| Infosys 234 | 4 | 4 | OK | **2** — every outline straddles a table boundary |
| Infosys 300 | 1 | 1 | OK | **3** — bounds the two-column prose; the table is untouched |
| HDFC 279 | 1 | 1 | OK | **3** — bounds the image; the table is untouched |
| Infosys 140 | unknown | 3 | not scored | **2** — three offset regions |

The two single-region pages are the sharpest case. Docling returned exactly one
region where exactly one table exists, scoring a clean match — and in both the
region contains **no table at all**, while the real table is enclosed by nothing.
An 18×8 grid over two-column prose and a 14×14 grid over an image.

This is the pre-committed caveat realised. It was stated as a theoretical
limitation of the screen; it is now a measured one, and it inverted the reading on
half the pages it was applied to. **A count-based screen is a cheap filter for
obviously-wrong candidates, not evidence about a plausible one.**

### The error has a shape

Across all six pages the annotator describes the same defect: regions offset by
one or more rows. Outlines stop before a table's last row, begin after its first
few, pick up the paragraph beneath, or absorb the leading line of the table below.
Even the two pages judged correct carry it — their outlines clip the final row and
a one-line footnote.

This is systematic, not random, and it is a different failure from PyMuPDF's.
PyMuPDF cannot find a boundary; Docling finds approximately the right one and
places it wrongly by a row or two. **Hypothesis, not a finding:** the region
derives from detected cell content rather than from the table's ruled extent, so
rows the model does not place fall outside. Untested, and it matters because a
clipped row is silent content loss — the cell simply is not in the table.

### The quality gate is anti-correlated here

| Page | Boundary verdict | Gate |
|---|---|---|
| Infosys 246 | **1** best page | all four `review_required` |
| Infosys 325 | **1** | both `accepted` |
| Infosys 234 | **2** | all four `accepted` |
| Infosys 300 | **3** no table | `accepted` |
| HDFC 279 | **3** no table | `accepted` |

The gate accepted both regions that contain no table, accepted all four
badly-offset regions, and was the only page it flagged the one the annotator rated
best. On this sample its signal runs **against** boundary quality.

That is consistent with how it was built — ADR-003 records that it rejects only
structurally unambiguous conditions and cannot catch a prose-swallowing region —
but it closes off the hope that the gate compensates for a weak detector. A
prose region read as an 18×8 grid has short cells, so no prose rule fires. Six
pages is far too small to call this a rate; it is enough to say the gate must not
be relied on for this, and the limitation register now says so.

---

### Cost

10 pages in **71.8 s** wall, including one model load. Warm per-page **1.6 s**
(Ola, sparse) to **7.6 s** (HDFC, dense), bracketing ENV-007's 2.89 s synthetic
median. The spread is page density, not variance, and it is real evidence that a
one-page synthetic timing underestimates dense filing pages.

---

## 3. Limitations

- **Ten pages, one annotator, three documents.** Nine of ten come from a single
  stratum chosen because it was maximally informative about `lines` versus `text`
  — which makes it the hardest stratum, and also an unrepresentative one.
- **Boundary correctness is unverified on the four positive pages.** Count
  agreement is necessary, not sufficient: four regions can have four wrong
  boundaries. The four true negatives are decided; the rest are screened.
- **No corpus-wide precision.** This says nothing about Docling's behaviour on the
  1,393 pages not in the sample, and in particular nothing about false positives
  on narrative pages beyond the four tested.
- **Cell content was not checked.** Whether the text inside those regions is
  correct, correctly spanned and correctly attributed is a separate question from
  whether the region bounds a table.
- **Peak memory still unmeasured**, against §41.11's shared 15.7 GB envelope.
- **No measurement on rotated pages, continued tables, or the held-out split.**
- **The annotation's two unscored pages** (`many`, `unknown`) are reported only;
  one rendered incompletely during annotation.

---

## 4. Open items

| # | Item | Owner |
|---|---|---|
| 1 | ~~Boundary confirmation~~ | **closed by §2.5** — Docling 2 of 6 |
| 2 | ~~Whether the row-offset defect is correctable from ruled extent~~ | **closed, negative** — see below |
| 3 | Corpus-wide false-positive rate on narrative pages | ADR-003 admission |
| 4 | Cell-content fidelity inside a correctly bounded region | Phase 6 |
| 5 | Peak RSS under the layout model against §41.11 | ADR-004 |
| 6 | Whether the detector contract should accept reported spans and header flags rather than deriving them | Phase 6 design |
| 7 | Throughput on a full document, where 1.6–7.6 s/page compounds | Phase 7 |

### Item 2, pursued and answered: the edge is not recoverable from the rules

The hypothesis was that a region's edge could be snapped to the table's ruled
extent, fixing the clipping. **Measurement refuted it.**

Rule gaps across five real pages, 103 gaps: within-table row spacing is **13.9pt
at both the median and the 75th percentile**; gaps between tables start at 44.5pt.
Two clean populations — so bands are easy to form, and that is where the good news
ends.

**No gap threshold recovers the table count.** At 40pt, Infosys p.234 yields 4
bands for 4 tables and Infosys p.246 yields 3 for 4. At 30pt, p.246 is right and
p.300 and HDFC p.279 split a single table into three and four.

**The reason is in the typography.** These filings rule *under headers and between
sections*, not row by row. Infosys p.234 carries a region spanning y 501-587 whose
rules occupy only y 562-576. Snapping that region to its rules would have cut the
table to a third of itself while presenting as a correctness fix — the exact shape
of silent failure this project is built to avoid.

**Rules bound *whether*, not *where*.** So the signal was kept for the question it
answers and refused for the one it does not.

### What was built instead

`src/finsight/extraction/tables/regions.py`: a region containing none of a ruled
page's rules is refused, and a band of three or more rules that no surviving region
covers is reported as a missed table.

Measured on the same six pages:

| Page | Verdict | Effect |
|---|---|---|
| Infosys 300 | 3 | **region refused**; the real table reported as missed |
| HDFC 279 | 3 | real table reported as missed; the spurious region grazes one rule and survives |
| Infosys 234 | 2 | the uncovered portion of table 2 reported as missed |
| Infosys 325 | 1 | no change, no false alarm |
| Infosys 246 | 1 | no change, no false alarm |
| Infosys 140 | 2 | unruled page, all checks disabled |

15 regions before, 14 after; one refused, three missed areas recorded.

The Infosys 234 result is the independent check worth noting: the annotator wrote
that "the remaining of second table is not enclosed by any red outline", and the
rule review located an uncovered band at y 264-335 without being told.

**This removes one false positive out of two and surfaces three silent losses. It
does not touch the row-level clipping**, which affects every region on every page
judged and remains the dominant defect and the reason admission stays refused.

---

## 5. What this establishes

**Docling is reproducible on this host.** `scripts/stage_docling_models.py` stages
both artifacts at pinned commits over verified TLS, and `artifacts_path` makes a
parse-time download raise rather than happen. That was an ADR-003 admission
prerequisite and it is now met.

**Docling is better than PyMuPDF and still not admissible.** It is right where
PyMuPDF is catastrophically wrong — four true negatives on pages where `text`
claimed a table on every one — and on boundaries it scores **2 of 6 against 0 of
6**. Two correctly bounded pages out of six is a real improvement and nowhere near
a production detector.

**Count agreement is not a proxy for boundary correctness**, now measured rather
than assumed. It passed three pages whose regions were wrong, two of which
contained no table at all. Any future screen built on region counts carries this
result as its caveat.

**The quality gate does not compensate for a weak detector**, and on this sample
runs against it.

**ADR-003's conclusion is unchanged and better supported.** No detector is
admitted; table cells stay out of the retrieval path. The difference from before
is that this is now the conclusion of a measurement rather than the absence of
one.
