"""A deterministic generator, so CI exercises the pipeline without a model.

Ollama is host-native and absent from CI (ENV-009 open item), so every test above the
adapter runs against this. It is a fixture, not a model: it produces a response that
*satisfies the schema's required keys* and says nothing about the prompt's meaning.

**It must never look clever.** A fake that paraphrased the evidence would make the Evidence
Gate's tests pass for the wrong reason — the Gate exists to catch a model that invents a
numeral or cites a passage it was not given, and a well-behaved fake never does either. So
the failure modes are reachable on request: ``invent_numeral`` and ``cite_outside`` make it
misbehave exactly the way a real model does, which is what the Gate's tests need.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from finsight.generation.port import (
    GenerationRequest,
    GenerationResult,
    GenerationShapeError,
    GenerationTruncatedError,
    GenerationUnavailableError,
)

__all__ = ["MODEL", "FakeGenerator"]

MODEL: Final = "fake-generator-1"


@dataclass
class FakeGenerator:
    """Returns a canned, schema-shaped payload and records what it was asked."""

    claims: Sequence[str] = ("The company describes its approach to the matter asked about.",)
    citations: Sequence[int] = (1,)

    answerable: bool = True
    """Whether the canned response claims the passages answer the question.

    Emitted because the contract requires it: the first version of this fake omitted the field
    and nothing noticed until the answer path ran end to end, where ``parse_answer`` rejected
    every response as a shape error. A fake whose payload the parser refuses tests the error
    path and nothing else.
    """

    fails: bool = False
    """Raise :class:`GenerationUnavailableError`, for §27.9's degradation path."""

    malformed: bool = False
    """Return a payload missing the required key, for the shape path."""

    truncated: bool = False
    """Raise :class:`GenerationTruncatedError`, for the silent-truncation path."""

    invent_numeral: str | None = None
    """Append a numeral no placeholder produced, which the Gate must refuse (§27.3)."""

    cite_outside: int | None = None
    """Cite an identifier outside the evidence set, which the Gate must refuse (§27.6)."""

    calls: list[GenerationRequest] = field(default_factory=list)

    @property
    def model(self) -> str:
        return MODEL

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        if self.fails:
            raise GenerationUnavailableError("fake generator was asked to fail")
        if self.truncated:
            raise GenerationTruncatedError("fake generator was asked to report truncation")
        if self.malformed:
            return GenerationResult(payload={"unexpected": []}, model=MODEL)

        citations = list(self.citations)
        if self.cite_outside is not None:
            citations = [*citations, self.cite_outside]

        bodies = [
            text
            if self.invent_numeral is None
            else f"{text} The figure was {self.invent_numeral}."
            for text in self.claims
        ]
        claims: list[dict[str, object]] = [
            {"text": body, "citations": citations} for body in bodies
        ]

        if not request.schema:
            raise GenerationShapeError("a request carried no schema")

        return GenerationResult(
            payload={"answerable": self.answerable, "claims": claims},
            model=MODEL,
            prompt_tokens=len(request.prompt) // 4,
            completion_tokens=sum(len(body) for body in bodies) // 4,
            elapsed_ms=1,
            context_window=8192,
        )
