"""Tests for the embedding boundary.

No Ollama here. The adapter is exercised against a stub transport so that every
failure the real service can produce — a timeout, a 500, a truncated batch, a
model that silently changed width — can be provoked deliberately rather than
waited for.

The ones that matter are in :class:`TestShapeVerification`. A wrong-sized or
miscounted response is the failure that does *not* announce itself: vectors get
paired with chunks by position, so a batch returning three vectors for four inputs
attaches every one of them to the wrong chunk, and every search afterwards is
subtly wrong with nothing in the logs.
"""

import httpx
import pytest

from finsight.embedding.fake import FakeEmbedder
from finsight.embedding.ollama_embedder import (
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    OllamaEmbedder,
)
from finsight.embedding.port import (
    Embedder,
    EmbeddingShapeError,
    EmbeddingUnavailableError,
)

DIMENSIONS = 4


def transport(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def responding(vectors: list[list[float]], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"embeddings": vectors})

    return transport(handler)


def embedder(client: httpx.Client, *, dimensions: int = DIMENSIONS) -> OllamaEmbedder:
    return OllamaEmbedder(
        base_url="http://127.0.0.1:11434",
        model="nomic-embed-text",
        dimensions=dimensions,
        timeout_seconds=5.0,
        batch_size=2,
        client=client,
    )


UNIT = [1.0, 0.0, 0.0, 0.0]


class TestTaskPrefixes:
    """Nomic needs them and Ollama does not apply them (measured: cosine 0.9388)."""

    def test_documents_are_prefixed_for_indexing(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.extend(json.loads(request.content)["input"])
            return httpx.Response(200, json={"embeddings": [UNIT]})

        embedder(transport(handler)).embed_documents(["Revenue grew."])

        assert seen == [f"{DOCUMENT_PREFIX}Revenue grew."]

    def test_queries_are_prefixed_for_searching(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.extend(json.loads(request.content)["input"])
            return httpx.Response(200, json={"embeddings": [UNIT]})

        embedder(transport(handler)).embed_query("how did revenue change")

        assert seen == [f"{QUERY_PREFIX}how did revenue change"]

    def test_the_two_prefixes_differ(self) -> None:
        """Indexing with one and searching with the other degrades silently."""
        assert DOCUMENT_PREFIX != QUERY_PREFIX


class TestShapeVerification:
    def test_a_short_batch_is_refused(self) -> None:
        """Three vectors for four inputs misattributes every one of them."""
        client = responding([UNIT, UNIT, UNIT])

        with pytest.raises(EmbeddingShapeError, match="asked for 4"):
            embedder(client, dimensions=4)._embed(["a", "b", "c", "d"])

    def test_a_wrong_width_is_refused(self) -> None:
        """A model swapped behind the same tag changes the collection's shape."""
        client = responding([[1.0, 0.0]])

        with pytest.raises(EmbeddingShapeError, match="expected 4 dimensions"):
            embedder(client).embed_query("anything")

    def test_a_response_without_embeddings_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "model not found"})

        with pytest.raises(EmbeddingShapeError, match="no 'embeddings' list"):
            embedder(transport(handler)).embed_query("anything")

    def test_a_non_numeric_vector_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": ["not a vector"]})

        with pytest.raises(EmbeddingShapeError):
            embedder(transport(handler)).embed_query("anything")


class TestUnavailability:
    def test_a_connection_failure_is_translated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(EmbeddingUnavailableError, match="could not reach"):
            embedder(transport(handler)).embed_query("anything")

    def test_a_server_error_is_translated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        with pytest.raises(EmbeddingUnavailableError, match="500"):
            embedder(transport(handler)).embed_query("anything")

    def test_a_non_json_response_is_translated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>proxy error</html>")

        with pytest.raises(EmbeddingUnavailableError, match="not JSON"):
            embedder(transport(handler)).embed_query("anything")

    def test_unavailability_is_distinct_from_a_shape_error(self) -> None:
        """One is retryable and leaves the event pending; the other never will be."""
        assert not issubclass(EmbeddingShapeError, EmbeddingUnavailableError)


class TestBatching:
    def test_a_long_list_is_sent_in_batches(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            size = len(json.loads(request.content)["input"])
            calls.append(size)
            return httpx.Response(200, json={"embeddings": [UNIT] * size})

        embedder(transport(handler)).embed_documents(["a", "b", "c", "d", "e"])

        assert calls == [2, 2, 1]

    def test_vectors_come_back_in_the_order_given(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json

            inputs = json.loads(request.content)["input"]
            return httpx.Response(
                200,
                json={
                    "embeddings": [
                        [float(len(text)), 0.0, 0.0, 0.0] for text in inputs
                    ]
                },
            )

        vectors = embedder(transport(handler)).embed_documents(["a", "bb", "ccc"])

        assert [v[0] for v in vectors] == [1.0, 1.0, 1.0]  # each normalised to unit

    def test_embedding_nothing_calls_nothing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        assert embedder(transport(handler)).embed_documents([]) == ()


class TestNormalisation:
    def test_a_long_vector_is_scaled_to_unit_length(self) -> None:
        client = responding([[3.0, 4.0, 0.0, 0.0]])

        vector = embedder(client).embed_query("anything")

        assert vector == pytest.approx((0.6, 0.8, 0.0, 0.0))

    def test_an_already_unit_vector_is_untouched(self) -> None:
        client = responding([UNIT])

        assert embedder(client).embed_query("anything") == tuple(UNIT)

    def test_a_zero_vector_is_returned_rather_than_divided(self) -> None:
        """A retrieval miss, not a corruption — and dividing would raise."""
        client = responding([[0.0, 0.0, 0.0, 0.0]])

        assert embedder(client).embed_query("anything") == (0.0, 0.0, 0.0, 0.0)


class TestFakeEmbedder:
    def test_it_satisfies_the_port(self) -> None:
        assert isinstance(FakeEmbedder(), Embedder)

    def test_the_same_text_always_embeds_identically(self) -> None:
        """§22.1 requires reproducible vectors; so does a reproducible test."""
        first = FakeEmbedder().embed_documents(["Revenue grew."])
        second = FakeEmbedder().embed_documents(["Revenue grew."])

        assert first == second

    def test_different_texts_embed_differently(self) -> None:
        vectors = FakeEmbedder().embed_documents(["alpha", "beta"])

        assert vectors[0] != vectors[1]

    def test_documents_and_queries_differ_for_the_same_text(self) -> None:
        """Mirrors the real asymmetry, so a prefix mix-up fails here too."""
        fake = FakeEmbedder()

        assert fake.embed_documents(["text"])[0] != fake.embed_query("text")

    def test_vectors_are_unit_length(self) -> None:
        vector = FakeEmbedder(dimensions=16).embed_query("anything")

        assert sum(value * value for value in vector) == pytest.approx(1.0)

    def test_the_width_is_configurable(self) -> None:
        assert len(FakeEmbedder(dimensions=32).embed_query("x")) == 32

    def test_it_carries_no_semantics(self) -> None:
        """Deliberate. A fake that looked semantic would invite a quality claim.

        Two near-identical sentences must *not* come back similar, so no test can
        accidentally assert that retrieval works.
        """
        fake = FakeEmbedder(dimensions=256)
        one = fake.embed_documents(["Revenue grew by six percent."])[0]
        two = fake.embed_documents(["Revenue grew by seven percent."])[0]

        similarity = sum(a * b for a, b in zip(one, two, strict=True))
        assert abs(similarity) < 0.3
