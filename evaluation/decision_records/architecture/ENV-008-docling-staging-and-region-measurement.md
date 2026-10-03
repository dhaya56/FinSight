# ENV-008 — Docling Artifact Staging and Region Measurement

- **Status:** complete for the measurements it covers; boundary confirmation outstanding
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

### The decisive page

Infosys 234 carries four separate financial tables. `text` returned **one** outline
enclosing all four plus prose and footnotes; `lines` returned nothing. Docling
returned **four regions**, 3×5, 4×7, 4×7 and 9×5, all accepted by the quality gate.

ENV-006 identified the missing capability as *boundary segmentation of
horizontally-ruled tables*. This is that capability, on a real page, on the
hardest stratum in the corpus.

### The quality gate behaved

Not designed into this measurement, but it ran on every region:

- Infosys 140's spurious 1×2 region was **rejected** as degenerate.
- Infosys 246's four regions came back **review_required** on dropped cells —
  Docling segmented a page of many small tables and reported honestly that it had
  lost content doing so.
- Every other region was accepted.

ADR-003's limitation register entry says the gate does not catch merged or
prose-swallowing regions. That remains true. What this shows is that it does not
*fire spuriously* on a detector whose regions are well-formed.

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
| 1 | Boundary confirmation on the four count-correct pages — render Docling's regions and judge them as the PyMuPDF regions were judged. ~4 pages, minutes | ADR-003 admission |
| 2 | Corpus-wide false-positive rate on narrative pages | ADR-003 admission |
| 3 | Cell-content fidelity inside a correctly bounded region | Phase 6 |
| 4 | Peak RSS under the layout model against §41.11 | ADR-004 |
| 5 | Whether the detector contract should accept reported spans and header flags rather than deriving them | Phase 6 design |
| 6 | Throughput on a full document, where 1.6–7.6 s/page compounds | Phase 7 |

---

## 5. What this establishes

**Docling is reproducible on this host.** `scripts/stage_docling_models.py` stages
both artifacts at pinned commits over verified TLS, and `artifacts_path` makes a
parse-time download raise rather than happen. That was an ADR-003 admission
prerequisite and it is now met.

**Docling's regions agree with human-verified truth where PyMuPDF's do not**, 8 of
8 against 2 of 8, including four true negatives on pages where `text` claimed a
table and the one page where it merged four tables into one region.

**This is not yet an admission.** Four of the eight remain count-correct with
unverified boundaries, the sample is ten pages from one stratum, and corpus-wide
precision is unmeasured. What it does establish is that the full measurement is
worth running and that nothing structural blocks it any more.
