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
from reportlab.lib.pagesizes import A4
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
