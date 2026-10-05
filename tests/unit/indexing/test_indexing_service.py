"""The indexer's ordering guarantees, asserted with fakes rather than services.

Three properties carry the weight, and none of them is about embedding quality:

* **no transaction spans a model or vector call** (§29.7),
* **activation happens last, and only on a complete, reconciled run** (§11.13),
* **a failure leaves the previously active generation alone.**

The recorder is a fake that records the order it was called in, so the ordering is
asserted directly instead of being inferred from the absence of a deadlock.
"""

import datetime
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from finsight.embedding.port import EmbeddingUnavailableError, Vector
from finsight.indexing.service import (
    GenerationNotIndexableError,
    IndexingError,
    IndexingService,
    IndexReconciliationError,
    NothingToIndexError,
)
from finsight.persistence.repositories.chunks import PendingChunk
from finsight.vector_index.port import IndexedChunk, VectorIndexUnavailableError

GENERATION = uuid4()
VERSION = uuid4()


def chunk(
    *,
    text: str = "Revenue from operations rose during the year.",
    lexemes: str | None = "'revenu':1 'oper':2 'rose':3 'year':6",
    **overrides: object,
) -> PendingChunk:
    defaults: dict[str, object] = {
        "chunk_id": uuid4(),
        "document_version_id": VERSION,
        "text": text,
        "lexemes": lexemes,
        "heading_path": ("Management Discussion",),
        "page_numbers": (42,),
        "evidence_type": "narrative",
        "issuer_name": "Infosys Limited",
        "document_type": "annual_report",
        "fiscal_period": "FY2024-25",
        "reporting_basis": "both",
        "currency": "INR",
        "period_end": datetime.date(2025, 3, 31),
    }
    defaults.update(overrides)
    return PendingChunk(**defaults)  # type: ignore[arg-type]


class FakeRecorder:
    """Records call order. Opens nothing, so a transaction cannot span anything."""

    def __init__(
        self,
        *,
        batches: Sequence[Sequence[PendingChunk]] = (),
        state: str = "shadow",
        total: int | None = None,
    ) -> None:
        self._batches = [list(batch) for batch in batches]
        self._state = state
        self._total = (
            total
            if total is not None
            else sum(len(batch) for batch in batches)
        )
        self.calls: list[str] = []
        self.completed: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []
        self.activated: list[UUID] = []
        self.generation_failed: list[UUID] = []
        self.reopened: list[UUID] = []

    def prepare(self, generation_id: UUID) -> tuple[UUID, int, str]:
        self.calls.append("prepare")
        if self._state == "missing":
            raise NothingToIndexError(f"no generation {generation_id}")
        return VERSION, self._total, self._state

    def pending(self, generation_id: UUID, limit: int) -> list[PendingChunk]:
        self.calls.append("pending")
        return self._batches.pop(0) if self._batches else []

    def complete(self, chunk_ids: Sequence[UUID]) -> None:
        self.calls.append("complete")
        self.completed.extend(chunk_ids)

    def record_failure(self, chunk_ids: Sequence[UUID], reason: str) -> None:
        self.calls.append("record_failure")
        self.failed.extend((chunk_id, reason) for chunk_id in chunk_ids)

    def indexed_count(self, generation_id: UUID) -> int:
        self.calls.append("indexed_count")
        return len(self.completed)

    def activate(self, generation_id: UUID) -> None:
        self.calls.append("activate")
        self.activated.append(generation_id)

    def fail(self, generation_id: UUID) -> None:
        self.calls.append("fail")
        self.generation_failed.append(generation_id)

    def reopen(self, generation_id: UUID) -> None:
        self.calls.append("reopen")
        self.reopened.append(generation_id)
        if self._state == "failed":
            self._state = "shadow"


class FakeEmbedder:
    """Deterministic vectors, so a test asserts pairing rather than similarity."""

    model = "fake-embed"
    dimensions = 4

    def __init__(self, *, fails: bool = False) -> None:
        self._fails = fails
        self.seen: list[str] = []

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        if self._fails:
            raise EmbeddingUnavailableError("ollama is not running")
        self.seen.extend(texts)
        return tuple(
            (float(len(text)), 0.0, 0.0, 1.0) for text in texts
        )

    def embed_query(self, text: str) -> Vector:
        return (1.0, 0.0, 0.0, 0.0)


class FakeIndex:
    """Collects upserts. ``count`` answers from what it was given."""

    collection = "chunks__fake_embed__4"

    def __init__(self, *, fails: bool = False, count_override: int | None = None) -> None:
        self._fails = fails
        self._count_override = count_override
        self.upserted: list[IndexedChunk] = []
        self.ensured = 0

    def ensure_collection(self) -> None:
        self.ensured += 1

    def upsert(self, chunks: Sequence[IndexedChunk]) -> int:
        if self._fails:
            raise VectorIndexUnavailableError("qdrant is not running")
        self.upserted.extend(chunks)
        return len(chunks)

    def search_dense(self, vector, *, limit, filters=None):  # type: ignore[no-untyped-def]
        return ()

    def search_sparse(self, indices, values, *, limit, filters=None):  # type: ignore[no-untyped-def]
        return ()

    def count(self, *, filters=None) -> int:  # type: ignore[no-untyped-def]
        if self._count_override is not None:
            return self._count_override
        return len(self.upserted)


def build(
    recorder: FakeRecorder,
    *,
    embedder: FakeEmbedder | None = None,
    index: FakeIndex | None = None,
    batch_size: int = 2,
) -> tuple[IndexingService, FakeEmbedder, FakeIndex]:
    resolved_embedder = embedder or FakeEmbedder()
    resolved_index = index or FakeIndex()
    service = IndexingService(
        recorder=recorder,
        embedder=resolved_embedder,
        index=resolved_index,
        batch_size=batch_size,
    )
    return service, resolved_embedder, resolved_index


class TestOrdering:
    def test_completion_is_recorded_after_the_upsert_not_before(self) -> None:
        """Recording first would mark a chunk indexed that a crash never wrote."""
        recorder = FakeRecorder(batches=[[chunk(), chunk()]])
        service, _embedder, _index = build(recorder)

        service.index(GENERATION)

        assert recorder.calls == [
            "prepare",
            "pending",
            "complete",
            "pending",
            "indexed_count",
            "activate",
        ]

    def test_activation_is_the_last_call(self) -> None:
        """§11.13: a generation becomes queryable only once the work is done."""
        recorder = FakeRecorder(batches=[[chunk()], [chunk()]])
        service, _embedder, _index = build(recorder, batch_size=1)

        service.index(GENERATION)

        assert recorder.calls[-1] == "activate"
        assert recorder.calls.index("activate") > recorder.calls.index("complete")

    def test_the_collection_is_prepared_before_any_chunk_is_read(self) -> None:
        """A shape mismatch must fail before a 4,816-chunk run starts embedding."""
        recorder = FakeRecorder(batches=[[chunk()]])
        index = FakeIndex()
        service, _embedder, _index = build(recorder, index=index)

        service.index(GENERATION)

        assert index.ensured == 1

    def test_batches_are_drained_until_none_remain(self) -> None:
        recorder = FakeRecorder(batches=[[chunk()], [chunk()], [chunk()]])
        service, _embedder, index = build(recorder, batch_size=1)

        result = service.index(GENERATION)

        assert result.indexed == 3
        assert len(index.upserted) == 3


class TestWhatGetsIndexed:
    def test_the_embedded_text_is_the_enriched_form(self) -> None:
        """§14.2 and §14.6: context reaches the vector, not just the body."""
        recorder = FakeRecorder(batches=[[chunk(text="Revenue rose.")]])
        service, embedder, _index = build(recorder)

        service.index(GENERATION)

        assert embedder.seen[0].endswith("Revenue rose.")
        assert "Issuer: Infosys Limited" in embedder.seen[0]
        assert "Section: Management Discussion" in embedder.seen[0]

    def test_the_stored_text_is_not_what_was_embedded(self) -> None:
        """The enriched string is never persisted, so it cannot be cited (§14.4)."""
        pending = chunk(text="Revenue rose.")
        recorder = FakeRecorder(batches=[[pending]])
        service, embedder, _index = build(recorder)

        service.index(GENERATION)

        assert embedder.seen[0] != pending.text

    def test_the_payload_carries_the_filter_fields(self) -> None:
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        payload = index.upserted[0].payload
        assert payload["generation_id"] == str(GENERATION)
        assert payload["document_version_id"] == str(VERSION)
        assert payload["issuer_name"] == "Infosys Limited"
        assert payload["evidence_type"] == "narrative"

    def test_the_payload_carries_an_orderable_year(self) -> None:
        """``fiscal_period`` is the document's own words and two filings share one.

        A year derived from ``period_end`` is what lets a filter express a range.
        """
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert index.upserted[0].payload["fiscal_year"] == 2025

    def test_the_payload_carries_the_section_and_pages(self) -> None:
        """Both are recorded on the chunk and were filterable on neither."""
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        payload = index.upserted[0].payload
        assert payload["section"] == "Management Discussion"
        assert payload["page_numbers"] == [42]

    def test_the_section_is_the_outermost_heading(self) -> None:
        """So a filter excludes a whole section, not a leaf of one."""
        recorder = FakeRecorder(
            batches=[[chunk(heading_path=("SECTION II: RISK FACTORS", "Internal"))]]
        )
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert index.upserted[0].payload["section"] == "SECTION II: RISK FACTORS"

    def test_an_unrecorded_period_leaves_no_year(self) -> None:
        """Absent cannot match a range, which is right for an unknown period."""
        recorder = FakeRecorder(batches=[[chunk(period_end=None)]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert "fiscal_year" not in index.upserted[0].payload

    def test_a_chunk_with_no_heading_carries_no_section(self) -> None:
        recorder = FakeRecorder(batches=[[chunk(heading_path=())]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert "section" not in index.upserted[0].payload

    def test_an_unknown_field_is_absent_from_the_payload(self) -> None:
        """Absent cannot match a filter, which is right for an unknown issuer.

        A placeholder would make every metadata-less document match each other.
        """
        recorder = FakeRecorder(batches=[[chunk(issuer_name=None)]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert "issuer_name" not in index.upserted[0].payload

    def test_sparse_weights_are_built_from_the_stored_lexemes(self) -> None:
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        point = index.upserted[0]
        assert len(point.sparse_indices) == 4
        assert all(value > 0 for value in point.sparse_values)

    def test_a_term_free_chunk_is_indexed_with_no_sparse_vector(self) -> None:
        """Legitimate: every token is a stopword. Dense search still reaches it."""
        recorder = FakeRecorder(batches=[[chunk(lexemes="")]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        assert index.upserted[0].sparse_indices == ()
        assert index.upserted[0].dense != ()

    def test_an_unanalysed_chunk_is_refused(self) -> None:
        """``None`` is a pipeline fault, not a term-free chunk.

        Indexing it would put a point in the collection that no lexical query can
        reach, and nothing downstream would report the chunk as missing.
        """
        recorder = FakeRecorder(batches=[[chunk(lexemes=None)]])
        service, _embedder, _index = build(recorder)

        with pytest.raises(IndexingError, match="no lexical vector"):
            service.index(GENERATION)

    def test_vectors_pair_with_their_chunks_by_position(self) -> None:
        """A reordering here attaches every vector to the wrong chunk."""
        first = chunk(text="a")
        second = chunk(text="bbbbbbbbbb")
        recorder = FakeRecorder(batches=[[first, second]])
        service, _embedder, index = build(recorder)

        service.index(GENERATION)

        by_id = {point.chunk_id: point for point in index.upserted}
        assert by_id[first.chunk_id].dense[0] < by_id[second.chunk_id].dense[0]


class TestRefusals:
    def test_a_missing_generation_is_refused(self) -> None:
        recorder = FakeRecorder(state="missing")
        service, _embedder, _index = build(recorder)

        with pytest.raises(NothingToIndexError):
            service.index(GENERATION)

    def test_a_generation_with_no_queued_chunks_is_refused(self) -> None:
        """Distinct from "already indexed": nothing was ever queued."""
        recorder = FakeRecorder(batches=[], total=0)
        service, _embedder, _index = build(recorder)

        with pytest.raises(NothingToIndexError, match="no chunks queued"):
            service.index(GENERATION)

    def test_an_active_generation_is_refused(self) -> None:
        """Re-indexing in place would mutate what a reader is being served."""
        recorder = FakeRecorder(batches=[[chunk()]], state="active", total=1)
        service, _embedder, _index = build(recorder)

        with pytest.raises(GenerationNotIndexableError, match="shadow"):
            service.index(GENERATION)

    def test_nothing_is_embedded_when_the_generation_is_refused(self) -> None:
        recorder = FakeRecorder(batches=[[chunk()]], state="active", total=1)
        service, embedder, index = build(recorder)

        with pytest.raises(GenerationNotIndexableError):
            service.index(GENERATION)

        assert embedder.seen == []
        assert index.ensured == 0


class TestFailure:
    def test_an_outage_leaves_the_events_pending(self) -> None:
        """The embedding port's contract: an unavailable model is retryable.

        Marking them failed would turn "restart Ollama and run it again" into an
        operator task with a flag, for a condition that resolves itself.
        """
        recorder = FakeRecorder(batches=[[chunk(), chunk()]])
        service, _embedder, _index = build(
            recorder, embedder=FakeEmbedder(fails=True)
        )

        with pytest.raises(EmbeddingUnavailableError):
            service.index(GENERATION)

        assert recorder.failed == []
        assert "record_failure" not in recorder.calls

    def test_an_outage_leaves_the_generation_shadow(self) -> None:
        """Shadow already means "not queryable"; failing it blocks the retry."""
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, _index = build(
            recorder, embedder=FakeEmbedder(fails=True)
        )

        with pytest.raises(EmbeddingUnavailableError):
            service.index(GENERATION)

        assert recorder.generation_failed == []

    def test_a_permanent_fault_marks_its_events_failed(self) -> None:
        """It will fail identically on every retry; pending would retry forever."""
        recorder = FakeRecorder(batches=[[chunk(lexemes=None)]])
        service, _embedder, _index = build(recorder)

        with pytest.raises(IndexingError):
            service.index(GENERATION)

        assert len(recorder.failed) == 1
        assert recorder.failed[0][1] == "IndexingError"

    def test_an_index_failure_does_not_record_completion(self) -> None:
        """A completed event for an unwritten point is a lie reconciliation cannot see."""
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, _index = build(recorder, index=FakeIndex(fails=True))

        with pytest.raises(VectorIndexUnavailableError):
            service.index(GENERATION)

        assert recorder.completed == []
        assert "complete" not in recorder.calls

    def test_a_failure_never_activates(self) -> None:
        """§11.13: the previously active generation stays queryable."""
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, _index = build(recorder, index=FakeIndex(fails=True))

        with pytest.raises(VectorIndexUnavailableError):
            service.index(GENERATION)

        assert recorder.activated == []


class TestRetry:
    def test_a_failed_generation_is_refused_without_retry(self) -> None:
        recorder = FakeRecorder(batches=[[chunk()]], state="failed", total=1)
        service, _embedder, _index = build(recorder)

        with pytest.raises(GenerationNotIndexableError):
            service.index(GENERATION)

        assert recorder.reopened == []

    def test_retry_reopens_before_reading_the_state(self) -> None:
        """Reopening after ``prepare`` would read the state it was meant to fix."""
        recorder = FakeRecorder(batches=[[chunk()]], state="failed", total=1)
        service, _embedder, _index = build(recorder)

        result = service.index(GENERATION, retry=True)

        assert recorder.calls[0] == "reopen"
        assert recorder.calls[1] == "prepare"
        assert result.activated is True

    def test_retry_on_a_healthy_generation_is_harmless(self) -> None:
        """So the flag can be passed habitually without thinking about state."""
        recorder = FakeRecorder(batches=[[chunk()]])
        service, _embedder, _index = build(recorder)

        result = service.index(GENERATION, retry=True)

        assert result.activated is True


class TestReconciliation:
    def test_a_short_index_refuses_activation(self) -> None:
        """§29.11. Activating here would answer queries incompletely, silently."""
        recorder = FakeRecorder(batches=[[chunk(), chunk()]])
        service, _embedder, _index = build(
            recorder, index=FakeIndex(count_override=1)
        )

        with pytest.raises(IndexReconciliationError, match="disagree"):
            service.index(GENERATION)

        assert recorder.activated == []
        assert recorder.generation_failed == [GENERATION]

    def test_a_shortfall_in_recorded_completions_refuses_activation(self) -> None:
        """The outbox says fewer were indexed than were queued."""
        recorder = FakeRecorder(batches=[[chunk()]], total=5)
        service, _embedder, _index = build(recorder)

        with pytest.raises(IndexReconciliationError, match="refusing to activate"):
            service.index(GENERATION)

        assert recorder.activated == []

    def test_a_run_with_nothing_left_still_reconciles_and_activates(self) -> None:
        """A crash between the last batch and activation must be recoverable.

        Every event is already complete, so there is no work — but the generation
        is still shadow, and a run that returned without activating would leave it
        permanently unqueryable.
        """
        recorder = FakeRecorder(batches=[], total=2)
        recorder.completed = [uuid4(), uuid4()]
        service, _embedder, _index = build(
            recorder, index=FakeIndex(count_override=2)
        )

        result = service.index(GENERATION)

        assert result.already_complete is True
        assert result.indexed == 0
        assert result.activated is True
        assert recorder.activated == [GENERATION]
