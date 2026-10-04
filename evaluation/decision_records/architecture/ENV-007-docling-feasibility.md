# ENV-007 — Docling Feasibility Spike

- **Status:** complete; superseded on provisioning and region quality by **ENV-008**
- **Date:** 2026-10-02
- **Phase:** 6 — table detection and the source representation
- **Scope:** feasibility and reconstruction burden. **Not a parser selection.**

## What this is and is not

CLAUDE.md §4 reserves parser selection for a recorded evaluation with developer
approval, and §8 forbids choosing a winner by intuition. This record establishes
whether Docling can run on this host, what it costs, and — the question that
prompted it — **whether it supplies the table semantics PyMuPDF makes us
reconstruct**. Admission remains ADR-004's decision.

The probe is this project's own table fixture, used because its ground truth is
known exactly: a header spanning two period columns, a units row, a deliberately
empty cell, an indented sub-item, a footnote marker beside a value, and
parenthesised negatives. Both parsers read byte-identical input — the fixture was
generated once by the project environment and handed to both.

Versions: docling 2.132.0, torch 2.14.1+cpu, Python 3.12.10. `cuda available:
False`, 4 threads, consistent with ENV-001.

---

## 1. The finding that matters

**Docling reports the two things our reconstruction layer cannot infer.**

| Semantic | PyMuPDF | Docling |
|---|---|---|
| **Merged-cell spans** | Reports an *absent neighbour*. A merge and a detection miss are **indistinguishable** | **`col_span=2` stated explicitly**, with `start_col_offset_idx=1`, `end_col_offset_idx=3` |
| **Header cells** | `header_rows` returns **0** on a two-header table | **`column_header=True`** per cell |
| **Units row** | Returned as an ordinary data row | **`row_section=True`** — structurally separated from both header and data |
| Row-label column | Inferred from `column_index == 0` | `row_header=True` per cell |
| Negatives `(45)` | Verbatim | Verbatim |
| Footnote marker in cell | Verbatim, unresolved | Verbatim, unresolved |
| Row-label **hierarchy** | Inferred from text left-edge | **Not represented** — see §3 |

The span result is the decisive one. ENV-006's corpus pass measured the
merge-versus-miss ambiguity at **mean 19% / median 11% of grid positions, with 436
of 861 tables (51%) above 10%**. That ambiguity is *undecidable* from a grid — no
refinement of our heuristic reaches it. Docling removes the error class rather
than shrinking it, which is precisely what the reconstruction-burden criterion was
introduced to measure.

---

## 2. The empty-cell model is better than ours, not worse

First reading looked like a regression: Docling reported **15 cells for a 7×3 = 21
position grid**, with zero empty-text cells. It omits empty cells entirely.

Tested rather than assumed. Every cell carries explicit start/end row and column
offsets plus `col_span`, so expanding each cell over its span gives the occupied
set, and the complement is exactly the empty positions:

```
positions covered by a cell or its span: 16 of 21
unreferenced positions: [(0,0), (1,0), (2,1), (2,2), (4,2)]
```

All five are genuinely empty in the fixture — the two blank label cells above the
header, the two blank value cells in the units row, and the one deliberately empty
cell at (4,2). **Zero false positives, zero misses.**

So the reconstruction is exact *and* unambiguous: a span-covered position is a
merge, an unreferenced position is empty. Our own model preserves empty-versus-
absent carefully precisely because it cannot tell a merge from a miss. With
explicit spans that distinction stops being load-bearing.

---

## 3. One result that did not survive checking

`row_header=True` on the indented sub-item initially looked like a hierarchy
signal. Dumping every cell showed it on **all four** row labels — `Deposits`,
`Of which: term deposits`, `Other income (a)`, `Loss on sale`.

So it flags the row-label *column*, not the indentation *nesting*. The
relationship that makes "Of which: term deposits" a component of the line above
rather than a peer of it is **not represented**. Our `text_left` indentation
inference, and its limitation-register entry, remain necessary.

Recorded because the flattering reading was the first one available.

---

## 4. Cost

| Measure | Value |
|---|---|
| Converter construction | 0.2 s |
| First convert, 1 page, includes model load | 8.2 s |
| **Warm convert, 1 page** | 8.73, 2.59, 3.13, 2.85, 2.89 s → **median 2.89 s** |
| **Throughput** | **≈ 0.35 pages/s** |
| PyMuPDF `find_tables` on real corpus pages | ≈ 7.9 pages/s |
| **Ratio** | **≈ 23× slower** |

Five repeated runs were used because this host swings ±50% on identical sustained
work (see §7). After the first warm-up the spread is narrow — 2.59 to 3.13 s — so
the per-page figure is more trustworthy than most timings in this project.

Extrapolated to the 1,403-page development split: **roughly 68 minutes against
roughly 3 minutes.** Extrapolation only; a single synthetic page is not a corpus.

### Footprint

| Component | Size |
|---|---|
| Installed packages | **1.4 GB** |
| `docling-layout-heron` weights | 163.8 MB |
| `docling-models` TableFormer weights | 341.6 MB |
| **Total** | **≈ 1.9 GB** |
| For comparison: the entire current PyMuPDF path | ≈ 20 MB |

TableFormer ships `accurate` (202.9 MB) and `fast` (138.7 MB) variants; the
default is `accurate`.

---

## 5. Provisioning: five obstacles, and what they say about §20.6

Every one was resolved without weakening any security control. None is
disqualifying on its own. Together they measure something real.

| # | Obstacle | Resolution |
|---|---|---|
| 1 | **Default install pulls a CUDA torch build.** Unusable on a host with no accelerator, and its CUDA header paths exceed the platform's maximum path length, failing the install outright | Pin the CPU wheel index explicitly |
| 2 | **Model fetch fails certificate verification.** Python's bundled CA list does not contain the host network's required issuer | Supply the host's own trust anchors. **Verification stayed enabled** — `CERT_REQUIRED`, hostname checking on. §10 forbids disabling TLS and nothing was disabled |
| 3 | **Chunked transfer backend corrupts downloads** — non-sequential byte ranges | Force the classic HTTP download path |
| 4 | **Symlinked cache unavailable** without elevated privileges | Degraded cache layout, more disk than nominal |
| 5 | **TableFormer is pinned to a release tag, not a branch**, and the pin is discoverable only by reading Docling's source | Stage that exact revision |

**Obstacle 5 was caught only because the parse ran with outbound traffic
disabled.** With the network open, Docling would have silently fetched the correct
revision and the pin would never have surfaced. That is the §20.6 model proving
its worth on first use: enforcing "no runtime download" converted a silent
success into a diagnosable failure.

### What this means architecturally

Docling's default configuration conflicts with **three** standing constraints at
once:

- **§12.11** defers OCR; the standard PDF pipeline initialises an OCR model
  unconditionally and fetched it from a third-party model host. The corpus
  contains zero scanned pages across 1,403.
- **§20.6** requires parser artifacts fetched and verified *before* network
  isolation, then served from a read-only local cache; Docling downloads at first
  `convert()`.
- **§11.7** gives the restricted parser worker no parse-time outbound internet;
  default Docling would fail closed on every parse.

All three are configuration-addressable — `do_ocr=False` plus a pre-staged cache —
but Docling is **categorically not a drop-in**. It arrives requiring explicit
configuration to be safe.

It also brings capability we deliberately deferred: an OCR stack and a computer-
vision library, plus **its own independent PDF engine** rather than a layer over
PyMuPDF. Two engines reading the same bytes matters when citations are character
offsets into whichever produced the text (§14.9) — ADR-002 already called a
producer change "a producer swap, not a schema change," and this sharpens the
cost.

### Artifact manifest

The first parser in this project to need one. PyMuPDF's version *is* its wheel
version; Docling needs a pinned package version, a pinned wheel index, and two
model repositories at two different revision styles:

| Artifact | Revision | Resolved commit |
|---|---|---|
| `docling-project/docling-layout-heron` | `main` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` |
| `docling-project/docling-models` | `v2.3.0` | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` |

A branch pin is not reproducible. If Docling is admitted, the layout model should
be pinned to a commit and this manifest becomes something the project owns and
keeps in step with every Docling upgrade.

### Dependency overlap is benign

Against the pinned requirements: **27 packages match exactly, 4 differ at patch
level** (`urllib3`, `charset-normalizer`, `idna`, `python-dotenv`). No major
bumps. `pydantic`, `pydantic-settings`, `pydantic_core`, `httpx`, `pillow` and
`defusedxml` all match, so no Pydantic or HTTPX migration. The CPU wheel removed
the only major conflict the CUDA build introduced.

---

## 6. Serialization, for comparison

Docling composes a two-level header into the column name and renders blanks
correctly:

```
|                         | Year ended March 31 - 2025 | Year ended March 31 - 2024 |
| (Rs in crore)           |                            |                            |
| Deposits                | 1,234                      | 1,100                      |
| Of which: term deposits | 560                        |                            |
```

PyMuPDF's `to_markdown()` on the same table **invents column names** —
`|Col1|Year ended March 31|Col3|` — and flattens the span away. Neither output is
citation-safe; §14.9 requires citations to resolve to source cells, not to a
rendered table.

---

## 7. Limitations

Stated plainly, because several figures here are weaker than they look.

- **One synthetic page.** Everything in §1–§3 is evidence about Docling's *data
  model*, which is a property of the library. Nothing here measures how well it
  reads real filings. ENV-005 overstated producer throughput ~3.5× from synthetic
  fixtures, and a borderless-table fixture in this phase overstated strategy
  complementarity so badly the conclusion was contradicted by the first
  real-document measurement. **Twice burned.**
- **No detection quality measured.** Whether Docling finds the right tables on
  real pages, and at what precision, is untested. Our own detector precision is
  equally unmeasured — `text` fires on 97% of real pages.
- **Throughput is extrapolated** from one page. The ±50% host variance applies to
  the comparison baseline even though the Docling figure itself was stable.
- **Memory unmeasured.** §41.11 flags layout-model memory contention against a
  shared 15.7 GB envelope; this spike did not measure peak RSS.
- **Fidelity on rotated pages, borderless tables and continued tables untested.**
- **A branch-pinned model revision was used** for layout, which is not
  reproducible.

---

## 8. Open items

| # | Item | Owner |
|---|---|---|
| 1 | Docling detection precision and fidelity on **real corpus pages**, against the ground truth | ADR-004 |
| 2 | Peak memory under a layout model, against §41.11 and the shared envelope | ADR-004 |
| 3 | Whether `row_section` reliably isolates units rows on real tables, where only 12% currently yield a declaration | Phase 6 ground truth |
| 4 | Whether the detector contract should **accept** spans and header flags from a parser that supplies them, deriving them only when absent | Phase 6 design |
| 5 | Citation-offset consequences of two independent PDF engines producing text for the same bytes | ADR-004 |
| 6 | Footnote resolution — unsolved by either parser; markers resolve to nothing in both | footnote commit |
| 7 | Row-label hierarchy — unsolved by either parser | limitation register |
| 8 | Pin the layout model to a commit rather than a branch | **closed by ENV-008** — `scripts/stage_docling_models.py` |

> **Annotation, 2026-10-04.** The model cache this spike measured against was
> later found gone, and the provisioning sequence in §5 existed only as prose
> here — so obstacles 2 and 3 had to be rediscovered. ENV-008 turns it into a
> committed script. The lesson generalises: **a manifest records what was staged,
> a script restores it**, and this record had only the first.

---

## 9. What this establishes for ADR-004

**Docling materially reduces reconstruction burden.** It supplies spans and header
flags directly, eliminating an ambiguity that affects the majority of real tables
and that no heuristic at our layer can resolve. On the criterion this project
adopted for parser evaluation, that is a strong result.

**It costs roughly 23× the throughput, ~95× the footprint, and a five-step
provisioning procedure** — against a current parser that is a single wheel with no
runtime artifacts.

**Neither figure decides the question**, because the thing ADR-004 must weigh —
extraction fidelity on real financial documents — is exactly what this spike did
not measure. What it did establish is that Docling is worth measuring properly,
which was the question asked.

The §12.4 reading the blueprint already held — Docling as the layout-aware adapter
for *difficult pages*, with the native-text fast path handling the rest under
§12.2 routing — now has quantitative support from both directions: it is too slow
to be the default, and it supplies semantics the default cannot.
