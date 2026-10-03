"""Render detected table regions over page images for human judgement.

The measurement in ``scripts/measure_docling_regions.py`` can tell whether a
detector returns the *right number* of regions. It cannot tell whether those
regions bound their tables, and that is the question ENV-006 §2.1 showed to be
decisive: PyMuPDF scored 0 of 10 on bounding while finding a real table in 6 of
those 10. Only eyes settle it.

**The output is blinded and paired.** Every selected page is rendered once per
detector, then the whole set is shuffled and numbered, so a judgement cannot be
anchored by knowing which detector produced an outline. The key is written
alongside and should not be opened until the judgements are recorded.

Blinding is not ceremony here. The previous round of this annotation ran blind
with controls, and the candidate under test is the one the project would prefer
to succeed — which is exactly the circumstance where an unblinded check is worth
little.

Judge the **outline**, not the page, on the scale the earlier round used:

    1 = bounds one real table, roughly right
    2 = a real table is inside, but the outline merges several tables
        and/or swallows prose
    3 = no table in the region at all

Run from an activated prompt at the repository root:

    python scripts/render_table_regions.py

Output goes to ``artifacts/region-review/`` (gitignored). Source pages are never
committed; these renders are filing content and must stay there.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Final

ANNOTATION: Final = Path("evaluation/data/detector-precision-annotation.tsv")
CORPUS: Final = Path("data/corpus/development")
FILES: Final[dict[str, str]] = {
    "Infosys": "in-ar-infosys-fy2025.pdf",
    "HDFC": "in-ar-hdfcbank-fy2025.pdf",
    "Ola": "in-drhp-ola-electric-2023.pdf",
}
OUT: Final = Path("artifacts/region-review")
ZOOM: Final = 2.0
"""Render scale. Page geometry and the outline share it, so no transform is needed.

Both the pixmap and a detector's reported bbox are in *displayed* space — rotation
already applied — so a box scales to pixels by multiplying. Drawing onto the image
rather than onto the page avoids the derotation that drawing in page space would
need, which is the step the Phase 5 coordinate defect got wrong on 78 pages.

**Unverified on ``/Rotate`` pages.** The reasoning above says it should hold, and
no page rendered so far is rotated, so it has not been demonstrated. A rotated
page whose outline looks displaced is this assumption failing, not the detector
— check here before recording a verdict.
"""


def _rows() -> list[dict[str, str]]:
    import csv

    text = ANNOTATION.read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines(), delimiter="\t"))


def _docling_regions(
    doc: str, pages: list[int]
) -> dict[int, list[tuple[float, float, float, float]]]:
    from finsight.extraction.pdf.docling_tables import build_docling_table_detector

    detector = build_docling_table_detector()
    with (CORPUS / FILES[doc]).open("rb") as handle:
        found = detector.detect(handle, pages=sorted(pages))
    return {page: [t.bbox for t in tables] for page, tables in found.items()}


def _pymupdf_regions(
    doc: str, pages: list[int], strategy: str
) -> dict[int, list[tuple[float, float, float, float]]]:
    """Regions from PyMuPDF, per page.

    ``find_tables`` is called per page rather than through the adapter because the
    adapter has no page selection and would scan the whole filing. The strategy
    keyword is the same one the adapter passes, so the regions are the adapter's.
    """
    import pymupdf

    out: dict[int, list[tuple[float, float, float, float]]] = {}
    with pymupdf.open(CORPUS / FILES[doc]) as native:
        for page_number in pages:
            page = native[page_number - 1]
            tables = page.find_tables(strategy=strategy)
            out[page_number] = [tuple(map(float, t.bbox)) for t in tables]
    return out


def _render(
    doc: str,
    page_number: int,
    boxes: list[tuple[float, float, float, float]],
    destination: Path,
) -> None:
    import pymupdf
    from PIL import Image, ImageDraw

    with pymupdf.open(CORPUS / FILES[doc]) as native:
        page = native[page_number - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
        image = Image.frombytes(
            "RGB", (pixmap.width, pixmap.height), pixmap.samples
        )

    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle(
            (x0 * ZOOM, y0 * ZOOM, x1 * ZOOM, y1 * ZOOM),
            outline=(220, 0, 0),
            width=4,
        )
    image.save(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        type=int,
        default=20261004,
        help="shuffle seed, recorded so the ordering is reproducible",
    )
    args = parser.parse_args(argv)

    if not ANNOTATION.is_file():
        print(f"missing annotation: {ANNOTATION}", file=sys.stderr)
        return 1
    missing = [n for n in FILES.values() if not (CORPUS / n).is_file()]
    if missing:
        print(f"corpus documents absent: {', '.join(missing)}", file=sys.stderr)
        return 1

    rows = _rows()
    # Pages with no table at all are already settled: a detector that returns
    # nothing there has no boundary to get wrong, and one that returns a region
    # is wrong by construction. Only pages holding tables need eyes.
    selected = [r for r in rows if r["real_tables"] != "0"]
    by_doc: dict[str, list[int]] = {}
    for row in selected:
        by_doc.setdefault(row["doc"], []).append(int(row["page"]))

    print(f"{len(selected)} page(s) carrying tables; rendering both detectors\n")

    items: list[dict[str, object]] = []
    for doc, pages in by_doc.items():
        docling = _docling_regions(doc, pages)
        text = _pymupdf_regions(doc, pages, "text")
        for row in selected:
            if row["doc"] != doc:
                continue
            page = int(row["page"])
            for detector, regions in (("docling", docling), ("pymupdf-text", text)):
                items.append(
                    {
                        "detector": detector,
                        "doc": doc,
                        "page": page,
                        "annotation_n": row["n"],
                        "real_tables": row["real_tables"],
                        "earlier_verdict": row["verdict"],
                        "boxes": regions.get(page, []),
                    }
                )
        print(f"  {doc}: {len(pages)} page(s)", flush=True)

    random.Random(args.seed).shuffle(items)

    OUT.mkdir(parents=True, exist_ok=True)
    for existing in OUT.glob("*.png"):
        existing.unlink()

    key = []
    for index, item in enumerate(items, start=1):
        name = f"{index:02d}.png"
        _render(
            str(item["doc"]),
            int(item["page"]),  # type: ignore[call-overload]
            list(item["boxes"]),  # type: ignore[arg-type]
            OUT / name,
        )
        key.append({"image": name, "regions": len(item["boxes"]), **item})

    (OUT / "_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    answers = OUT / "ANSWERS.txt"
    answers.write_text(
        "Judge the RED OUTLINE, not the page.\n\n"
        "  1 = bounds one real table, roughly right\n"
        "  2 = a real table is inside, but the outline merges several tables\n"
        "      and/or swallows prose\n"
        "  3 = no table in the region at all\n"
        "  ? = unsure\n\n"
        "An image may carry more than one outline. Judge the set: if every\n"
        "outline on the page bounds its own table, that is a 1.\n\n"
        + "".join(f"{i:02d} = \n" for i in range(1, len(items) + 1)),
        encoding="utf-8",
    )

    print(f"\n{len(items)} image(s) in {OUT.resolve()}")
    print(f"shuffle seed {args.seed}; key written to _key.json - do not open it yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
