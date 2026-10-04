"""BM25 weighting, and the silent failures it is shaped to avoid.

Every test here corresponds to a way lexical retrieval can return nothing, or
return the wrong order, while every component reports success.
"""

import math

import pytest

from finsight.lexical.bm25 import (
    BM25Config,
    SparseVector,
    document_vector,
    parse_tsvector,
    query_vector,
    term_index,
)


class TestParseTsvector:
    def test_positions_are_counted_as_occurrences(self) -> None:
        assert parse_tsvector("'revenu':1,5 'oper':3") == (
            ("revenu", 2),
            ("oper", 1),
        )

    def test_an_escaped_apostrophe_does_not_terminate_the_lexeme(self) -> None:
        """PostgreSQL writes a literal apostrophe as ``''`` inside the quotes.

        Reading it as a closing quote would split one lexeme into two and parse the
        remainder of the vector as nonsense — a corruption that grows with the
        document rather than staying local.
        """
        assert parse_tsvector("'shareholder''s':2") == (("shareholder's", 1),)

    def test_an_entry_without_positions_counts_once(self) -> None:
        """A stripped vector has no positions; zero occurrences would drop the term."""
        assert parse_tsvector("'bare'") == (("bare", 1),)

    def test_weight_labels_do_not_lose_the_positions(self) -> None:
        """``setweight`` adds a label. Not emitted today, admitted so it parses."""
        assert parse_tsvector("'revenu':1A,5B") == (("revenu", 2),)

    def test_an_empty_vector_is_an_empty_result(self) -> None:
        """A real outcome: eight development chunks analyse to no lexemes."""
        assert parse_tsvector("") == ()


class TestTermIndex:
    def test_the_index_is_stable_across_processes(self) -> None:
        """Pinned literals, because this is the failure that looks like health.

        Python's ``hash`` is salted per process, so a salted index would map a word
        one way in the indexer and another in the API, and every lexical query would
        return nothing with no error anywhere. These values must not change without
        a full re-index.
        """
        assert term_index("revenu") == 582199554
        assert term_index("oper") == 2131045380
        assert term_index("credit") == 1188487522
        assert term_index("risk") == 1405333799

    def test_the_index_fits_an_unsigned_32_bit_field(self) -> None:
        """Qdrant sparse indices are u32; a wider value is rejected on upsert."""
        for word in ("revenu", "oper", "a", "", "शेयर", "x" * 500):
            assert 0 <= term_index(word) < 1 << 32

    def test_different_lexemes_get_different_indices(self) -> None:
        words = ("revenu", "oper", "credit", "risk", "profit", "loss", "tax")
        assert len({term_index(word) for word in words}) == len(words)


class TestDocumentVector:
    def test_term_frequency_saturates(self) -> None:
        """Ten occurrences must not weigh ten times one.

        Without saturation a chunk repeating a term outranks a chunk that discusses
        it, which in a filing means boilerplate outranking the note that explains
        it.
        """
        once = document_vector("'revenu':1")
        ten = document_vector("'revenu':" + ",".join(str(n) for n in range(1, 11)))

        assert ten.values[0] > once.values[0]
        assert ten.values[0] < 10 * once.values[0]
        assert ten.values[0] < BM25Config().k1 + 1.0

    def test_a_longer_document_weighs_the_same_term_less(self) -> None:
        """Length normalisation, which is the factor that matters on this corpus.

        Development chunk length runs p10 5 to p90 191 positions, so without this a
        long chunk would win on term count alone.
        """
        short = document_vector("'revenu':1 'oper':2")
        padding = " ".join(f"'w{n}':{n + 3}" for n in range(200))
        long = document_vector(f"'revenu':1 'oper':2 {padding}")

        index = term_index("revenu")
        assert short.values[short.indices.index(index)] > (
            long.values[long.indices.index(index)]
        )

    def test_the_weights_match_the_formula(self) -> None:
        """Computed independently here rather than compared against a recorded run."""
        config = BM25Config()
        raw = "'revenu':1,5 'oper':3"
        length = 3
        denominator = config.k1 * (
            1.0 - config.b + config.b * length / config.average_document_length
        )

        vector = document_vector(raw, config=config)

        expected = {
            term_index("revenu"): 2 * (config.k1 + 1.0) / (2 + denominator),
            term_index("oper"): 1 * (config.k1 + 1.0) / (1 + denominator),
        }
        for index, value in zip(vector.indices, vector.values, strict=True):
            assert math.isclose(value, expected[index])

    def test_indices_are_ascending(self) -> None:
        vector = document_vector("'zebra':1 'apple':2 'mango':3")

        assert list(vector.indices) == sorted(vector.indices)

    def test_a_term_free_chunk_produces_no_sparse_vector(self) -> None:
        """Dense-searchable, lexically absent. Not an error (§20.12)."""
        vector = document_vector("")

        assert len(vector) == 0

    def test_b_of_zero_disables_length_normalisation(self) -> None:
        """The parameter has to actually reach the formula."""
        config = BM25Config(b=0.0, version="probe")
        short = document_vector("'revenu':1", config=config)
        long = document_vector(
            "'revenu':1 " + " ".join(f"'w{n}':{n + 2}" for n in range(100)),
            config=config,
        )

        index = term_index("revenu")
        assert math.isclose(
            short.values[short.indices.index(index)],
            long.values[long.indices.index(index)],
        )


class TestQueryVector:
    def test_every_term_carries_unit_weight(self) -> None:
        """BM25 has no query-frequency factor; the document side carries the tf."""
        vector = query_vector("'credit':1 'risk':2")

        assert set(vector.values) == {1.0}
        assert len(vector) == 2

    def test_a_repeated_query_term_is_not_counted_twice(self) -> None:
        vector = query_vector("'risk':1,2,3")

        assert len(vector) == 1

    def test_a_query_with_no_terms_is_empty_rather_than_an_error(self) -> None:
        """"the and of" analyses to nothing. The dense side still answers."""
        assert len(query_vector("")) == 0

    def test_a_query_term_matches_the_index_a_document_term_got(self) -> None:
        """The two sides must agree, or the whole lexical path returns nothing."""
        document = document_vector("'credit':1 'risk':2")
        query = query_vector("'risk':1")

        assert query.indices[0] in document.indices


class TestSparseVector:
    def test_mismatched_pairs_are_refused(self) -> None:
        """A shifted pairing scores nonsense while looking perfectly healthy."""
        with pytest.raises(ValueError, match="meaningless"):
            SparseVector(indices=(1, 2), values=(1.0,))


class TestConfig:
    def test_a_non_positive_average_length_is_refused(self) -> None:
        """It is a divisor; zero would raise far from the cause."""
        with pytest.raises(ValueError, match="average_document_length"):
            BM25Config(average_document_length=0.0)

    def test_b_outside_the_unit_interval_is_refused(self) -> None:
        with pytest.raises(ValueError, match="b must"):
            BM25Config(b=1.5)

    def test_the_measured_average_is_carried_as_the_default(self) -> None:
        """Pinned so a change has to be deliberate: it invalidates every weight."""
        assert BM25Config().average_document_length == 99.21
        assert BM25Config().version == "1"
