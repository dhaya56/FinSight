"""Vectors kept so identical text is never embedded twice (§29.2).

**The saving is across runs, not within one.** Re-chunking gives every chunk a new
identifier while most chunk *text* survives unchanged, so a re-index recomputes
thousands of vectors it already had. Measured on this corpus: normalising glyph
artefacts alters 770 of 4,867 embedded passages, so 84.2% of a 40-minute re-index is
work that has not changed. Only 127 chunks share text *within* one run, which is why
deduplicating inside a single run would have been nearly worthless — the cache has to
outlive the run, which means it has to live here rather than in memory.

**A hit must be indistinguishable from a miss, or this is a correctness bug wearing
a performance costume.** Four properties make that true:

* **The key is everything that determines the vector** — the exact input text, the
  model, the embedding configuration version, and whether it was embedded as a
  document or as a query. The model is asymmetric (it applies ``search_document:``
  and ``search_query:`` prefixes), so the same string has two correct vectors and
  returning one for the other would degrade retrieval silently.
* **The stored text is compared on read, not just its digest.** A SHA-256 collision
  is not a practical worry, but the cost of being wrong is a financial passage
  ranked as though it said something else, with a well-formed vector and plausible
  distances, detectable by nothing downstream. The digest is the lookup; the text is
  the proof.
* **Vectors are stored at full precision.** ``double precision``, not ``real``: the
  port returns Python floats and the vector store receives them unchanged, so a
  float32 round trip would make a cached vector differ from a fresh one in the last
  bits and could reorder two near-identical candidates. 6 KB per row against 4,867
  rows is roughly 30 MB, which is not worth one bit of drift.
* **Nothing is overwritten.** A row is written once. If the same key is presented
  with a different vector, the model stopped being deterministic, and the insert is
  ignored rather than replacing a vector that other points were already ranked
  against.
"""

import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    Double,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from finsight.persistence.tables.base import Base

KIND_DOCUMENT = "document"
KIND_QUERY = "query"
KINDS = (KIND_DOCUMENT, KIND_QUERY)


class EmbeddingCacheEntry(Base):
    """One vector, and every fact that determines it."""

    __tablename__ = "embedding_cache"
    __table_args__ = (
        # Left unnamed so NAMING_CONVENTION derives uq_embedding_cache_input_digest.
        # An explicitly named unique constraint keeps its own name verbatim — the
        # convention for "uq" interpolates the first column, not the given name —
        # and the insert below has to spell that name to use ON CONFLICT.
        UniqueConstraint("input_digest", "model", "config_version", "kind"),
        # Check names do carry through the convention, which adds the prefix.
        CheckConstraint(
            f"kind IN ('{KIND_DOCUMENT}', '{KIND_QUERY}')", name="kind_known"
        ),
        CheckConstraint("cardinality(vector) > 0", name="vector_not_empty"),
        CheckConstraint(
            "cardinality(vector) = dimensions", name="dimensions_match_vector"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    input_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """SHA-256 of the exact input text, as UTF-8 bytes.

    Indexed through the unique constraint rather than the text itself, because a
    btree key is bounded near 2,700 bytes and a child chunk routinely exceeds that.
    """

    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    """The exact text embedded, kept so a hit can be proved rather than assumed."""

    model: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    """§14.10's embedding configuration version. Bumping it invalidates every row."""

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    """``document`` or ``query``; see the module docstring on asymmetry."""

    vector: Mapped[list[float]] = mapped_column(ARRAY(Double), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    """Stored as well as checked, so a model that changed width is a constraint
    violation here rather than a dimension mismatch at the vector store."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    """Touched on every hit, so the table can be pruned by disuse.

    Document entries are bounded by the corpus, but query entries grow with every
    distinct question ever asked, at about 6 KB each. Indexed because pruning reads
    by this column and nothing else does.
    """
