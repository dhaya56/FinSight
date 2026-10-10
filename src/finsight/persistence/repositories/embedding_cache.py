"""Reading and writing cached vectors, in transactions that span no model call.

§29.7 forbids a transaction that spans a model call, and a cache sits exactly where
that rule is easiest to break: the natural shape is "look, embed, store", and the
natural implementation holds one transaction open across all three. So this
repository exposes the look and the store as two separate units of work, and the
embedding happens between them with nothing open.

**Lookups are one round trip, not one per text.** A batch of 32 chunks is a single
``WHERE input_digest = ANY(...)``, because 32 round trips to save 32 model calls
would give back a measurable share of what the cache is for.
"""

import datetime
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from finsight.persistence.tables.embedding_cache import (
    KINDS,
    EmbeddingCacheEntry,
)

Vector = tuple[float, ...]


def digest_of(text: str) -> bytes:
    """The lookup key for one input text.

    SHA-256 over UTF-8, which is a key and not a security boundary — the text is
    stored beside it and compared on read, so the digest only has to be fast and
    well-distributed.
    """
    return hashlib.sha256(text.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Everything that determines a vector, and therefore identifies one."""

    text: str
    model: str
    config_version: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(
                f"kind must be one of {KINDS}, not {self.kind!r}; a document and a "
                "query embed differently and their vectors are not interchangeable"
            )


@dataclass(frozen=True, slots=True)
class CacheStatistics:
    """What the table holds, for the operator deciding whether to prune it."""

    entries: int
    document_entries: int
    query_entries: int
    oldest_use: datetime.datetime | None
    newest_use: datetime.datetime | None
    models: tuple[str, ...]


class EmbeddingCacheRepository:
    """Cached vectors, keyed by everything that produced them."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch(
        self, keys: Sequence[CacheKey]
    ) -> tuple[dict[CacheKey, Vector], tuple[int, ...]]:
        """Return the vectors held for ``keys``, and the row ids that were hit.

        **A digest match is not a hit.** The stored text must equal the requested
        text exactly, or the row is ignored and the text is embedded again. That
        turns the one failure mode that could silently corrupt an index — a digest
        collision returning another passage's vector — into a cache miss, which
        costs one model call.

        The row ids come back so the caller can touch ``last_used_at`` in its write
        transaction rather than in this read one.
        """
        if not keys:
            return {}, ()

        wanted = {
            (digest_of(key.text), key.model, key.config_version, key.kind): key
            for key in keys
        }
        rows = self._session.execute(
            select(
                EmbeddingCacheEntry.id,
                EmbeddingCacheEntry.input_digest,
                EmbeddingCacheEntry.input_text,
                EmbeddingCacheEntry.model,
                EmbeddingCacheEntry.config_version,
                EmbeddingCacheEntry.kind,
                EmbeddingCacheEntry.vector,
            ).where(
                EmbeddingCacheEntry.input_digest.in_(
                    {digest for digest, _, _, _ in wanted}
                ),
                EmbeddingCacheEntry.model.in_({key.model for key in keys}),
                EmbeddingCacheEntry.config_version.in_(
                    {key.config_version for key in keys}
                ),
                EmbeddingCacheEntry.kind.in_({key.kind for key in keys}),
            )
        ).all()

        found: dict[CacheKey, Vector] = {}
        hit_ids: list[int] = []
        for row in rows:
            key = wanted.get(
                (row.input_digest, row.model, row.config_version, row.kind)
            )
            if key is None or row.input_text != key.text:
                continue
            found[key] = tuple(row.vector)
            hit_ids.append(row.id)
        return found, tuple(hit_ids)

    def store(
        self, vectors: Mapping[CacheKey, Vector], *, dimensions: int
    ) -> int:
        """Insert vectors that are not already held, and return how many landed.

        ``ON CONFLICT DO NOTHING`` rather than an upsert. Two indexers embedding the
        same text concurrently is ordinary, and both computed the same vector; a row
        that already exists is the right answer and replacing it would rewrite a
        vector that live points were ranked against. A conflict here is not an
        error, and the returned count says how many rows were genuinely new.
        """
        if not vectors:
            return 0

        statement = (
            insert(EmbeddingCacheEntry)
            .values(
                [
                    {
                        "input_digest": digest_of(key.text),
                        "input_text": key.text,
                        "model": key.model,
                        "config_version": key.config_version,
                        "kind": key.kind,
                        "vector": list(vector),
                        "dimensions": dimensions,
                    }
                    for key, vector in vectors.items()
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_embedding_cache_input_digest",
            )
            .returning(EmbeddingCacheEntry.id)
        )
        return len(self._session.execute(statement).all())

    def touch(self, entry_ids: Sequence[int]) -> None:
        """Record that these entries were used, for pruning by disuse.

        One statement for the whole batch. Separate from :meth:`fetch` so the read
        stays a read: a lookup that wrote would take row locks that a concurrent
        indexer embedding the same corpus would wait on.
        """
        if not entry_ids:
            return
        self._session.execute(
            update(EmbeddingCacheEntry)
            .where(EmbeddingCacheEntry.id.in_(set(entry_ids)))
            .values(last_used_at=func.now())
        )

    def statistics(self) -> CacheStatistics:
        """Summarise the table for the ``cache`` command."""
        totals = self._session.execute(
            select(
                func.count(EmbeddingCacheEntry.id),
                func.min(EmbeddingCacheEntry.last_used_at),
                func.max(EmbeddingCacheEntry.last_used_at),
            )
        ).one()
        by_kind: dict[str, int] = {
            row.kind: row.tally
            for row in self._session.execute(
                select(
                    EmbeddingCacheEntry.kind,
                    func.count(EmbeddingCacheEntry.id).label("tally"),
                ).group_by(EmbeddingCacheEntry.kind)
            ).all()
        }
        models = self._session.execute(
            select(EmbeddingCacheEntry.model)
            .distinct()
            .order_by(EmbeddingCacheEntry.model)
        ).scalars()
        return CacheStatistics(
            entries=totals[0],
            document_entries=by_kind.get("document", 0),
            query_entries=by_kind.get("query", 0),
            oldest_use=totals[1],
            newest_use=totals[2],
            models=tuple(models),
        )

    def count_unused_since(self, cutoff: datetime.datetime) -> int:
        """How many entries have not been used since ``cutoff``."""
        return int(
            self._session.execute(
                select(func.count(EmbeddingCacheEntry.id)).where(
                    EmbeddingCacheEntry.last_used_at < cutoff
                )
            ).scalar_one()
        )

    def prune_unused_since(self, cutoff: datetime.datetime) -> int:
        """Delete entries unused since ``cutoff`` and return how many went.

        Deleting a cached vector cannot lose evidence: the text it was computed from
        lives in ``chunks`` or was a question, and the model will compute the same
        vector again. The only cost of pruning too eagerly is time.
        """
        # ``returning`` rather than ``rowcount``: the row count is typed as absent on
        # a generic Result, and counting what came back is exact rather than a cast.
        return len(
            self._session.execute(
                delete(EmbeddingCacheEntry)
                .where(EmbeddingCacheEntry.last_used_at < cutoff)
                .returning(EmbeddingCacheEntry.id)
            ).all()
        )


__all__ = [
    "CacheKey",
    "CacheStatistics",
    "EmbeddingCacheRepository",
    "digest_of",
]
