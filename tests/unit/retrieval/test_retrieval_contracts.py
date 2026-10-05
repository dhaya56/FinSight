"""The filter payload: what it always carries, and what it refuses to widen."""

from uuid import uuid4

import pytest

from finsight.retrieval.contracts import (
    GENERATION_FIELD,
    Candidate,
    LexicalResult,
    RetrievalFilters,
    Retriever,
)
from finsight.vector_index.port import Span


class TestGenerationBound:
    def test_the_generation_filter_is_always_present(self) -> None:
        """§20.2 and §11.13: a reader may only see active generations.

        Not the caller's to supply and not the caller's to omit, because the index
        legitimately holds superseded and still-building points.
        """
        generation = uuid4()

        payload = RetrievalFilters().as_payload(generations=[generation])

        assert payload[GENERATION_FIELD] == [str(generation)]

    def test_several_generations_become_an_any_of(self) -> None:
        """Three filings, three active generations, one search across them."""
        generations = [uuid4(), uuid4(), uuid4()]

        payload = RetrievalFilters().as_payload(generations=generations)

        assert payload[GENERATION_FIELD] == [str(g) for g in generations]

    def test_no_active_generation_yields_an_empty_set_not_an_absent_filter(
        self,
    ) -> None:
        """An empty list matches nothing; an absent key would match everything.

        This is the distinction that keeps a half-built index from being served.
        """
        payload = RetrievalFilters().as_payload(generations=[])

        assert GENERATION_FIELD in payload
        assert payload[GENERATION_FIELD] == []


class TestOptionalFields:
    def test_an_unset_filter_is_absent_rather_than_null(self) -> None:
        """A key with a null value would match only documents missing the field."""
        payload = RetrievalFilters().as_payload(generations=[uuid4()])

        assert set(payload) == {GENERATION_FIELD}

    def test_every_dimension_reaches_the_payload(self) -> None:
        version = uuid4()
        filters = RetrievalFilters(
            issuer_name="Infosys Limited",
            document_type="annual_report",
            fiscal_period="FY2024-25",
            fiscal_year=2025,
            reporting_basis="both",
            evidence_type="narrative",
            section="7. Risk factors",
            document_version_id=version,
        )

        payload = filters.as_payload(generations=[uuid4()])

        assert payload["issuer_name"] == "Infosys Limited"
        assert payload["document_type"] == "annual_report"
        assert payload["fiscal_period"] == "FY2024-25"
        assert payload["fiscal_year"] == 2025
        assert payload["reporting_basis"] == "both"
        assert payload["evidence_type"] == "narrative"
        assert payload["section"] == "7. Risk factors"
        assert payload["document_version_id"] == str(version)

    def test_a_year_span_is_carried_as_a_span(self) -> None:
        """Flattening it to a value here would lose the range."""
        filters = RetrievalFilters(fiscal_year=Span(low=2023))

        payload = filters.as_payload(generations=[uuid4()])

        assert payload["fiscal_year"] == Span(low=2023)

    def test_identifiers_are_stringified_for_the_payload(self) -> None:
        """Qdrant payload values are scalars; a UUID object would not match."""
        version = uuid4()

        payload = RetrievalFilters(document_version_id=version).as_payload(
            generations=[uuid4()]
        )

        assert isinstance(payload["document_version_id"], str)


class TestSpan:
    def test_a_span_needs_a_bound(self) -> None:
        with pytest.raises(ValueError, match="at least one bound"):
            Span()

    def test_an_inverted_span_is_refused(self) -> None:
        """It would match nothing, which is never what a caller meant."""
        with pytest.raises(ValueError, match="empty"):
            Span(low=2025, high=2023)

    def test_one_sided_spans_are_allowed(self) -> None:
        assert Span(low=2023).high is None
        assert Span(high=2023).low is None


class TestResult:
    def test_a_result_without_flags_is_not_degraded(self) -> None:
        result = LexicalResult(candidates=(), retriever=Retriever.BM25)

        assert result.is_degraded is False

    def test_a_flagged_result_is_degraded(self) -> None:
        result = LexicalResult(
            candidates=(),
            retriever=Retriever.POSTGRES_FTS,
            degraded=("lexical_fallback_postgres_fts",),
        )

        assert result.is_degraded is True

    def test_a_candidate_carries_no_text(self) -> None:
        """§10.7: the vector store is not truth; text comes from PostgreSQL."""
        candidate = Candidate(
            chunk_id=uuid4(), score=1.0, rank=1, retriever=Retriever.BM25
        )

        assert not hasattr(candidate, "text")
