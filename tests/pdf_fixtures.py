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
