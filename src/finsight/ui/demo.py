"""Fixtures for the interface previews.

**Every issuer named here is invented.** Meridian, Calder and Northwind do not exist.
That is a deliberate constraint rather than a stylistic one: a preview panel showing a
plausible revenue figure beside a real company's name is a fabricated financial record,
and it stays fabricated once it is screenshotted out of context. Inventing the company
too means nothing here can be mistaken for a claim about a real issuer.

The real corpus is reachable on the pages that are wired. Those pages name real issuers
because the values come from their filings.

Numbers below are arithmetically self-consistent so the previews do not look broken —
totals add up, growth rates match the values they are computed from — but they measure
nothing. None is a benchmark, a target, or a result.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

__all__ = [
    "DEMO_ISSUERS",
    "DemoDocument",
    "demo_documents",
    "ingest_stages",
]

DEMO_ISSUERS: Final = (
    "Meridian Industries Limited",
    "Calder Financial Services Limited",
    "Northwind Technologies Limited",
)

# Concepts the Fact Ledger is bounded to. The catalogue is the point: §7 restricts the
# ledger to an approved set, and anything outside it stays source-bound narrative.


@dataclass(frozen=True, slots=True)
class DemoDocument:
    """One ingested filing as the library would show it."""

    issuer: str
    document_type: str
    period: str
    fiscal_year: int
    pages: int
    size_mb: float
    ingested: date
    generation_state: str
    chunks: int
    tables_found: int
    reading_order_divergence: float
    split: str


def demo_documents() -> list[DemoDocument]:
    """The library listing."""
    return [
        DemoDocument(
            issuer="Meridian Industries Limited",
            document_type="Annual report",
            period="FY2024-25",
            fiscal_year=2025,
            pages=312,
            size_mb=18.4,
            ingested=date(2026, 9, 12),
            generation_state="active",
            chunks=2841,
            tables_found=174,
            reading_order_divergence=0.11,
            split="development",
        ),
        DemoDocument(
            issuer="Meridian Industries Limited",
            document_type="Annual report",
            period="FY2023-24",
            fiscal_year=2024,
            pages=298,
            size_mb=17.1,
            ingested=date(2026, 9, 12),
            generation_state="active",
            chunks=2655,
            tables_found=168,
            reading_order_divergence=0.09,
            split="development",
        ),
        DemoDocument(
            issuer="Calder Financial Services Limited",
            document_type="Annual report",
            period="FY2024-25",
            fiscal_year=2025,
            pages=286,
            size_mb=22.8,
            ingested=date(2026, 9, 28),
            generation_state="active",
            chunks=2430,
            tables_found=203,
            reading_order_divergence=0.17,
            split="development",
        ),
        DemoDocument(
            issuer="Northwind Technologies Limited",
            document_type="Quarterly results",
            period="Q2 FY2025-26",
            fiscal_year=2026,
            pages=46,
            size_mb=2.9,
            ingested=date(2026, 10, 2),
            generation_state="shadow",
            chunks=398,
            tables_found=31,
            reading_order_divergence=0.06,
            split="development",
        ),
        DemoDocument(
            issuer="Northwind Technologies Limited",
            document_type="Offer document",
            period="FY2024-25",
            fiscal_year=2025,
            pages=504,
            size_mb=41.2,
            ingested=date(2026, 8, 30),
            generation_state="superseded",
            chunks=4120,
            tables_found=288,
            reading_order_divergence=0.23,
            split="held-out",
        ),
    ]


# Values are in crore, which is how Indian filings present them, and are internally
# consistent across periods so a growth column computes to something sensible.
_FACT_ROWS: Final = (
    ("Revenue from operations", "FY2024-25", "consolidated", "48206.00"),
    ("Revenue from operations", "FY2023-24", "consolidated", "43891.00"),
    ("Profit before tax", "FY2024-25", "consolidated", "9642.00"),
    ("Profit before tax", "FY2023-24", "consolidated", "8407.00"),
    ("Profit after tax", "FY2024-25", "consolidated", "7231.00"),
    ("Profit after tax", "FY2023-24", "consolidated", "6305.00"),
    ("Total assets", "FY2024-25", "consolidated", "61480.00"),
    ("Total assets", "FY2023-24", "consolidated", "56120.00"),
    ("Total equity", "FY2024-25", "consolidated", "38940.00"),
    ("Total equity", "FY2023-24", "consolidated", "34118.00"),
)


@dataclass(frozen=True, slots=True)
class IngestStage:
    """One stage of the ingestion pipeline, for the progress preview."""

    key: str
    label: str
    detail: str
    seconds: float
    produces: str


def ingest_stages() -> list[IngestStage]:
    """The stages a document passes through, in order."""
    return [
        IngestStage(
            "validate",
            "Validate",
            "Signature, declared content type, structural limits, archive safety",
            0.4,
            "accepted upload",
        ),
        IngestStage(
            "store",
            "Store original",
            "Immutable object written, SHA-256 recorded, document version created",
            0.9,
            "1 document version",
        ),
        IngestStage(
            "extract",
            "Extract",
            "Isolated parser worker: text blocks, reading order, table regions",
            46.2,
            "4,118 source elements",
        ),
        IngestStage(
            "chunk",
            "Chunk",
            "Heading-path grouping, paragraph and sentence splits, parent windows",
            6.1,
            "2,841 chunks",
        ),
        IngestStage(
            "index",
            "Embed and index",
            "Dense vectors and BM25 sparse terms upserted, outbox drained",
            1464.0,
            "2,841 points",
        ),
        IngestStage(
            "reconcile",
            "Reconcile and activate",
            "Both stores compared, then the generation becomes queryable",
            2.3,
            "generation active",
        ),
    ]


