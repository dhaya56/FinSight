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

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final

__all__ = [
    "DEMO_ISSUERS",
    "DemoDocument",
    "DemoExperiment",
    "DemoFact",
    "EvaluationSummary",
    "answer_preview",
    "demo_documents",
    "demo_experiments",
    "demo_facts",
    "evaluation_summary",
    "ingest_stages",
    "ledger_concepts",
]

DEMO_ISSUERS: Final = (
    "Meridian Industries Limited",
    "Calder Financial Services Limited",
    "Northwind Technologies Limited",
)

# Concepts the Fact Ledger is bounded to. The catalogue is the point: §7 restricts the
# ledger to an approved set, and anything outside it stays source-bound narrative.
ledger_concepts: Final = (
    "Revenue from operations",
    "Profit before tax",
    "Profit after tax",
    "Total assets",
    "Total equity",
    "Earnings per share (basic)",
    "Dividend per share",
    "Return on net worth",
)


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


@dataclass(frozen=True, slots=True)
class DemoFact:
    """One canonical fact, carrying the dimensions §7 requires to be preserved."""

    concept: str
    issuer: str
    period: str
    basis: str
    value: Decimal
    currency: str
    unit: str
    value_state: str
    assurance: str
    as_presented: bool
    source: str


@dataclass(frozen=True, slots=True)
class DemoExperiment:
    """One recorded experiment, as the evaluation surface would list it."""

    identifier: str
    changed_factor: str
    baseline: str
    candidate: str
    sample: int
    baseline_score: float
    candidate_score: float
    decision: str
    recorded: date
    metric: str = "recall@10"
    notes: str = ""

    @property
    def delta(self) -> float:
        return round(self.candidate_score - self.baseline_score, 4)


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


def demo_facts(issuer: str = "Meridian Industries Limited") -> list[DemoFact]:
    """Canonical facts for the ledger preview."""
    facts = [
        DemoFact(
            concept=concept,
            issuer=issuer,
            period=period,
            basis=basis,
            value=Decimal(value),
            currency="INR",
            unit="crore",
            value_state="reported",
            assurance="audited",
            as_presented=True,
            source=f"p. {120 + index * 3}, Statement of profit and loss",
        )
        for index, (concept, period, basis, value) in enumerate(_FACT_ROWS)
    ]
    facts.extend(
        [
            DemoFact(
                concept="Earnings per share (basic)",
                issuer=issuer,
                period="FY2024-25",
                basis="consolidated",
                value=Decimal("174.20"),
                currency="INR",
                unit="per share",
                value_state="reported",
                assurance="audited",
                as_presented=True,
                source="p. 148, Note 2.18 Earnings per equity share",
            ),
            DemoFact(
                concept="Dividend per share",
                issuer=issuer,
                period="FY2024-25",
                basis="standalone",
                value=Decimal("46.00"),
                currency="INR",
                unit="per share",
                value_state="reported",
                assurance="audited",
                as_presented=True,
                source="p. 92, Directors' report",
            ),
            DemoFact(
                concept="Return on net worth",
                issuer=issuer,
                period="FY2024-25",
                basis="consolidated",
                value=Decimal("18.57"),
                currency="INR",
                unit="percent",
                value_state="derived",
                assurance="unaudited",
                as_presented=False,
                source="Computed: profit after tax / total equity",
            ),
        ]
    )
    return facts


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


def demo_experiments() -> list[DemoExperiment]:
    """Recorded experiments for the evaluation surface."""
    return [
        DemoExperiment(
            identifier="EXP-004",
            changed_factor="Embedding model",
            baseline="nomic-embed-text",
            candidate="BGE-M3",
            sample=120,
            baseline_score=0.812,
            candidate_score=0.847,
            decision="Candidate leads; not admitted without a second run",
            recorded=date(2026, 9, 30),
            notes="Development split only. Held-out never opened.",
        ),
        DemoExperiment(
            identifier="EXP-003",
            changed_factor="Reranker",
            baseline="MiniLM-L-6-v2",
            candidate="BGE reranker base",
            sample=120,
            baseline_score=0.794,
            candidate_score=0.801,
            decision="Within noise at this sample size; no change",
            recorded=date(2026, 9, 24),
        ),
        DemoExperiment(
            identifier="EXP-002",
            changed_factor="Fusion constant k",
            baseline="k=60",
            candidate="k=20",
            sample=120,
            baseline_score=0.812,
            candidate_score=0.788,
            decision="Baseline retained",
            recorded=date(2026, 9, 18),
            notes="Negative result, recorded rather than discarded.",
        ),
        DemoExperiment(
            identifier="EXP-001",
            changed_factor="Chunking method",
            baseline="Fixed 512-token window",
            candidate="Heading-path with parent windows",
            sample=80,
            baseline_score=0.701,
            candidate_score=0.812,
            decision="Candidate adopted",
            recorded=date(2026, 9, 9),
        ),
    ]


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Headline figures for the evaluation surface.

    A dataclass rather than a mapping so each field keeps its type: a ``dict`` of mixed
    values types every member as ``object``, and every arithmetic or ``len`` call on one
    then needs a cast that hides a genuine mistake just as well as a spurious one.
    """

    questions: int
    answerable: int
    unanswerable_probes: int
    citation_resolution: float
    refusal_precision: float
    recall_at_10: float
    recall_trend: tuple[float, ...]
    latency_trend: tuple[float, ...]


def evaluation_summary() -> EvaluationSummary:
    """Headline figures for the evaluation surface."""
    return EvaluationSummary(
        questions=120,
        answerable=104,
        unanswerable_probes=16,
        citation_resolution=1.0,
        refusal_precision=0.94,
        recall_at_10=0.812,
        recall_trend=(0.70, 0.72, 0.75, 0.78, 0.79, 0.81, 0.81),
        latency_trend=(3.1, 2.9, 2.6, 2.5, 2.3, 2.2, 2.1),
    )


@dataclass(frozen=True, slots=True)
class AnswerPreview:
    """A composed answer, as Phase 8 would release one."""

    question: str
    sentences: list[tuple[str, list[int]]] = field(default_factory=list)
    support_band: str = "Strong"
    gate_checks: list[tuple[str, bool, str]] = field(default_factory=list)
    model: str = "llama3.1:8b"
    numerals_verified: int = 0
    """Numerals found in a span their own claim cites (§26.5, ADR-009)."""

    refused_numerals: int = 0


def answer_preview(question: str) -> AnswerPreview:
    """A generated answer with its citations and Evidence Gate result.

    The claim/citation pairing is the shape the generation phase produces: the model emits
    claims with citation references, code resolves each reference to the stored source span,
    and a numeral is released only if it appears in a span its own claim cites (ADR-009).
    """
    return AnswerPreview(
        question=question,
        sentences=[
            (
                "Meridian describes credit risk as the risk of financial loss where a "
                "customer or counterparty fails to meet its contractual obligations.",
                [1],
            ),
            (
                "Exposure arises principally from trade receivables and from deposits "
                "held with banks.",
                [1, 2],
            ),
            (
                "The company reports a loss allowance of INR 412.00 crore for "
                "FY2024-25, against INR 386.00 crore in the prior year.",
                [2, 3],
            ),
            (
                "Concentration is described as limited, with no single customer "
                "accounting for more than ten per cent of receivables.",
                [3],
            ),
        ],
        support_band="Strong",
        gate_checks=[
            ("Every numeral appears in a span its claim cites", True, "3 of 3 verified"),
            ("No numeral introduced by the model", True, "0 unbound numerals"),
            ("Every citation resolves inside the evidence set", True, "4 of 4 resolved"),
            ("Issuer, period and basis match the claim", True, "FY2024-25, standalone"),
            ("Conflicting figures disclosed with their periods", True, "no conflict found"),
            ("Arithmetic refused on non-ledger values", True, "no computation attempted"),
        ],
        numerals_verified=3,
        refused_numerals=0,
    )
