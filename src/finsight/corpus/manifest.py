"""The corpus manifest: what was acquired, from where, and which split it belongs to.

PROJECT_BLUEPRINT.md §32.6 requires the manifest to record document identity,
source, issuer, type, period, format, publication and retrieval dates, checksums,
redistribution status, split, and rationale. This module parses that record and
enforces the rules around it.

**The manifest is read, never written.** Entries are authored by a person who
downloaded the document, and reviewed like any other committed file. The CLI
prints a paste-ready block rather than editing this file, so the manifest stays
an artefact someone approved rather than one a tool produced. That is also why
parsing is strict: an unknown key is refused rather than ignored, because a typo
that silently drops ``frozen_at`` would quietly unfreeze a held-out document.

**A document_id identifies a byte stream, not a document.** §32.7 makes a changed
download a new candidate version, so re-acquiring a document that has since been
revised produces a *new* entry with ``superseded_by`` set on the old one. Entries
are never edited once a checksum is recorded — that is what makes the corpus
reproducible.

No document content ever appears in an error raised here. The manifest describes
filings; it never contains them.
"""

import datetime
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from finsight.domain.identifiers import ContentAddress

CORPUS_ROOT: Final = Path("data") / "corpus"
"""Where the corpus lives, relative to the repository root.

Relative on purpose. The corpus is repository data rather than packaged data, so
there is no installed location to resolve against; the commands that read it are
documented as being run from the repository root. When that assumption is broken
the error names the path it looked for, because a bare "not found" would send a
reader hunting for a missing file rather than a wrong directory.
"""

MANIFEST_PATH: Final = CORPUS_ROOT / "manifest.toml"

MANIFEST_VERSION: Final = 1
"""The only manifest layout this code understands.

Checked rather than assumed, so a future layout change fails loudly instead of
being half-parsed by old code.
"""

UNRECORDED: Final = "unknown"
"""The value an entry carries when a descriptive field cannot be established.

Spelled out rather than left blank, so a reader can tell "nobody could determine
this" from "nobody filled this in".
"""


def _is_unrecorded(value: str) -> bool:
    return not value.strip() or value.strip().lower() == UNRECORDED


_DOCUMENT_ID: Final = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
"""Lowercase, hyphen-separated. Restrictive on purpose: the id appears in file
names, report output and decision records, so it must never need escaping."""


class CorpusError(RuntimeError):
    """Base class for corpus governance failures.

    A separate family from ``DomainError`` and ``ObjectStoreError`` because these
    are failures of the *record* — a malformed manifest, a document that no
    longer matches its checksum, an attempt to read frozen evidence — rather than
    of a document or a backend.
    """


class ManifestError(CorpusError):
    """The manifest is malformed, or violates a corpus rule.

    Messages name the offending ``document_id`` and the rule it broke. They never
    echo a path or any part of a document.
    """


class Split(StrEnum):
    """Which partition a document belongs to.

    The values are exactly the directory names already reserved in
    ``.gitignore``, so the split recorded in the manifest and the directory the
    bytes sit in cannot disagree.
    """

    DEVELOPMENT = "development"
    HELD_OUT_PDF_CORE = "held_out_pdf_core"
    HELD_OUT_FORMAT_SUPPLEMENT = "held_out_format_supplement"

    @property
    def is_held_out(self) -> bool:
        """True for any split that must stay unread (§34.6, §34.7, §34.10)."""
        return self is not Split.DEVELOPMENT


class DocumentType(StrEnum):
    """The filing classes §5.1 and §32.2 admit."""

    ANNUAL_REPORT = "annual_report"
    DRHP = "drhp"
    RHP = "rhp"
    QUARTERLY_RESULT = "quarterly_result"
    FORM_10K = "form_10k"


class SourceRepository(StrEnum):
    """Where a document was obtained (§32.3).

    Restricted to official issuer, exchange, regulator and offer-document
    repositories. There is deliberately no value for a mirror, an aggregator or
    a third-party download.
    """

    SEBI = "sebi"
    BSE = "bse"
    NSE = "nse"
    ISSUER_IR = "issuer_ir"
    SEC_EDGAR = "sec_edgar"


class Redistribution(StrEnum):
    """Whether redistribution is permitted (§32.8).

    Recorded because §32.6 requires it, not because it changes where bytes are
    stored: **no source document is committed regardless of this value**. Public
    availability is not permission, so the default is the restrictive one and
    ``PERMITTED`` must be a deliberate, justified entry.
    """

    NOT_REDISTRIBUTABLE = "not_redistributable"
    PERMITTED = "permitted"


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """One acquired document, as the manifest records it."""

    document_id: str
    split: Split
    filename: str
    issuer_name: str
    issuer_identifier: str
    document_type: DocumentType
    jurisdiction: str
    fiscal_period: str
    period_end: datetime.date
    reporting_basis: str
    currency: str
    units_as_presented: str
    format: str
    byte_size: int
    sha256: str
    source_repository: SourceRepository
    source_url: str
    published_at: datetime.date
    retrieved_at: datetime.date
    redistribution: Redistribution
    selection_rationale: str
    expected_challenges: str
    """What this document is expected to be hard about, recorded *before*
    extraction runs. That ordering turns validation into a prediction test
    rather than a rationalisation of whatever happened."""

    frozen_at: datetime.date | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if not _DOCUMENT_ID.match(self.document_id):
            raise ManifestError(
                "document_id must be lowercase alphanumeric words joined by hyphens"
            )
        _require_bare_filename(self.filename, self.document_id)

        try:
            ContentAddress.sha256(self.sha256)
        except ValueError as error:
            raise ManifestError(f"{self.document_id}: sha256 is not a valid digest") from error

        if self.byte_size <= 0:
            raise ManifestError(f"{self.document_id}: byte_size must be positive")

        if self.split.is_held_out and self.frozen_at is None:
            raise ManifestError(
                f"{self.document_id}: a held-out entry must record frozen_at (§34.10)"
            )
        if not self.split.is_held_out and self.frozen_at is not None:
            raise ManifestError(
                f"{self.document_id}: frozen_at belongs only to a held-out entry"
            )

        for label, value in (
            ("issuer_name", self.issuer_name),
            ("selection_rationale", self.selection_rationale),
            ("expected_challenges", self.expected_challenges),
            ("source_url", self.source_url),
        ):
            if not value.strip():
                raise ManifestError(f"{self.document_id}: {label} is required")

    @property
    def relative_path(self) -> Path:
        """Where the bytes sit, relative to the repository root."""
        return CORPUS_ROOT / self.split.value / self.filename

    @property
    def unrecorded_fields(self) -> tuple[str, ...]:
        """Fields §32.6 asks for that this entry does not actually carry.

        The manifest permits honest absence, which is better than an estimate —
        but an absent field must not look like an overlooked one, so every gap
        is enumerable and gets printed during validation.
        """
        return tuple(
            name
            for name in (
                "issuer_identifier",
                "fiscal_period",
                "reporting_basis",
                "units_as_presented",
            )
            if _is_unrecorded(getattr(self, name))
        )


@dataclass(frozen=True, slots=True)
class Manifest:
    """Every acquired document, across every split."""

    manifest_version: int
    entries: tuple[CorpusEntry, ...]

    def for_split(self, split: Split) -> tuple[CorpusEntry, ...]:
        return tuple(entry for entry in self.entries if entry.split is split)

    def by_id(self, document_id: str) -> CorpusEntry | None:
        return next(
            (entry for entry in self.entries if entry.document_id == document_id), None
        )


def _require_bare_filename(filename: str, document_id: str) -> None:
    """Refuse anything that is not a plain file name.

    Deliberately *not* ``sanitize_filename``: that function cleans untrusted
    upload metadata and never raises, because a bad filename must not fail an
    upload. Here the filename becomes a real path component under
    ``data/corpus/``, so the only safe response to a separator or a traversal
    segment is refusal. The offending value is never echoed back.
    """
    if not filename.strip():
        raise ManifestError(f"{document_id}: filename is required")
    if filename != Path(filename).name or filename in {".", ".."}:
        raise ManifestError(
            f"{document_id}: filename must be a bare file name, "
            "with no directory separators or traversal segments"
        )


def _enum_value[E: StrEnum](
    kind: type[E], raw: object, document_id: str, field: str
) -> E:
    """Resolve a controlled value, refusing anything outside the permitted set.

    The type check is not redundant: TOML will happily hand over an integer or a
    boolean, and calling the enum with one raises ``TypeError`` rather than the
    ``ManifestError`` a caller expects.
    """
    permitted = ", ".join(member.value for member in kind)
    if not isinstance(raw, str):
        raise ManifestError(f"{document_id}: {field} must be one of: {permitted}")
    try:
        return kind(raw)
    except ValueError as error:
        raise ManifestError(
            f"{document_id}: {field} must be one of: {permitted}"
        ) from error


_FIELD_NAMES: Final[frozenset[str]] = frozenset(field.name for field in fields(CorpusEntry))

_OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset({"frozen_at", "superseded_by"})
"""Keys an entry may omit entirely, both conditional by nature.

Every date is mandatory, including ``period_end`` and ``published_at``. Both
were briefly made optional on the belief that they could not always be
established — an annual report dates its board approval rather than its
publication, and a frozen offer document's restated period sits inside the
document. Exhausting the official sources disproved it: exchange submission
letters carry publication dates, and SEBI-hosted filing material states the
restated period. Every entry in the corpus now records both, so the flexibility
had no case left.

A descriptive field may still read ``UNRECORDED``, but its key must be present,
so an absent value is a deliberate act rather than a line someone forgot.
"""


def _entry_from(raw: Mapping[str, Any]) -> CorpusEntry:
    """Build one entry, refusing unknown and missing keys.

    Strictness here is a safety property, not pedantry: a mistyped ``frozen_at``
    would be silently ignored by a permissive parser, and a held-out document
    would quietly lose its freeze.
    """
    document_id = str(raw.get("document_id", "<unnamed>"))

    unknown = sorted(set(raw) - _FIELD_NAMES)
    if unknown:
        raise ManifestError(f"{document_id}: unknown manifest keys: {unknown}")

    required = _FIELD_NAMES - _OPTIONAL_FIELDS
    missing = sorted(required - set(raw))
    if missing:
        raise ManifestError(f"{document_id}: missing manifest keys: {missing}")

    values = dict(raw)
    values["split"] = _enum_value(Split, raw["split"], document_id, "split")
    values["document_type"] = _enum_value(
        DocumentType, raw["document_type"], document_id, "document_type"
    )
    values["source_repository"] = _enum_value(
        SourceRepository, raw["source_repository"], document_id, "source_repository"
    )
    values["redistribution"] = _enum_value(
        Redistribution, raw["redistribution"], document_id, "redistribution"
    )

    try:
        return CorpusEntry(**values)
    except TypeError as error:
        raise ManifestError(f"{document_id}: manifest entry is malformed") from error


def _check_corpus_rules(entries: Iterable[CorpusEntry]) -> None:
    """Enforce the rules that span entries rather than sitting inside one."""
    ordered = tuple(entries)

    seen: set[str] = set()
    for entry in ordered:
        if entry.document_id in seen:
            raise ManifestError(f"{entry.document_id}: document_id is not unique")
        seen.add(entry.document_id)

    for entry in ordered:
        if entry.superseded_by is not None and entry.superseded_by not in seen:
            raise ManifestError(
                f"{entry.document_id}: superseded_by names an entry that does not exist"
            )

    # §34.12: two filings from one issuer restate the same facts, so an issuer
    # spanning a split boundary leaks the answer into the held-out measurement.
    splits_by_issuer: dict[str, set[Split]] = {}
    for entry in ordered:
        splits_by_issuer.setdefault(entry.issuer_name, set()).add(entry.split)
    for issuer, splits in sorted(splits_by_issuer.items()):
        if len(splits) > 1:
            named = ", ".join(sorted(split.value for split in splits))
            raise ManifestError(
                f"issuer '{issuer}' appears in more than one split ({named}); "
                "§34.12 requires splits to be issuer-disjoint"
            )


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    """Read and validate the corpus manifest.

    Raises:
        ManifestError: the file is unreadable, malformed, or breaks a corpus rule.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(
            f"no corpus manifest at '{path}' — corpus commands run from the "
            "repository root"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"the corpus manifest is not valid TOML: {error}") from error

    unknown = sorted(set(raw) - {"manifest_version", "document"})
    if unknown:
        raise ManifestError(f"unknown top-level manifest keys: {unknown}")

    version = raw.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise ManifestError(
            f"manifest_version must be {MANIFEST_VERSION}, found {version!r}"
        )

    documents = raw.get("document", [])
    if not isinstance(documents, list):
        raise ManifestError("document entries must be a list of tables")

    entries = tuple(_entry_from(document) for document in documents)
    _check_corpus_rules(entries)
    return Manifest(manifest_version=version, entries=entries)
