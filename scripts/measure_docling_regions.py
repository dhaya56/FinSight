"""Score a table detector against the human-annotated pages.

Reads ``evaluation/data/detector-precision-annotation.tsv`` — the only
human-verified detection truth this project has — and reports, for each annotated
page, how many regions the detector claims against how many real tables the
annotator counted.

**What this measures, and what it does not.** The annotation's ``real_tables``
column is truth about the *document*, so it scores any detector without new
annotation. But region *count* agreement is a necessary condition, not a
sufficient one: a detector can return four regions with four wrong boundaries.
A page recorded as holding no table is fully decided here — a correct detector
returns nothing and there is no boundary to get wrong. A page with tables is only
screened, and boundary correctness still needs a rendered check.

Run from an activated prompt at the repository root, after staging artifacts with
``scripts/stage_docling_models.py``:

    python scripts/measure_docling_regions.py

PyMuPDF's recorded result on the same screen is 2 of 8, and 0 of 10 regions were
correctly bounded (ENV-006 §2.1, ADR-003).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Final

ANNOTATION: Final = Path("evaluation/data/detector-precision-annotation.tsv")
CORPUS: Final = Path("data/corpus/development")
FILES: Final[dict[str, str]] = {
    "Infosys": "in-ar-infosys-fy2025.pdf",
    "HDFC": "in-ar-hdfcbank-fy2025.pdf",
    "Ola": "in-drhp-ola-electric-2023.pdf",
}


def _rows() -> list[dict[str, str]]:
    text = ANNOTATION.read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines(), delimiter="\t"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="optional path to write per-region detail as JSON",
    )
    args = parser.parse_args(argv)

    if not ANNOTATION.is_file():
        print(f"missing annotation: {ANNOTATION}", file=sys.stderr)
        return 1
    missing = [name for name in FILES.values() if not (CORPUS / name).is_file()]
    if missing:
        print(
            "corpus documents are not present (they are never committed): "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    import pymupdf

    from finsight.extraction.pdf.docling_tables import build_docling_table_detector
    from finsight.extraction.tables.structure import derive
    from finsight.extraction.tables.validation import assess

    detector = build_docling_table_detector()
    rows = _rows()

    by_doc: dict[str, list[int]] = {}
    for row in rows:
        by_doc.setdefault(row["doc"], []).append(int(row["page"]))

    detail: dict[str, dict[str, object]] = {}
    for doc, pages in by_doc.items():
        path = CORPUS / FILES[doc]
        started = time.perf_counter()
        with path.open("rb") as handle:
            found = detector.detect(handle, pages=sorted(pages))
        elapsed = time.perf_counter() - started

        with pymupdf.open(path) as native:
            for row in rows:
                if row["doc"] != doc:
                    continue
                page = int(row["page"])
                rect = native[page - 1].rect
                area = rect.width * rect.height
                regions = []
                for table in found.get(page, []):
                    x0, y0, x1, y1 = table.bbox
                    quality = assess(table, derive(table))
                    regions.append(
                        {
                            "rows": table.row_count,
                            "cols": table.column_count,
                            "page_area_pct": round(
                                100.0 * abs(x1 - x0) * abs(y1 - y0) / area, 1
                            ),
                            "verdict": quality.verdict.value,
                            "prose_ratio": quality.prose_ratio,
                            "numeric_ratio": quality.numeric_ratio,
                            "dropped_cells": quality.dropped_cells,
                        }
                    )
                detail[row["n"]] = {
                    "doc": doc,
                    "page": page,
                    "stratum": row["stratum"],
                    "real_tables": row["real_tables"],
                    "text_regions": int(row["text_regions"]),
                    "detected": len(regions),
                    "regions": regions,
                }
        print(f"{doc}: {len(pages)} page(s) in {elapsed:.1f}s", flush=True)

    header = (
        f"{'n':>3} {'doc':<8} {'page':>5} {'truth':>6} {'found':>6} "
        f"{'text':>5}  regions"
    )
    print(f"\n{header}\n{'-' * len(header)}")
    scored = hits = decided = 0
    for n in sorted(detail, key=int):
        item = detail[n]
        truth_text = str(item["real_tables"])
        shape = ", ".join(
            f"{g['rows']}x{g['cols']}@{g['page_area_pct']}%/{g['verdict'][:3]}"
            for g in item["regions"]  # type: ignore[union-attr]
        )
        mark = "    "
        if truth_text.isdigit():
            scored += 1
            ok = item["detected"] == int(truth_text)
            hits += ok
            # A page with no table is fully decided: nothing to bound.
            decided += ok and int(truth_text) == 0
            mark = "OK  " if ok else "XX  "
        print(
            f"{n:>3} {item['doc']:<8} {item['page']:>5} {truth_text:>6} "
            f"{item['detected']:>6} {item['text_regions']:>5}  {mark}{shape}"
        )

    print(f"\ncount agreement: {hits} of {scored}")
    print(f"fully decided (true negatives, no boundary to verify): {decided}")
    print(f"count-correct but boundary unverified: {hits - decided}")

    if args.out is not None:
        import json

        args.out.write_text(json.dumps(detail, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
