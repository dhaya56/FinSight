"""Translating filter values into Qdrant conditions.

Unit-testable because ``_as_filter`` is pure: it builds a request object and talks to
nothing. The reason to test it directly is that each value shape fails differently
and silently — a range read as any-of excludes the middle of its own interval, and an
empty sequence read as "no filter" returns the whole collection.
"""

import pytest
from qdrant_client import models

from finsight.vector_index.port import Span, VectorIndexShapeError
from finsight.vector_index.qdrant_index import _as_filter


def conditions(built: models.Filter | None) -> list:
    assert built is not None
    assert built.must is not None
    return list(built.must)


class TestShapes:
    def test_a_scalar_becomes_an_exact_match(self) -> None:
        built = _as_filter({"issuer_name": "Infosys Limited"})

        condition = conditions(built)[0]
        assert condition.key == "issuer_name"
        assert condition.match == models.MatchValue(value="Infosys Limited")

    def test_a_sequence_becomes_any_of(self) -> None:
        """How a search is bounded to the set of active generations."""
        built = _as_filter({"generation_id": ["a", "b"]})

        condition = conditions(built)[0]
        assert condition.match == models.MatchAny(any=["a", "b"])

    def test_a_span_becomes_a_range(self) -> None:
        built = _as_filter({"fiscal_year": Span(low=2023, high=2025)})

        condition = conditions(built)[0]
        assert condition.range == models.Range(gte=2023, lte=2025)

    def test_a_one_sided_span_leaves_the_other_bound_open(self) -> None:
        built = _as_filter({"fiscal_year": Span(low=2023)})

        assert conditions(built)[0].range == models.Range(gte=2023, lte=None)

    def test_an_integer_sequence_stays_integral(self) -> None:
        """Stringifying page numbers would not match an integer payload index."""
        built = _as_filter({"page_numbers": [41, 42]})

        assert conditions(built)[0].match == models.MatchAny(any=[41, 42])

    def test_an_integer_scalar_stays_integral(self) -> None:
        built = _as_filter({"fiscal_year": 2025})

        assert conditions(built)[0].match == models.MatchValue(value=2025)


class TestBoundaries:
    def test_no_filters_means_no_filter_object(self) -> None:
        assert _as_filter(None) is None
        assert _as_filter({}) is None

    def test_an_empty_sequence_matches_nothing_rather_than_everything(self) -> None:
        """It arises when no generation is active, and must not widen the search.

        A filter object with an empty any-of is what makes "nothing may be returned"
        expressible; dropping the key would serve the whole collection.
        """
        built = _as_filter({"generation_id": []})

        assert conditions(built)[0].match == models.MatchAny(any=[])

    def test_every_field_is_required_together(self) -> None:
        """AND across fields. An OR would let a close vector escape its scope (§7)."""
        built = _as_filter(
            {"issuer_name": "Infosys Limited", "fiscal_year": 2025}
        )

        assert built is not None
        assert len(conditions(built)) == 2
        assert built.should is None

    def test_an_unsupported_value_is_refused(self) -> None:
        """Rather than being coerced into a match that means something else."""
        with pytest.raises(VectorIndexShapeError, match="not a scalar"):
            _as_filter({"issuer_name": {"nested": "mapping"}})
