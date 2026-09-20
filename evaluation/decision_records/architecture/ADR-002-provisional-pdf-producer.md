# ADR-002 — Provisional PDF Producer

- **Status:** accepted, **provisional**
- **Date:** 2026-09-21
- **Phase:** 4 — extraction and the source representation
- **Decision:** build Phase 4 on **PyMuPDF** as the PDF producer, behind a producer protocol that keeps the choice reversible.
- **This is not a production parser selection.** CLAUDE.md §4 reserves parser selection for a recorded evaluation with developer approval, and §8 forbids choosing a winner by intuition. No evaluation has been run. Admission of a production parser remains deferred to PROJECT_BLUEPRINT.md §12.12.

## Context

§10.6's third arrow needs *some* producer before extraction can exist at all, and a comparative evaluation needs real filings, a golden set, and development data that Phase 4 does not have. Waiting for the evaluation would mean building nothing; choosing permanently would mean deciding without evidence. The way out is to make the choice cheap to reverse and to say plainly that it is provisional.

The blueprint constrains the field: §6 names PyMuPDF and pdfplumber as the native PDF path, Docling as an evaluated layout-aware candidate, and Camelot as a conditional table comparator.

## Why PyMuPDF rather than PyMuPDF4LLM

These are not competing parsers. PyMuPDF4LLM is a convenience layer over PyMuPDF that converts a document to **Markdown** for naive RAG ingestion. In FinSight's vocabulary that output is a *retrieval representation* (§14.2), not a source representation.

| | PyMuPDF | PyMuPDF4LLM |
|---|---|---|
| Output | blocks with bounding boxes | a Markdown string |
| Layer | source representation | retrieval representation |
| Coordinate fidelity | exact, rotation resolved | geometry only via side channels |
| Citation support | bbox and character offsets resolve to a page region | Markdown offsets do not map back to page geometry |
| Reading order | positional, and ours to decide | inferred and baked in |
| Fits the extraction contract | directly | only by discarding its primary output |

Using it as the producer would breach three rules at once: §14.4 requires source and retrieval representations to be separately stored and addressable; CLAUDE.md §7 forbids citing enriched retrieval text as source evidence; and §14.9 requires citations to resolve to exact source spans, which a character offset into Markdown cannot do. Its heading inference would also become "structure" FinSight never verified.

## Licence

PyMuPDF is **AGPL-3.0 or commercial**. §13's network clause engages if FinSight is ever served to others, which is a real constraint on a system intended to be self-hosted and potentially shared.

The decision proceeds knowingly. The mitigation is not a licence argument but the reversibility below: if the licence becomes a problem, the producer is replaceable without touching the schema, the citations, or anything downstream.

## What keeps it reversible

- **`PdfProducer` protocol.** No PyMuPDF type crosses it. `pymupdf_adapter.py` is the only module in FinSight that imports a PDF library, mirroring the rule that confines boto3 to `s3_store.py`.
- **`source_elements.extraction_method` and `extraction_method_version` are per element, not per run.** A run whose page 3 used the fast path and page 47 a layout-aware path is representable today, so §12.9's per-page routing needs no schema change.
- **`extraction_runs.producer_policy`** records which selection policy orchestrated a run, so runs made before and after a routing change can still be told apart — which is what makes an evaluation across them meaningful.
- **Adding a producer** is one adapter file and one policy entry. Superseded runs are never deleted, so citations issued against them keep resolving (§27.7).

ADR-001 demonstrated this property under real conditions: replacing the object-storage backend cost six files because the adapter was named for the protocol rather than the vendor. The same reasoning applies here.

## Consequences and current limits

- **Reading order is single-column only.** Two-column layouts are read across rather than down. The failure is asserted by a test rather than left implicit, because a limitation without a test is a defect waiting to be found by a reader trusting a citation.
- **No table extraction.** `find_tables()` exists and is unused; §17's table representation is a later slice.
- **No OCR.** An image-only page records a coverage gap (§11.11) rather than silently yielding nothing.
- **Block text is verbatim, including the newline PyMuPDF appends to every block.** That newline is the producer's artefact, not a character in the document, and is kept anyway — citations are offsets into the stored string, and trimming "obvious" noise is where normalisation starts. A different producer may legitimately return different text for the same page, which is why the method is recorded per element.
- **Parser isolation is not enforced** (§11.7). Only trusted development documents may be processed until the restricted parser container exists.

## What would trigger revisiting

A recorded evaluation under §12.12 comparing candidates on real filings with developer approval; the AGPL obligation becoming material; or a document class PyMuPDF handles measurably worse than an alternative. Any of these is a producer swap, not a schema change.
