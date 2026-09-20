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

from finsight.domain.errors import DomainError


class InvalidSourceElementError(DomainError):
    """An element or its location violates the source-representation rules.

    Messages name structural problems — a missing key, an impossible ordinal —
    and never include element text, so a rejection cannot leak document content
    into a log (CLAUDE.md §10).
    """


class ElementType(StrEnum):
    """The kind of structural element a row represents.

    Only the two the PDF path produces today exist. A new format widens this and
    the matching CHECK constraint by one additive line each; nothing downstream
    changes, because consumers reference an element by id, not by shape.
    """

    PAGE = "page"
    BLOCK = "block"


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
    """

    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if len(self.bbox) != 4:
            raise InvalidSourceElementError("bbox must hold four coordinates")
        x0, y0, x1, y1 = self.bbox
        if x1 < x0 or y1 < y0:
            raise InvalidSourceElementError(
                "bbox must run top-left to bottom-right with y increasing downward"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {"bbox": list(self.bbox)}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "BlockLocation":
        _require_exact_keys(mapping, frozenset({"bbox"}), "block")
        raw = mapping["bbox"]
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise InvalidSourceElementError("bbox must be a sequence of four numbers")
        if len(raw) != 4:
            raise InvalidSourceElementError("bbox must hold four coordinates")
        x0, y0, x1, y1 = (float(value) for value in raw)
        return cls(bbox=(x0, y0, x1, y1))


SourceLocation = PageLocation | BlockLocation

_LOCATION_FOR_TYPE: Final[dict[ElementType, type[PageLocation] | type[BlockLocation]]] = {
    ElementType.PAGE: PageLocation,
    ElementType.BLOCK: BlockLocation,
}


def location_from_mapping(
    element_type: ElementType,
    mapping: Mapping[str, Any],
) -> SourceLocation:
    """Rebuild a location from a stored JSONB payload."""
    location_type = _LOCATION_FOR_TYPE[element_type]
    return location_type.from_mapping(mapping)


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
        if self.element_type is ElementType.BLOCK and self.children:
            raise InvalidSourceElementError("a block has no child elements")

    @property
    def char_count(self) -> int | None:
        """The length of the verbatim text, or None when the element carries none.

        Derived rather than supplied, so it cannot disagree with the text it
        describes. The database repeats the check as a constraint.
        """
        return None if self.text is None else len(self.text)
