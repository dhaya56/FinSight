"""The generation contract: shape enforced, semantics deliberately not.

The temptation here is to reject things the Evidence Gate should reject — a citation outside
the set, a claim with no citations — and these tests pin that it does not, because moving that
judgement into parsing would let a future adapter skip it.
"""

import pytest

from finsight.generation.contract import (
    ANSWER_SCHEMA,
    CLAIM_FIELDS,
    CONTRACT_FIELDS,
    GeneratedAnswer,
    GeneratedClaim,
    parse_answer,
)
from finsight.generation.port import GenerationShapeError


class TestParsing:
    def test_a_well_formed_answer_parses(self) -> None:
        answer = parse_answer(
            {"answerable": True, "claims": [{"text": "a claim.", "citations": [1, 2]}]}
        )

        assert answer.answerable is True
        assert answer.claims[0].text == "a claim."
        assert answer.claims[0].citations == [1, 2]

    def test_a_refusal_parses(self) -> None:
        answer = parse_answer({"answerable": False, "claims": []})

        assert answer.answerable is False
        assert answer.claims == []

    def test_cited_ids_collects_every_reference(self) -> None:
        answer = parse_answer(
            {
                "answerable": True,
                "claims": [
                    {"text": "one.", "citations": [1, 2]},
                    {"text": "two.", "citations": [2, 5]},
                ],
            }
        )

        assert answer.cited_ids == {1, 2, 5}

    def test_claims_default_to_empty(self) -> None:
        assert parse_answer({"answerable": False}).claims == []


class TestRejections:
    def test_a_missing_answerable_flag_is_refused(self) -> None:
        with pytest.raises(GenerationShapeError, match="answerable"):
            parse_answer({"claims": []})

    def test_an_unknown_field_is_refused(self) -> None:
        """extra='forbid': a renamed field would otherwise parse as an empty answer."""
        with pytest.raises(GenerationShapeError):
            parse_answer({"answerable": True, "claims": [], "commentary": "hello"})

    def test_an_empty_claim_text_is_refused(self) -> None:
        with pytest.raises(GenerationShapeError):
            parse_answer({"answerable": True, "claims": [{"text": "", "citations": [1]}]})

    def test_a_non_object_payload_is_refused(self) -> None:
        with pytest.raises(GenerationShapeError):
            parse_answer(["not", "an", "object"])

    def test_a_non_integer_citation_is_refused(self) -> None:
        with pytest.raises(GenerationShapeError):
            parse_answer(
                {"answerable": True, "claims": [{"text": "x.", "citations": ["one"]}]}
            )

    def test_the_error_names_where_the_problem_was(self) -> None:
        with pytest.raises(GenerationShapeError, match=r"claims\.0\.text"):
            parse_answer({"answerable": True, "claims": [{"citations": [1]}]})


class TestSemanticsAreNotJudgedHere:
    """Each of these is a real failure the Evidence Gate removes, not a parse error."""

    def test_a_claim_citing_nothing_parses(self) -> None:
        """§27.3 removes it. Rejecting here would lose the properly cited claims beside it."""
        answer = parse_answer(
            {"answerable": True, "claims": [{"text": "unsupported.", "citations": []}]}
        )

        assert answer.claims[0].citations == []

    def test_a_citation_outside_any_evidence_set_parses(self) -> None:
        """The set is not known at this layer, so it cannot be checked here."""
        answer = parse_answer(
            {"answerable": True, "claims": [{"text": "x.", "citations": [47]}]}
        )

        assert answer.cited_ids == {47}

    def test_answerable_with_no_claims_parses(self) -> None:
        """A contradiction the Gate reads as abstention (§27.11)."""
        assert parse_answer({"answerable": True, "claims": []}).claims == []


class TestSchema:
    def test_the_schema_carries_no_unresolved_references(self) -> None:
        """A runtime that ignores $ref would constrain nothing, and look fine doing it."""
        rendered = repr(ANSWER_SCHEMA)

        assert "$ref" not in rendered
        assert "$defs" not in rendered

    def test_the_schema_requires_both_top_level_fields(self) -> None:
        """Stricter than the parser, on purpose.

        Pydantic leaves a defaulted field out of ``required``, so without this the runtime
        would not oblige the model to emit ``claims`` — and a response missing it looks
        exactly like one that found nothing.
        """
        assert set(ANSWER_SCHEMA["required"]) == {"answerable", "claims"}

    def test_the_parser_stays_lenient_where_the_schema_is_strict(self) -> None:
        """The two layers answer different questions and may disagree."""
        assert parse_answer({"answerable": False}).claims == []

    def test_the_claim_shape_is_inlined(self) -> None:
        items = ANSWER_SCHEMA["properties"]["claims"]["items"]

        assert set(items["properties"]) == {"text", "citations"}

    def test_the_schema_is_an_object(self) -> None:
        assert ANSWER_SCHEMA["type"] == "object"


class TestFieldNamesAgree:
    def test_the_answer_fields_match_the_prompt(self) -> None:
        """Two literals in two modules. A drift produces a valid-looking empty answer."""
        assert set(GeneratedAnswer.model_fields) == set(CONTRACT_FIELDS)

    def test_the_claim_fields_match_the_prompt(self) -> None:
        assert set(GeneratedClaim.model_fields) == set(CLAIM_FIELDS)
