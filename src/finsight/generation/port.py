"""The generation boundary: a protocol, its errors, and nothing else.

No module here imports an HTTP client or a model runtime, for the same reason the
embedding port does not: §22 leaves the generation model unselected, and ADR-008 adopts
one provisionally rather than choosing it.

**The schema goes to the server, and that is the whole design.** §26.3 wants the model to
return a typed intermediate rather than prose, and asking an 8-billion-parameter model
politely for JSON produces invalid JSON often enough to need a repair path. Measured
against the running service, passing a JSON schema constrains decoding and the response
parsed cleanly on every attempt. So the contract is enforced at the boundary instead of
hoped for, and a caller receives a parsed object rather than a string it must re-judge.

**Two layers of validation, deliberately.** This port guarantees only that the response
was JSON. Whether it is a *valid* generation contract — citation references that resolve
inside the evidence set, numerals present in the spans they cite — is decided above, by
Pydantic and then by the Evidence Gate (§26.4, §27). A port that validated meaning would
put the security boundary in the transport layer, where a new adapter could quietly omit
it.

**The model has no capabilities** (§10.4): no tools, no function calling, no filesystem,
no network of its own. An adapter that enabled any of those would break the trust boundary
this port exists to draw, and the Gate's guarantees are stated against a model that can
only emit text.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "GenerationError",
    "GenerationRequest",
    "GenerationResult",
    "GenerationShapeError",
    "GenerationTruncatedError",
    "GenerationUnavailableError",
    "Generator",
    "JsonSchema",
]

JsonSchema = Mapping[str, object]
"""A JSON Schema the runtime is asked to constrain its output to."""


class GenerationError(RuntimeError):
    """Base class for failures at the generation boundary."""


class GenerationUnavailableError(GenerationError):
    """The model could not be reached, or did not answer in time.

    Retryable, and §27.9 requires it to be disclosed as a *degradation* rather than as a
    reason the question could not be answered. The two are different facts: one is about
    this deployment, the other about the evidence, and conflating them tells a reader the
    corpus lacks something when the model was simply down.

    §26.10 defines the fallback — render the facts, citations and context deterministically
    with no prose — so losing the model costs fluency, not the answer.
    """


class GenerationShapeError(GenerationError):
    """The response was not the JSON the schema asked for.

    Not survivable by retrying the same request: either the runtime ignored the schema or
    the schema is wrong, and both mean the typed contract (§26.3) is not in force. Raised
    rather than parsed leniently, because a partially-understood contract is how a claim's
    citations go unread and its numerals reach a reader unchecked.
    """


class GenerationTruncatedError(GenerationError):
    """The prompt did not fit the context window, so evidence was silently dropped.

    **The failure this names is silent and specific.** Ollama truncates a prompt longer
    than ``num_ctx`` without erroring — the same behaviour measured on the embedding path,
    where appending a sentence to an over-length passage left the vector bit-identical. A
    truncated *generation* prompt is worse: the model answers from the evidence that
    survived while the citations still name everything that was sent, so the answer looks
    fully supported and is not.

    Detected after the fact, from the token counts the runtime reports, because nothing
    here can tokenise for a model it must not import.
    """


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """One constrained generation call."""

    prompt: str
    """The complete prompt, assembled by §26.1's construction and already delimited.

    This port does no assembly. A boundary that formatted prompts would be a boundary that
    decides what the model sees, and §26.2's isolation of document content as data is a
    property of the prompt, checked where it is built.
    """

    schema: JsonSchema
    """The JSON Schema the response must satisfy."""

    max_tokens: int = 800
    """Upper bound on the completion. Unmeasured.

    Present so a runaway generation cannot hold a request open for minutes. Measured on
    this host the model emits roughly 3.9 tokens per second, so this bound is also a
    latency bound: 800 tokens is about three and a half minutes in the worst case.
    """


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """A parsed response and what it cost."""

    payload: Mapping[str, object]
    """The decoded JSON. Shape beyond "it is an object" is not this layer's business."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: int = 0

    context_window: int = 0
    """The window the request was made under, recorded so truncation is checkable.

    Carried on the result rather than assumed by the caller: the window is a property of
    the adapter's configuration, and a caller comparing token counts against its own idea
    of the window would compare against the wrong number after a settings change.
    """

    degraded: tuple[str, ...] = field(default_factory=tuple)
    """Flags describing how this call fell short, for §27.9's separate disclosure."""


@runtime_checkable
class Generator(Protocol):
    """Turns a prompt into a parsed, schema-constrained object."""

    @property
    def model(self) -> str:
        """The model identifier, recorded on every answer.

        §26.11 stores plans, evidence and decisions rather than chain-of-thought, and this
        is part of that record: an answer whose model is unknown cannot be reproduced or
        compared, and §22's selection has nothing to select between.
        """
        ...

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Produce a parsed object satisfying ``request.schema``.

        Raises:
            GenerationUnavailableError: the runtime could not be reached or timed out.
            GenerationShapeError: the response was not JSON matching the schema.
            GenerationTruncatedError: the prompt exceeded the context window, so the
                model did not see all of the evidence.
        """
        ...
