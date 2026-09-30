"""The source representation as the domain sees it.

PROJECT_BLUEPRINT.md §14.5 defines one structural element model that every format
adapter emits into — page, section, block, table, cell and span — without erasing
format-specific source locations. The records of that model are *source
elements*, and a *source region* is one or more of them.

Two rules shape everything in this module.

**Text is verbatim.** ``text`` holds exactly what the producer read: no
whitespace tidying, no Unicode folding, no ligature expansion, no digit or
separator normalisation. Citations are character offsets into that text (§14.9),
so normalising in place would silently misalign every citation already stored.
Interpretation belongs to the retrieval representation (§14.2) and to the
ledger's numeric parsing (§16); extraction preserves and interprets nothing. This
matters concretely for the rupee sign, Devanagari, non-breaking spaces in
European numerals, and lakh/crore digit grouping.

**A location is an address, never a claim.** ``location`` says where an element
sits in its source and nothing else. What an element *means* — a table cell's
header path, an XBRL fact's concept and unit — belongs in a typed extension table
keyed one-to-one to the element. Keeping the two apart is what lets a new format
introduce a location shape with no migration at all, while its semantics arrive
as constrained, indexable columns.

These types know nothing about SQLAlchemy, PDFs, or any producer library
(CLAUDE.md §11). A producer emits :class:`ExtractedElement` trees; the repository
turns them into rows.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from finsight.domain.errors import DomainError


class InvalidSourceElementError(DomainError):
    """An element or its location violates the source-representation rules.

    Messages name structural problems — a missing key, an impossible ordinal —
    and never include element text, so a rejection cannot leak document content
    into a log (CLAUDE.md §10).
    """


class ElementType(StrEnum):
    """The kind of structural element a row represents.

    A new format widens this and the matching CHECK constraint by one additive
    line each; nothing downstream changes, because consumers reference an element
    by id, not by shape.

    There is deliberately **no** ``row``. §14.5 enumerates page, section, block,
    table, cell and span, and a row is recoverable from a cell's ``row_index``
    without spending an element on it — a 20-by-8 table costs 161 rows this way
    against 181 with a row level. Row *grouping* for retrieval (§17.5) is a
    retrieval representation and does not belong here at all.
    """

    PAGE = "page"
    BLOCK = "block"
    TABLE = "table"
    CELL = "cell"


class ExtractionState(StrEnum):
    """How an extraction run finished.

    ``PARTIAL`` is a success with recorded coverage gaps (§11.11): some elements
    carry a ``failure_reason`` instead of text, and the run's output is still
    usable. ``FAILED`` produced nothing usable and may be retried.
    """

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


_ROTATIONS: Final[frozenset[int]] = frozenset({0, 90, 180, 270})


def _require_exact_keys(
    mapping: Mapping[str, Any],
    expected: frozenset[str],
    kind: str,
) -> None:
    """Reject a location payload whose keys are not exactly the expected set.

    Unknown keys are refused rather than ignored. An ignored key is how semantics
    quietly enter an address payload, which is the one thing ``location`` exists
    to prevent.
    """
    present = frozenset(mapping)
    if present == expected:
        return
    missing = sorted(expected - present)
    unknown = sorted(present - expected)
    raise InvalidSourceElementError(
        f"{kind} location keys are wrong: missing={missing} unknown={unknown}"
    )


def _check_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Reject a box that is not top-left to bottom-right in displayed space.

    Shared by every box-shaped location so the convention is enforced in exactly
    one place. A block, a table and a cell are all rectangles on a page and all
    inherit the same trap: a silently flipped axis leaves stored citations
    pointing at the mirror image of their evidence.
    """
    if len(bbox) != 4:
        raise InvalidSourceElementError("bbox must hold four coordinates")
    x0, y0, x1, y1 = bbox
    if x1 < x0 or y1 < y0:
        raise InvalidSourceElementError(
            "bbox must run top-left to bottom-right with y increasing downward"
        )


def _bbox_from(raw: Any) -> tuple[float, float, float, float]:
    """Parse a stored bbox payload into four floats."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise InvalidSourceElementError("bbox must be a sequence of four numbers")
    if len(raw) != 4:
        raise InvalidSourceElementError("bbox must hold four coordinates")
    x0, y0, x1, y1 = (float(value) for value in raw)
    return (x0, y0, x1, y1)


def _non_negative(value: int, field: str) -> int:
    if value < 0:
        raise InvalidSourceElementError(f"{field} must not be negative")
    return value


def _at_least_one(value: int, field: str) -> int:
    if value < 1:
        raise InvalidSourceElementError(f"{field} must be at least 1")
    return value


@dataclass(frozen=True, slots=True)
class PageLocation:
    """Where a page sits in a PDF, and how large it is.

    ``width`` and ``height`` are PDF points of the page as displayed, so a
    bounding box can be interpreted without reopening the original. ``rotation``
    is the page's own rotation, recorded because a viewer applies it and a
    citation highlight must agree with what the reader sees.
    """

    page_number: int
    width: float
    height: float
    rotation: int

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise InvalidSourceElementError("page_number is one-based")
        if self.width <= 0 or self.height <= 0:
            raise InvalidSourceElementError("page dimensions must be positive")
        if self.rotation not in _ROTATIONS:
            raise InvalidSourceElementError("rotation must be 0, 90, 180 or 270")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PageLocation":
        _require_exact_keys(
            mapping,
            frozenset({"page_number", "width", "height", "rotation"}),
            "page",
        )
        return cls(
            page_number=int(mapping["page_number"]),
            width=float(mapping["width"]),
            height=float(mapping["height"]),
            rotation=int(mapping["rotation"]),
        )


@dataclass(frozen=True, slots=True)
class BlockLocation:
    """Where a block sits on its page, as ``(x0, y0, x1, y1)`` in PDF points.

    The origin is the **top-left** corner with y increasing downward, matching
    how the page is displayed rather than the PDF format's own bottom-left user
    space. The convention is fixed here and pinned by the producer's tests: a
    silent flip would leave every stored citation pointing at the mirror image of
    its evidence, and nothing downstream could detect it.

    "As displayed" includes page rotation. A box must lie within the page
    rectangle recorded on its parent, and producers are responsible for putting
    it there — PyMuPDF, for one, reports text in the *unrotated* space and
    leaves the transform to the caller. This is stated because assuming
    otherwise cost 78 pages of a real annual report their citation coordinates.
    """

    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        _check_bbox(self.bbox)

    def to_mapping(self) -> dict[str, Any]:
        return {"bbox": list(self.bbox)}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "BlockLocation":
        _require_exact_keys(mapping, frozenset({"bbox"}), "block")
        return cls(bbox=_bbox_from(mapping["bbox"]))


@dataclass(frozen=True, slots=True)
class TableLocation:
    """Where a table's bounding region sits on its page (§12.6).

    A table belongs to exactly one page. A table continued across a page break
    (§12.8) is therefore two table elements, which is what keeps §12.8's
    requirement that "page-specific source cells remain addressable" true by
    construction. Linking the halves is a later concern and deliberately has no
    column here: an unpopulated continuation pointer would be a placeholder for a
    phase that has not started (CLAUDE.md §11).
    """

    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        _check_bbox(self.bbox)

    def to_mapping(self) -> dict[str, Any]:
        return {"bbox": list(self.bbox)}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "TableLocation":
        _require_exact_keys(mapping, frozenset({"bbox"}), "table")
        return cls(bbox=_bbox_from(mapping["bbox"]))


@dataclass(frozen=True, slots=True)
class CellLocation:
    """Where a cell sits, both on the page and in its table's grid.

    The grid position is here rather than in the semantics extension because it
    is an *address*, in the same sense as ``Sheet1!B7``: it says where the cell
    is, not what it means. ``row_span`` and ``column_span`` come with it because a
    merged cell's address includes its extent, exactly as a bbox does.

    Recording spans at all is what makes a flattened spanning header detectable.
    A detector that collapses "Year ended March 31" spanning two columns into one
    single-column cell is not obviously wrong from its text; it is obviously wrong
    from its span, and §17.2's header path is what would silently mislabel every
    number beneath it.
    """

    bbox: tuple[float, float, float, float]
    row_index: int
    column_index: int
    row_span: int = 1
    column_span: int = 1

    def __post_init__(self) -> None:
        _check_bbox(self.bbox)
        _non_negative(self.row_index, "row_index")
        _non_negative(self.column_index, "column_index")
        _at_least_one(self.row_span, "row_span")
        _at_least_one(self.column_span, "column_span")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "bbox": list(self.bbox),
            "row_index": self.row_index,
            "column_index": self.column_index,
            "row_span": self.row_span,
            "column_span": self.column_span,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "CellLocation":
        _require_exact_keys(
            mapping,
            frozenset(
                {"bbox", "row_index", "column_index", "row_span", "column_span"}
            ),
            "cell",
        )
        return cls(
            bbox=_bbox_from(mapping["bbox"]),
            row_index=int(mapping["row_index"]),
            column_index=int(mapping["column_index"]),
            row_span=int(mapping["row_span"]),
            column_span=int(mapping["column_span"]),
        )


SourceLocation = PageLocation | BlockLocation | TableLocation | CellLocation

_LOCATION_FOR_TYPE: Final[dict[ElementType, type[SourceLocation]]] = {
    ElementType.PAGE: PageLocation,
    ElementType.BLOCK: BlockLocation,
    ElementType.TABLE: TableLocation,
    ElementType.CELL: CellLocation,
}


def location_from_mapping(
    element_type: ElementType,
    mapping: Mapping[str, Any],
) -> SourceLocation:
    """Rebuild a location from a stored JSONB payload."""
    location_type = _LOCATION_FOR_TYPE[element_type]
    return location_type.from_mapping(mapping)


@dataclass(frozen=True, slots=True)
class TableSemantics:
    """What a table means, as opposed to where it is (§12.6).

    Only the caption, because that is the only table-level meaning §12.6 names
    that is not derivable from the cells beneath it.
    """

    caption: str | None = None


@dataclass(frozen=True, slots=True)
class CellSemantics:
    """What a cell means (§17.2): its header path, row-label path and footnotes.

    Both paths are ordered outermost-first, so ``("Year ended March 31", "2025")``
    reads as a spanning header narrowing to a column. They are tuples rather than
    a joined string because a delimiter would be ambiguous the moment a header
    contains it, and because the persistence side stores them as typed arrays.

    ``is_header`` marks a cell that labels others rather than carrying a value.
    It is stored rather than inferred from ``row_index == 0``: financial tables
    routinely open with a units row, a blank row, or two header rows, so position
    does not identify a header.

    Nothing here is interpreted. A header path records the text a header cell
    held; deciding that "FY2024" denotes a period is §16 work, and this module
    preserves and interprets nothing.
    """

    header_path: tuple[str, ...] = ()
    row_label_path: tuple[str, ...] = ()
    footnote_refs: tuple[str, ...] = ()
    is_header: bool = False


ElementSemantics = TableSemantics | CellSemantics

_SEMANTICS_FOR_TYPE: Final[dict[ElementType, type[ElementSemantics]]] = {
    ElementType.TABLE: TableSemantics,
    ElementType.CELL: CellSemantics,
}

_TYPES_WITH_CHILDREN: Final[frozenset[ElementType]] = frozenset(
    {ElementType.PAGE, ElementType.TABLE}
)
"""Which element types may contain others.

Stated as the containers rather than the leaves. The rule it replaces named only
``block``, which would have silently permitted a cell to hold children the moment
cells existed.
"""


@dataclass(frozen=True, slots=True)
class ExtractedElement:
    """One element a producer emitted, before it is persisted.

    ``extraction_method`` and ``extraction_method_version`` sit on the element
    rather than only on the run because §16.5 requires provenance to record the
    extraction method *and* version, and §12.9 routes per page: a run whose
    page 3 used the fast path and page 47 a layout-aware path must be
    representable without a second schema.
    """

    element_type: ElementType
    ordinal: int
    locator: str
    location: SourceLocation
    extraction_method: str
    extraction_method_version: str
    text: str | None = None
    failure_reason: str | None = None
    children: tuple["ExtractedElement", ...] = ()
    semantics: ElementSemantics | None = None
    """Typed meaning for the element types that have any, None for the rest.

    Mirrors the persistence split: ``location`` and this field are separate for
    the same reason ``source_elements`` and its extension tables are, so that a
    new format can introduce an address shape without touching semantics and
    vice versa.
    """

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvalidSourceElementError("ordinal must not be negative")
        if not self.locator.strip():
            raise InvalidSourceElementError("locator must be a non-empty citation address")
        if not self.extraction_method.strip():
            raise InvalidSourceElementError("extraction_method is required")
        if not self.extraction_method_version.strip():
            raise InvalidSourceElementError("extraction_method_version is required")
        if self.text is not None and self.failure_reason is not None:
            raise InvalidSourceElementError(
                "an element carries extracted text or a failure reason, never both"
            )
        expected = _LOCATION_FOR_TYPE[self.element_type]
        if not isinstance(self.location, expected):
            raise InvalidSourceElementError(
                f"{self.element_type.value} requires a {expected.__name__}"
            )
        if self.children and self.element_type not in _TYPES_WITH_CHILDREN:
            raise InvalidSourceElementError(
                f"a {self.element_type.value} has no child elements"
            )
        self._check_semantics()

    def _check_semantics(self) -> None:
        """Refuse semantics of the wrong shape, or on a type that has none.

        Refused rather than ignored, for the reason unknown location keys are:
        silently dropping a producer's header path would lose §17.2 evidence with
        nothing to show that it happened.
        """
        expected_semantics = _SEMANTICS_FOR_TYPE.get(self.element_type)
        if expected_semantics is None:
            if self.semantics is not None:
                raise InvalidSourceElementError(
                    f"a {self.element_type.value} carries no semantics"
                )
            return
        if self.semantics is not None and not isinstance(
            self.semantics, expected_semantics
        ):
            raise InvalidSourceElementError(
                f"{self.element_type.value} requires a {expected_semantics.__name__}"
            )

    @property
    def char_count(self) -> int | None:
        """The length of the verbatim text, or None when the element carries none.

        Derived rather than supplied, so it cannot disagree with the text it
        describes. The database repeats the check as a constraint.
        """
        return None if self.text is None else len(self.text)


@dataclass(frozen=True, slots=True)
class RecordedExtraction:
    """The outcome of recording one extraction run.

    Mirrors ``RecordedVersion``: a plain value the caller can hold after the
    transaction has closed, rather than an ORM row that would expire with it.
    """

    run_id: UUID
    document_version_id: UUID
    state: ExtractionState
    element_count: int
    already_existed: bool = False
    """True when a run for this configuration was already recorded.

    Re-extracting the same version under the same producer policy and
    configuration version is a no-op, enforced by the partial unique index
    rather than by a prior read.
    """
