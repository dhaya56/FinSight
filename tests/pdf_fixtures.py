"""Deterministic PDF fixtures, written with ReportLab.

ReportLab rather than PyMuPDF, deliberately. PyMuPDF reports geometry with a
top-left origin; ReportLab draws in the PDF format's own bottom-left space. That
asymmetry is the whole point: text drawn 700pt up from the bottom of an 842pt
page must read back near 142pt from the top. If the writer and the reader shared
a coordinate bug it would cancel out, and the test that is supposed to pin the
convention would pass while every future citation pointed at the mirror image of
its evidence.

These fixtures prove the plumbing works. They are not evidence that PyMuPDF
extracts real financial filings well, and no green result here should ever be
read that way (CLAUDE.md §8).
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from PIL import Image
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FONT: Final = "Helvetica"
FONT_SIZE: Final = 12

PAGE_WIDTH: Final[float] = A4[0]
PAGE_HEIGHT: Final[float] = A4[1]


@dataclass(frozen=True, slots=True)
class PlacedText:
    """Text drawn at a baseline measured from the bottom-left corner.

    ``y_from_bottom`` is named for what it is, so no test can quietly read it as
    a distance from the top.
    """

    text: str
    x: float
    y_from_bottom: float


def build_pdf(
    pages: Sequence[Sequence[PlacedText]],
    *,
    rotation: int = 0,
    encrypt: Any = None,
) -> bytes:
    """Render pages of placed text and return the PDF bytes."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, encrypt=encrypt)
    for placements in pages:
        if rotation:
            pdf.setPageRotation(rotation)
        pdf.setFont(FONT, FONT_SIZE)
        for placed in placements:
            pdf.drawString(placed.x, placed.y_from_bottom, placed.text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_image_only_pdf() -> bytes:
    """A single page holding one image and no text, as a scanned filing would."""
    image = Image.new("RGB", (64, 64), (200, 200, 200))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawImage(ImageReader(image), 72, 600, width=100, height=100)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_encrypted_pdf() -> bytes:
    """A password-protected page.

    The password exists only inside this fixture and protects nothing real; it
    is here so the producer's refusal to open encrypted documents is exercised.
    """
    from reportlab.lib.pdfencrypt import StandardEncryption

    return build_pdf(
        [[PlacedText("Confidential", x=72, y_from_bottom=700)]],
        encrypt=StandardEncryption("fixture-only-not-a-secret"),
    )


# --- Layout fixtures (§33.6, layout half) -----------------------------------


def build_rotated_pdf(rotation: int, *, text: str = "Revenue from operations") -> bytes:
    """One rotated page whose text is placed where rotation actually matters.

    Two traps are avoided here, and the second one hid a real defect for a while.

    ``setPageRotation(90)`` makes ReportLab swap the media box, so text drawn
    700pt up — comfortably inside a portrait A4 — lands *outside* the page and
    extracts as nothing. A fixture that silently produced an empty page lets a
    rotation test pass while proving nothing.

    Worse, text near the origin corner sits inside *both* the rotated and the
    unrotated coordinate space, so a box that was never transformed still looks
    valid. The placement below is deliberately far from that corner: on a
    rotated page the untransformed box falls outside the displayed rectangle,
    which is what makes the assertion in the producer's tests able to fail.
    """
    if rotation in {90, 270}:
        # Landscape page size, so ReportLab writes a portrait media box and a
        # /Rotate entry — the shape real annual reports use for fold-out tables.
        page_size = (PAGE_HEIGHT, PAGE_WIDTH)
        placement = PlacedText(text, x=300, y_from_bottom=100)
    else:
        page_size = (PAGE_WIDTH, PAGE_HEIGHT)
        placement = PlacedText(text, x=300, y_from_bottom=100)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    pdf.setPageRotation(rotation)
    pdf.setFont(FONT, FONT_SIZE)
    pdf.drawString(placement.x, placement.y_from_bottom, placement.text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_mixed_page_size_pdf() -> bytes:
    """A4 followed by Letter, so page geometry cannot be assumed document-wide.

    Real filings mix sizes — a landscape fold-out for a wide table inside an
    otherwise portrait report — and a reader that caches the first page's
    dimensions would mis-place every citation after it.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont(FONT, FONT_SIZE)
    pdf.drawString(72, 700, "A4 page")
    pdf.showPage()

    pdf.setPageSize(LETTER)
    pdf.setFont(FONT, FONT_SIZE)
    pdf.drawString(72, 700, "Letter page")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_two_column_pdf() -> bytes:
    """Two aligned columns of continuous prose.

    Both columns start at the same height, and PyMuPDF merges each into a single
    block, so the positional rule orders them correctly. Kept as the control for
    :func:`build_staggered_columns_pdf`, which is the case that fails.
    """
    left = ["Revenue from operations", "Other income", "Total income"]
    right = ["Finance costs", "Depreciation", "Profit before tax"]
    placements = [
        PlacedText(line, x=72, y_from_bottom=700 - index * 16)
        for index, line in enumerate(left)
    ] + [
        PlacedText(line, x=320, y_from_bottom=700 - index * 16)
        for index, line in enumerate(right)
    ]
    return build_pdf([placements])


def build_staggered_columns_pdf() -> bytes:
    """Two columns whose blocks sit at different heights.

    This is where top-to-bottom-then-left-to-right genuinely fails. Correct
    reading order is the whole left column, then the right. The positional rule
    interleaves them, because the right column's block starts higher than the
    left column's second block.

    Separated vertically on purpose: adjacent lines merge into one block, and a
    merged column would hide the failure.
    """
    placements = [
        PlacedText("LEFT TOP", x=72, y_from_bottom=700),
        PlacedText("LEFT BOTTOM", x=72, y_from_bottom=400),
        PlacedText("RIGHT MIDDLE", x=320, y_from_bottom=550),
    ]
    return build_pdf([placements])


def build_hyphenated_pdf() -> bytes:
    """A word split across a line break by a hyphen.

    Extraction must not silently rejoin it: the hyphen is in the document, and
    repairing it is a retrieval-representation decision (§18.7), not something
    the source representation may do on its way past.
    """
    return build_pdf(
        [
            [
                PlacedText("consoli-", x=72, y_from_bottom=700),
                PlacedText("dated statements", x=72, y_from_bottom=684),
            ]
        ]
    )


# --- Table fixtures (§33.6, table half) -------------------------------------

TABLE_X: Final[tuple[float, ...]] = (60.0, 240.0, 350.0, 460.0)
TABLE_Y: Final[tuple[float, ...]] = (700.0, 680.0, 660.0, 640.0, 620.0, 600.0, 580.0, 560.0)

TABLE_CONTENT: Final[tuple[tuple[str, int, int], ...]] = (
    ("Year ended March 31", 1, 0),  # spans the two period columns
    ("2025", 1, 1),
    ("2024", 2, 1),
    ("(Rs in crore)", 0, 2),  # units row, not a header and not data
    ("Deposits", 0, 3),
    ("1,234", 1, 3),
    ("1,100", 2, 3),
    ("Of which: term deposits", 0, 4),  # indented, so a child of Deposits
    ("560", 1, 4),
    # (2, 4) deliberately absent from the content: an empty cell the document has
    ("Other income (a)", 0, 5),  # footnote marker beside a value
    ("56", 1, 5),
    ("40", 2, 5),
    ("Loss on sale", 0, 6),
    ("(45)", 1, 6),  # negatives in parentheses, which must not read as markers
    ("(30)", 2, 6),
)

INDENTED_LABEL: Final = "Of which: term deposits"
"""The row whose label is drawn further right, expressing hierarchy by position."""


def build_financial_table_pdf(*, ruled: bool = True) -> bytes:
    """One financial table, optionally without ruling lines.

    The two variants are the same table and the whole point of the pair: the
    ``lines`` strategy reads the ruled one correctly and finds *nothing at all* in
    the borderless one, while ``text`` finds the borderless one and over-segments
    it. A single fixture would have made one strategy look simply better than the
    other rather than differently blind.

    Every awkward case a financial table carries is here on purpose — a header
    spanning two period columns, a units row that is neither header nor data, an
    indented sub-item, a genuinely empty cell, a footnote marker next to a value,
    and parenthesised negatives that must not be mistaken for markers.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont(FONT, 9)

    if ruled:
        for y in TABLE_Y:
            pdf.line(TABLE_X[0], y, TABLE_X[-1], y)
        pdf.line(TABLE_X[0], TABLE_Y[-1], TABLE_X[0], TABLE_Y[0])
        pdf.line(TABLE_X[1], TABLE_Y[-1], TABLE_X[1], TABLE_Y[0])
        # Stops below the first row, which is what makes that header span.
        pdf.line(TABLE_X[2], TABLE_Y[-1], TABLE_X[2], TABLE_Y[1])
        pdf.line(TABLE_X[3], TABLE_Y[-1], TABLE_X[3], TABLE_Y[0])

    for text, column, row in TABLE_CONTENT:
        indent = 12.0 if text == INDENTED_LABEL else 4.0
        pdf.drawString(TABLE_X[column] + indent, TABLE_Y[row + 1] + 6, text)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_rotated_table_pdf(rotation: int) -> bytes:
    """A ruled table on a rotated page.

    Cell coordinates inherit the trap that cost a real annual report the citation
    boxes on 78 of its pages: ``find_tables`` reports regions in unrotated space
    while the page rectangle has rotation applied. This fixture is what makes a
    missing transform fail a test instead of surfacing as a wrong highlight.
    """
    buffer = io.BytesIO()
    page_size = (PAGE_HEIGHT, PAGE_WIDTH) if rotation in {90, 270} else (PAGE_WIDTH, PAGE_HEIGHT)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    pdf.setPageRotation(rotation)
    pdf.setFont(FONT, 9)

    left, right = 60.0, 300.0
    top, bottom = 400.0, 340.0
    for y in (top, (top + bottom) / 2, bottom):
        pdf.line(left, y, right, y)
    for x in (left, (left + right) / 2, right):
        pdf.line(x, bottom, x, top)
    pdf.drawString(left + 4, top - 14, "Revenue")
    pdf.drawString((left + right) / 2 + 4, top - 14, "1,234")
    pdf.drawString(left + 4, bottom + 6, "Total")
    pdf.drawString((left + right) / 2 + 4, bottom + 6, "1,234")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# --- Negative fixtures (§33.9) ----------------------------------------------


def build_truncated_pdf(keep: float = 0.4) -> bytes:
    """A valid PDF cut short mid-stream.

    Detected as a PDF by signature, so it reaches the producer rather than being
    refused at intake — which is the point: the controlled failure being tested
    is extraction's, not validation's.
    """
    whole = build_pdf([[PlacedText("Revenue from operations", x=72, y_from_bottom=700)]])
    return whole[: int(len(whole) * keep)]


def build_malformed_pdf() -> bytes:
    """A PDF header with a structurally broken body.

    Distinct from truncation in what it provokes, not just in shape. PyMuPDF
    *repairs* a broken cross-reference table rather than refusing it, so this
    file opens cleanly and reports zero pages — a silent emptiness rather than
    an error. A truncated file, by contrast, cannot be opened at all. Both are
    controlled failures; only one of them raises.
    """
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 99 0 R >>\nendobj\n"
        b"xref\n0 1\n0000000000 65535 f \n"
        b"trailer\n<< /Size 1 /Root 1 0 R >>\n"
        b"startxref\n999999\n"
        b"%%EOF\n"
    )


def build_unsupported_format() -> bytes:
    """A real PNG.

    A genuine signature for a format the allow-list does not admit, so intake
    refuses it on what the bytes are rather than on what they are called
    (§30.5). Not a PDF pretending to be an image, and not random bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()
