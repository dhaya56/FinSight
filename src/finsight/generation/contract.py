"""The typed intermediate a model is constrained to produce (§26.3, §26.4).

**Structure only.** This layer decides whether the response has the right shape; it decides
nothing about whether the content is true. A citation that names a passage outside the
evidence set is structurally valid and semantically wrong, and catching it here would put the
Evidence Gate's job in the parsing layer where a new adapter could skip it.

**One source of truth for field names.** The prompt names the fields in prose and the schema
constrains them, so a drifted name would produce a schema-valid response the gate reads as an
empty answer. The names come from :mod:`finsight.generation.prompt` and a test asserts the
model's fields match them.

**The schema is flattened before it reaches the runtime.** Pydantic emits ``$ref`` and
``$defs`` for a nested model, and a constrained-decoding implementation that does not resolve
references would silently constrain nothing — the worst outcome available, because the
response would look fine until a malformed one arrived. Inlining is cheap and removes the
question.
"""

from copy import deepcopy
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finsight.generation.port import GenerationShapeError
from finsight.generation.prompt import (
    ANSWERABLE_FIELD,
    CITATIONS_FIELD,
    CLAIMS_FIELD,
    TEXT_FIELD,
)

__all__ = [
    "ANSWER_SCHEMA",
    "GeneratedAnswer",
    "GeneratedClaim",
    "parse_answer",
]


class GeneratedClaim(BaseModel):
    """One assertion the model makes, and the passages it rests on."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="One self-contained sentence.")
    citations: list[int] = Field(
        default_factory=list,
        description="Passage ids this claim rests on.",
    )
    """Allowed to be empty at this layer.

    A claim citing nothing is a real thing a model produces and §27.3 removes it. Rejecting
    it here would turn one unsupported sentence into a failed answer, losing the claims that
    were properly cited alongside it.
    """


class GeneratedAnswer(BaseModel):
    """The complete response, before any of it is believed."""

    model_config = ConfigDict(extra="forbid")

    answerable: bool = Field(
        description="Whether the passages answer the question."
    )
    claims: list[GeneratedClaim] = Field(default_factory=list)
    """May be empty while ``answerable`` is true, and that contradiction is not rejected here.

    The Gate owns decisions (§27.11), and a model that says yes then says nothing is
    abstaining in substance. Raising would make the parsing layer decide an answer's outcome.
    """

    @property
    def cited_ids(self) -> set[int]:
        """Every passage id the response refers to, for the Gate to check against the set."""
        return {
            identifier for claim in self.claims for identifier in claim.citations
        }


def _flatten(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$ref`` pointers so the schema stands alone.

    Only the local ``#/$defs/<name>`` form Pydantic emits is handled; anything else is left
    untouched, because silently rewriting a reference shape this does not understand would be
    worse than passing it through and having the runtime reject it.
    """
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                target = definitions.get(reference.removeprefix("#/$defs/"))
                if isinstance(target, dict):
                    return resolve(deepcopy(target))
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    flattened = resolve(deepcopy(schema))
    return flattened if isinstance(flattened, dict) else schema


def _answer_schema() -> dict[str, Any]:
    """The schema sent to the runtime: flattened, and stricter than the parser.

    **``claims`` is forced to be required, deliberately diverging from the model.** Pydantic
    omits a defaulted field from ``required``, so constrained decoding would not oblige the
    model to emit ``claims`` at all — and a response without it is indistinguishable from one
    that found nothing. The two layers want different strictness: the schema constrains
    *generation*, so it should demand everything; the parser validates what *arrived*, so it
    should tolerate a missing list rather than fail an otherwise good answer.
    """
    schema = _flatten(GeneratedAnswer.model_json_schema())
    required = schema.get("required")
    if isinstance(required, list) and CLAIMS_FIELD not in required:
        schema["required"] = [*required, CLAIMS_FIELD]
    return schema


ANSWER_SCHEMA: Final[dict[str, Any]] = _answer_schema()
"""The schema sent to the runtime, with no unresolved references."""


def parse_answer(payload: object) -> GeneratedAnswer:
    """Validate a decoded response against the contract.

    Raises:
        GenerationShapeError: the payload does not satisfy the contract. Raised rather than
            returning a default, because a response the runtime was asked to constrain and
            did not is a configuration fault — retrying reproduces it, and a default would
            present an empty answer as the model's own.
    """
    try:
        return GeneratedAnswer.model_validate(payload)
    except ValidationError as error:
        raise GenerationShapeError(
            f"the response did not satisfy the generation contract: "
            f"{error.error_count()} problem(s); first at "
            f"{'.'.join(str(part) for part in error.errors()[0]['loc']) or '<root>'}"
        ) from error


# Named here so a drift between the prompt's prose and the model's fields fails a test rather
# than producing a schema-valid response the Gate reads as empty.
CONTRACT_FIELDS: Final = (ANSWERABLE_FIELD, CLAIMS_FIELD)
CLAIM_FIELDS: Final = (TEXT_FIELD, CITATIONS_FIELD)
