"""What a document *is*, as opposed to what bytes it holds.

PROJECT_BLUEPRINT.md §20.2 requires every retrieval — lexical and dense alike — to
be constrained by issuer, period, basis and document scope. None of that was
recorded anywhere in the authoritative store: ``documents`` holds an identifier and
a timestamp, and ``document_versions`` holds hashes, content types and an object
key. The values existed only in ``data/corpus/manifest.toml``, which is
development-corpus *governance* and not application state — so filtering from there
would have worked for the three corpus filings and failed silently for anything
uploaded.

§44 puts it plainly: "A value is unusable without knowing the issuer, fiscal
period, reporting basis, currency, presentation scale, metric concept, and
provenance." This table is the first five of those.

**A one-to-one extension, not columns on ``document_versions``.** ``documents.py``
calls its tables deliberately thin and says issuer, type and period are discovered
later; the same reasoning that put table captions in ``source_tables`` rather than
on every element applies here. It also lets *absence of a row* mean "never
established", which is a different fact from a row full of NULLs.

**Keyed to the version, not the document.** A reissued filing is new bytes and a
new version, and its period or basis may differ from the one before it. Keying to
the document would make the later one overwrite the earlier, and §27.7 requires
citations against the earlier to keep resolving.
"""

import datetime
import uuid
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finsight.domain.documents import DocumentType, ReportingBasis
from finsight.persistence.tables.base import Base

SOURCE_CORPUS_MANIFEST: Final = "corpus_manifest"
"""Declared by corpus governance, not read from the document."""

METADATA_SOURCES: Final[tuple[str, ...]] = (SOURCE_CORPUS_MANIFEST,)
"""Where metadata came from.

One value, because one route exists. Metadata *extraction* — reading the issuer and
period out of the filing itself — does not exist, and listing it here before it
does would let a NULL-free row claim a provenance nothing can produce. Widened by
migration when that capability arrives, which is the same convention
``DOCUMENT_VERSION_STATES`` follows.
"""


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_TYPE_LIST: Final = _quoted(tuple(kind.value for kind in DocumentType))
_BASIS_LIST: Final = _quoted(tuple(basis.value for basis in ReportingBasis))
_SOURCE_LIST: Final = _quoted(METADATA_SOURCES)


class DocumentMetadata(Base):
    """Issuer, type, period, basis and currency for one document version."""

    __tablename__ = "document_metadata"
    __table_args__ = (
        CheckConstraint(f"document_type IN ({_TYPE_LIST})", name="document_type_known"),
        CheckConstraint(
            f"reporting_basis IN ({_BASIS_LIST})", name="reporting_basis_known"
        ),
        CheckConstraint(f"source IN ({_SOURCE_LIST})", name="source_known"),
        CheckConstraint("length(issuer_name) > 0", name="issuer_name_not_blank"),
    )
    """The vocabularies are built from the domain enums, never retyped.

    ``DocumentType`` is the same enum ``corpus/manifest.py`` validates against, so
    a manifest entry the database would reject cannot be written. Spelling the list
    out here instead would drift the moment a filing class is added — the manifest
    would accept an RHP and the insert would fail at ingest.
    """

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id"), primary_key=True
    )

    issuer_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    """The issuer as the source states it, verbatim.

    Indexed because §20.2 makes issuer a hard filter on every retrieval, so it is
    read on each query rather than occasionally.

    Not normalised to a canonical issuer here. §19.5 makes issuer aliases a
    versioned, deterministically resolved seed table, and folding "Infosys
    Limited" into "Infosys" at write time would destroy the distinction that
    resolution needs to make.
    """

    issuer_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    """A registry identifier — CIN in India, CIK in the US. NULL when unknown."""

    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(8), nullable=True)

    fiscal_period: Mapped[str] = mapped_column(Text, nullable=False)
    """The period as stated: ``FY2024-25``, ``nine months ended 31 December 2024``.

    Text, not a parsed range, and deliberately so. The corpus already contains a
    nine-month stub period alongside ordinary fiscal years, and §16.8 treats
    non-coterminous periods as a real case rather than an anomaly. Parsing at write
    time would force a shape onto a value whose variation is the point;
    ``period_end`` carries the part that is reliably comparable.
    """

    period_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    """The period's closing date, which is what makes periods orderable."""

    reporting_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    """Consolidated, standalone, both, or undetermined (§16.9).

    A hard context dimension under §20.2, not a conflict to resolve: consolidated
    and standalone revenue for one issuer and period are both correct and are
    different numbers.
    """

    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    units_as_presented: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The scale the document presents figures in, as governance recorded it.

    Free text because one corpus entry reads "INR crore, lakh and million mixed
    within one document" — a description of a hazard, not a unit. Treating this as
    a normalisable scale would apply one factor to a document that has three.
    """

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    """Where these values came from.

    Load-bearing rather than bookkeeping. Everything here is currently *asserted by
    corpus governance*, not read from the filing, and a consumer that filters on
    issuer deserves to know which. When extraction begins supplying metadata the
    two must stay distinguishable, because one is evidence and the other is a
    curator's claim.
    """
