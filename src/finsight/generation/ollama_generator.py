"""Constrained generation through a host-native Ollama instance.

The only module in the project that calls a generation model. It speaks HTTP to a loopback
address (§9.8, §10.4): nothing is containerised, nothing is exposed to a network, and the
model is given no tools, no function calling and no way to reach a file or a store.

**Determinism is requested, not assumed.** ``temperature`` is zero because §26.7's
substitution is deterministic and an answer that varies between identical runs cannot be
compared, reproduced in an experiment, or explained to a reader who saw a different one.
Zero temperature is not a guarantee — batching and floating-point order can still move a
token — so nothing here claims the output is reproducible, only that the request asks for
the least variance available.

**Truncation is checked after the call, because it cannot be checked before.** Ollama
discards prompt tokens past ``num_ctx`` and reports success. Catching that would need the
model's own tokenizer, and importing one to approximate another is the mistake the
embedding adapter documents at length. Instead the response reports how many prompt tokens
were evaluated, and a count at or above the window means the prompt was cut — so the
failure is raised rather than returned as an answer whose citations overstate what the
model read.
"""

import json
import time
from typing import Any, Final

import httpx

from finsight.config.settings import Settings, get_settings
from finsight.generation.port import (
    GenerationRequest,
    GenerationResult,
    GenerationShapeError,
    GenerationTruncatedError,
    GenerationUnavailableError,
)

__all__ = ["OllamaGenerator", "build_generator"]

_GENERATE_PATH: Final = "/api/generate"

_TRUNCATION_MARGIN: Final = 8
"""Tokens of headroom below ``num_ctx`` before a prompt is called truncated.

Ollama's reported ``prompt_eval_count`` includes template and control tokens the caller
never sent, so an exact comparison against the window would both miss a cut by a token and
cry truncation on a prompt that fitted. A small margin treats "within a handful of the
ceiling" as unsafe, which is the honest reading: a prompt that close is one evidence
passage away from being cut on the next question.
"""


class OllamaGenerator:
    """Generates schema-constrained JSON through Ollama."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        context_window: int,
        temperature: float,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._context_window = context_window
        self._temperature = temperature
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @property
    def model(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window

    def generate(self, request: GenerationRequest) -> GenerationResult:
        body = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": False,
            "format": dict(request.schema),
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._context_window,
                "num_predict": request.max_tokens,
            },
        }

        started = time.perf_counter()
        try:
            response = self._client.post(f"{self._base_url}{_GENERATE_PATH}", json=body)
            response.raise_for_status()
            envelope: Any = response.json()
        except httpx.HTTPStatusError as error:
            raise GenerationUnavailableError(
                f"ollama returned {error.response.status_code} for model "
                f"{self._model!r}"
            ) from error
        except httpx.HTTPError as error:
            raise GenerationUnavailableError(
                f"could not reach ollama at {self._base_url}: {error}"
            ) from error
        except ValueError as error:
            raise GenerationUnavailableError(
                "ollama returned a response envelope that was not JSON"
            ) from error
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if not isinstance(envelope, dict):
            raise GenerationShapeError(
                f"ollama returned {type(envelope).__name__}, not an object"
            )

        prompt_tokens = _count(envelope.get("prompt_eval_count"))
        self._refuse_truncated(prompt_tokens)

        return GenerationResult(
            payload=self._decoded(envelope.get("response")),
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=_count(envelope.get("eval_count")),
            elapsed_ms=elapsed_ms,
            context_window=self._context_window,
        )

    def _refuse_truncated(self, prompt_tokens: int) -> None:
        """Refuse a response whose prompt did not fit the window.

        Checked before the payload is decoded, so a truncated prompt never produces a
        parsed answer that a caller might use before noticing.
        """
        if prompt_tokens and prompt_tokens >= self._context_window - _TRUNCATION_MARGIN:
            raise GenerationTruncatedError(
                f"the prompt evaluated to {prompt_tokens:,} tokens against a context "
                f"window of {self._context_window:,}; ollama discards the remainder "
                "silently, so the model did not see all of the evidence and any answer "
                "would cite passages it never read"
            )

    def _decoded(self, raw: object) -> dict[str, object]:
        """Parse the constrained completion, refusing anything that is not an object.

        The schema is enforced by the runtime, so a failure here means the runtime ignored
        it — a different Ollama version, a model that does not support constrained
        decoding, or a schema it rejected silently. That is a configuration fault and
        retrying reproduces it, so it raises rather than degrading.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise GenerationShapeError(
                "ollama returned no completion text; the schema may have been rejected"
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise GenerationShapeError(
                f"the completion was not valid JSON despite a constraining schema: "
                f"{error}"
            ) from error
        if not isinstance(parsed, dict):
            raise GenerationShapeError(
                f"the completion decoded to {type(parsed).__name__}, not an object"
            )
        return parsed

    def close(self) -> None:
        self._client.close()


def _count(value: object) -> int:
    """Read a token count, treating an absent or odd value as unknown rather than zero.

    Ollama omits these fields in some responses. Zero is returned for "unknown", and the
    truncation check reads zero as "cannot tell" rather than "nothing was sent" — a check
    that fired on a missing field would refuse every answer.
    """
    return value if isinstance(value, int) and value >= 0 else 0


def build_generator(settings: Settings | None = None) -> OllamaGenerator:
    """Wire the generator from configuration."""
    resolved = settings or get_settings()
    return OllamaGenerator(
        base_url=resolved.ollama_base_url,
        model=resolved.generation_model,
        context_window=resolved.generation_context_window,
        temperature=resolved.generation_temperature,
        timeout_seconds=resolved.generation_timeout_seconds,
    )
