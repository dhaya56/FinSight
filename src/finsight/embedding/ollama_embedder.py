"""The Ollama adapter: the only module in FinSight that embeds text over HTTP.

Ollama runs host-native (§9.8, §10.4) rather than in a container, so this is an
HTTP call to the host and not a service dependency Compose can start. That is a
real operational asymmetry and it shows up in CI, where Ollama does not exist and
tests use the deterministic fake instead.

Three things this adapter does that a thin HTTP wrapper would not:

**It applies task prefixes.** Nomic requires ``search_document:`` and
``search_query:``; measured against the running model, Ollama does not apply them
itself — the prefixed and plain forms of one sentence embed to cosine 0.9388 of
each other. Applying them here, rather than at the call site, is what guarantees
the index and the query always agree.

**It verifies the shape.** A model swapped behind the same tag, or a truncated
response, produces vectors the collection either rejects or silently accepts into
a differently-shaped space. Either way the failure must be loud.

**It normalises.** Nomic already returns unit vectors — measured at norm 1.0 — but
the port promises them, and a candidate that does not would otherwise make cosine
and dot product disagree at the vector store.
"""

import math
from collections.abc import Sequence
from typing import Any, Final

import httpx

from finsight.config.settings import Settings, get_settings
from finsight.embedding.port import (
    EmbeddingInputTooLongError,
    EmbeddingShapeError,
    EmbeddingUnavailableError,
    Vector,
)

DOCUMENT_PREFIX: Final = "search_document: "
QUERY_PREFIX: Final = "search_query: "
"""Nomic's task prefixes.

Not configurable, because they are a property of the model rather than of this
deployment, and a deployment that changed one without the other would degrade
retrieval in a way that looks like a bad model. A model needing different prefixes
needs a different adapter.
"""

_EMBED_PATH: Final = "/api/embed"


class OllamaEmbedder:
    """Embeds text through a host-native Ollama instance."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
        batch_size: int,
        max_input_chars: int,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._max_input_chars = max_input_chars
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        if not texts:
            return ()
        vectors: list[Vector] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(
                self._embed([f"{DOCUMENT_PREFIX}{text}" for text in batch])
            )
        return tuple(vectors)

    def embed_query(self, text: str) -> Vector:
        return self._embed([f"{QUERY_PREFIX}{text}"])[0]

    def _embed(self, inputs: Sequence[str]) -> list[Vector]:
        self._refuse_over_long(inputs)
        try:
            response = self._client.post(
                f"{self._base_url}{_EMBED_PATH}",
                json={"model": self._model, "input": list(inputs)},
            )
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.HTTPStatusError as error:
            raise EmbeddingUnavailableError(
                f"ollama returned {error.response.status_code} for model "
                f"{self._model!r}"
            ) from error
        except httpx.HTTPError as error:
            raise EmbeddingUnavailableError(
                f"could not reach ollama at {self._base_url}: {error}"
            ) from error
        except ValueError as error:
            raise EmbeddingUnavailableError(
                "ollama returned a response that was not JSON"
            ) from error

        return self._verified(payload, expected=len(inputs))

    def _refuse_over_long(self, inputs: Sequence[str]) -> None:
        """Reject input the model would silently truncate.

        **Measured, not assumed.** Appending a distinctive sentence to a
        2,048-token passage returned a bit-identical vector — Ollama anchors
        ``num_ctx`` to 2,048 for this model rather than its native 8,192, and
        discards the remainder without an error.

        The bound is in *characters* because this adapter deliberately has no
        tokenizer: acquiring one would mean carrying a second model's vocabulary
        to approximate a first model's. Characters are a conservative proxy —
        English financial prose measured at roughly 4.4 characters per token, so
        2,048 tokens is around 9,000 characters, and the default sits well below
        that. Dense numerals tokenize worse and would hit the wall sooner, which
        is the residual risk and why the bound is configurable.
        """
        for text in inputs:
            if len(text) > self._max_input_chars:
                raise EmbeddingInputTooLongError(
                    f"input of {len(text):,} characters exceeds the configured "
                    f"{self._max_input_chars:,}; the model would truncate it "
                    "silently and the discarded text would be unfindable"
                )

    def _verified(self, payload: Any, *, expected: int) -> list[Vector]:
        """Check the response before any of it reaches a vector store."""
        raw = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise EmbeddingShapeError(
                "ollama response contained no 'embeddings' list"
            )
        if len(raw) != expected:
            raise EmbeddingShapeError(
                f"asked for {expected} embedding(s) and received {len(raw)}; "
                "pairing vectors with chunks by position would misattribute them"
            )

        vectors: list[Vector] = []
        for vector in raw:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                length = len(vector) if isinstance(vector, list) else "none"
                raise EmbeddingShapeError(
                    f"expected {self._dimensions} dimensions from "
                    f"{self._model!r} and received {length}"
                )
            vectors.append(_unit(tuple(float(value) for value in vector)))
        return vectors

    def close(self) -> None:
        self._client.close()


def _unit(vector: Vector) -> Vector:
    """Scale to unit length, leaving an already-normalised vector unchanged.

    Nomic returns unit vectors already, so this is normally a no-op that costs one
    pass. It exists because the port promises unit vectors and the vector store's
    distance metric is chosen on that promise: with cosine the scale is irrelevant,
    but a later switch to dot product would silently rank by magnitude instead of
    direction.

    A zero vector is returned unchanged rather than divided. It cannot be
    normalised, and raising would reject a document for containing nothing a model
    recognised, which is a retrieval miss rather than a corruption.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0 or abs(norm - 1.0) < 1e-6:
        return vector
    return tuple(value / norm for value in vector)


def build_embedder(settings: Settings | None = None) -> OllamaEmbedder:
    """Wire the embedder from configuration."""
    resolved = settings if settings is not None else get_settings()
    return OllamaEmbedder(
        base_url=resolved.ollama_base_url,
        model=resolved.embedding_model,
        dimensions=resolved.embedding_dimensions,
        timeout_seconds=resolved.embedding_timeout_seconds,
        batch_size=resolved.embedding_batch_size,
        max_input_chars=resolved.embedding_max_input_chars,
    )
