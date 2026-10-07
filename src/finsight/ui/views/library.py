"""Library: what has been ingested, how well it was read, and what of it is queryable.

Three questions a reader of a filing-analysis system actually has, in order:

* **what do I have?** — the table;
* **can I trust what was taken out of it?** — the extraction state, the regions nothing could
  read, and the table verdicts;
* **what can a question actually reach?** — the active generation's passage count, which is a
  different number from "ingested" and the one that matters.

The distinction running through all of it is §11.12's: a document version records what
*exists*, a generation records what is *queryable*. A document can be fully extracted and
still unreachable, which is the state a listing of "ingested documents" would misreport.

**Live.** Every figure comes from PostgreSQL through the authenticated API.

**What this page deliberately does not have.** A legend explaining five generation states, and
a reprocessing diagram ending in a disabled button. Both described the system rather than
reporting on it, and a control that cannot be pressed is worse than no control: the states are
in the column tooltip, where a reader meets them.
"""

from typing import Any, Final

import streamlit as st

from finsight.ui.client import ApiError, client_from_environment
from finsight.ui.theme import page_header

_NOT_BUILT: Final = "not built"

_STATE_HELP: Final = (
    "Which generation of this document a question can reach (§11.12).\n\n"
    "- **active** — queryable. Retrieval is bound to active generations alone.\n"
    "- **shadow** — built and reconciling. Not yet visible to any reader.\n"
    "- **superseded** — replaced. Retained for audit, never served.\n"
    "- **failed** — did not complete. Its partial index points are never served.\n"
    "- **not built** — extracted, but never chunked. Nothing about it is retrievable."
)


def render() -> None:
    """Render the Library page."""
    page_header(
        "Library",
        "Ingested filings, how completely each was read, and what a question can reach.",
    )

    try:
        payload = client_from_environment().library()
    except ApiError as error:
        st.error(str(error), icon=":material/error:")
        return

    documents = [_normalise(entry) for entry in payload.get("documents") or ()]
    if not documents:
        st.info(
            "Nothing has been ingested yet. Documents are brought in from the command "
            "line: `python -m finsight.cli.main corpus ingest --split development`.",
            icon=":material/inbox:",
        )
        return

    _summary(documents)
    st.container(height=14, border=False)
    _table(documents)
    st.container(height=14, border=False)
    _detail(documents)


def _normalise(entry: dict[str, Any]) -> dict[str, Any]:
    """One wire row flattened for display, with the nulls given words.

    ``generation_state`` is null when no generation exists. Rendered as an empty cell it
    would read as "unknown"; it is a definite state and is named.
    """
    tables = int(entry.get("tables") or 0)
    accepted = int(entry.get("tables_accepted") or 0)
    return {
        "issuer": entry.get("issuer_name") or "Issuer not recorded",
        "document_type": entry.get("document_type") or "\N{EM DASH}",
        "period": entry.get("fiscal_period") or "\N{EM DASH}",
        "basis": entry.get("reporting_basis") or "\N{EM DASH}",
        "state": entry.get("generation_state") or _NOT_BUILT,
        "config": entry.get("chunking_config_version") or "\N{EM DASH}",
        "extraction_state": entry.get("extraction_state") or "\N{EM DASH}",
        "extraction_config": entry.get("extraction_config_version") or "\N{EM DASH}",
        "extraction_seconds": float(entry.get("extraction_seconds") or 0.0),
        "pages": int(entry.get("pages") or 0),
        "blocks": int(entry.get("blocks") or 0),
        "chunks": int(entry.get("chunks") or 0),
        "children": int(entry.get("child_chunks") or 0),
        "parents": int(entry.get("parent_chunks") or 0),
        "median_tokens": int(entry.get("median_child_tokens") or 0),
        "tables": tables,
        "accepted": accepted,
        "rejected": int(entry.get("tables_rejected") or 0),
        "unreadable": int(entry.get("unreadable_regions") or 0),
        "footnotes": int(entry.get("footnotes") or 0),
        "megabytes": round(int(entry.get("byte_size") or 0) / 1_000_000, 1),
        "sections": entry.get("sections") or [],
        "ingested_at": entry.get("ingested_at"),
        "activated_at": entry.get("activated_at"),
    }


def _summary(documents: list[dict[str, Any]]) -> None:
    """Four figures, each of which answers a question rather than filling a row.

    "Tables retrievable" is deliberately zero and deliberately present. 861 tables were
    found across this corpus and none of them can be retrieved, because ADR-003 admits no
    detector — a metric that is always zero is worth keeping when the alternative is a
    reader assuming otherwise.
    """
    active = [d for d in documents if d["state"] == "active"]
    columns = st.columns(4)
    columns[0].metric(
        "Queryable filings",
        f"{len(active)} of {len(documents)}",
        help="Documents with an active generation. The rest cannot be retrieved at all.",
        border=True,
    )
    columns[1].metric(
        "Passages indexed",
        f"{sum(d['chunks'] for d in active):,}",
        help=(
            f"{sum(d['children'] for d in active):,} children for matching and "
            f"{sum(d['parents'] for d in active):,} parents for context (§18.5)."
        ),
        border=True,
    )
    columns[2].metric(
        "Pages read",
        f"{sum(d['pages'] for d in documents):,}",
        delta=f"-{sum(d['unreadable'] for d in documents)} unreadable regions",
        delta_color="inverse",
        help=(
            "Regions that carried no extractable text. Recorded as gaps rather than "
            "dropped, which is why the number is knowable."
        ),
        border=True,
    )
    columns[3].metric(
        "Tables retrievable",
        "0",
        delta=f"{sum(d['tables'] for d in documents):,} found",
        delta_color="off",
        help=(
            "No table detector passes the bar, so table cells are excluded from "
            "retrieval entirely (ADR-003). A figure that exists only inside a table "
            "cannot be found today."
        ),
        border=True,
    )


def _table(documents: list[dict[str, Any]]) -> None:
    """The corpus itself. Filters sit above it rather than in a column beside it."""
    st.markdown("##### Filings")
    issuers = st.multiselect(
        "Issuer",
        sorted({d["issuer"] for d in documents}),
        placeholder="All issuers",
        label_visibility="collapsed",
    )
    shown = [d for d in documents if not issuers or d["issuer"] in issuers]

    if not shown:
        st.info("No documents match this filter.", icon=":material/filter_alt_off:")
        return

    st.dataframe(
        [
            {
                "Issuer": d["issuer"],
                "Document": d["document_type"],
                "Period": d["period"],
                "Basis": d["basis"],
                "Queryable": d["state"],
                "Extraction": d["extraction_state"],
                "Pages": d["pages"],
                "Unreadable": d["unreadable"],
                "Passages": d["chunks"],
                "Median tokens": d["median_tokens"],
                "Tables": d["tables"],
                "Size": d["megabytes"],
            }
            for d in shown
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "Queryable": st.column_config.TextColumn("Queryable", help=_STATE_HELP),
            "Extraction": st.column_config.TextColumn(
                "Extraction",
                help=(
                    "**partial is the common case and not a failure.** It means some "
                    "region could not be read and was recorded as a gap rather than "
                    "silently dropped."
                ),
            ),
            "Unreadable": st.column_config.NumberColumn(
                "Unreadable",
                help="Regions that carry a failure reason instead of text. Not indexed.",
            ),
            "Passages": st.column_config.NumberColumn(
                "Passages",
                help=(
                    "Chunks in the active generation \N{EM DASH} what a question can "
                    "reach. Zero means nothing about this document is retrievable."
                ),
            ),
            "Median tokens": st.column_config.NumberColumn(
                "Median tokens",
                help=(
                    "Median size of a retrieval child. The reranker filters most short "
                    "ones out before they reach an answer."
                ),
            ),
            "Tables": st.column_config.NumberColumn(
                "Tables", help="Found, not retrievable (ADR-003)."
            ),
            "Size": st.column_config.NumberColumn("Size", format="%.1f MB"),
        },
    )


def _detail(documents: list[dict[str, Any]]) -> None:
    """One filing, end to end: what was read, what was judged, what is queryable.

    A selector rather than a click-through on the table, because a dataframe selection
    costs a rerun per click and this needs none of that interactivity to be useful.
    """
    st.markdown("##### Processing detail")
    labels = {f"{d['issuer']} \N{BULLET} {d['period']}": d for d in documents}
    # **No `on_change="ignore"` here, and that is the point.** It is right for the Ask
    # page's scope filters, which must not disturb a conversation until a question is
    # sent — but here the rerun *is* the update. Suppressing it left the panel below
    # showing the first filing whatever the reader chose.
    chosen = st.selectbox(
        "Filing",
        options=list(labels),
        index=0,
        label_visibility="collapsed",
    )
    document = labels[str(chosen)]

    stages = (
        (
            "Extracted",
            f"{document['pages']:,} pages, {document['blocks']:,} blocks",
            f"config {document['extraction_config']} \N{BULLET} "
            f"{document['extraction_seconds']:.1f}s \N{BULLET} "
            f"{document['extraction_state']}",
            document["extraction_state"] == "succeeded",
        ),
        (
            "Judged",
            f"{document['accepted']:,} tables accepted",
            f"{document['rejected']:,} rejected \N{BULLET} none retrievable (ADR-003)",
            False,
        ),
        (
            "Chunked",
            f"{document['children']:,} children, {document['parents']:,} parents",
            f"config {document['config']} \N{BULLET} median "
            f"{document['median_tokens']} tokens",
            document["chunks"] > 0,
        ),
        (
            "Queryable",
            document["state"],
            _activated(document),
            document["state"] == "active",
        ),
    )

    columns = st.columns(len(stages))
    for column, (label, headline, note, done) in zip(columns, stages, strict=True):
        css = "fs-stage fs-stage-done" if done else "fs-stage"
        with column:
            st.html(
                f'<div class="{css}"><span class="fs-stage-label">{label}</span>'
                f"<span>{headline}</span><br>"
                f'<span class="fs-stage-note">{note}</span></div>'
            )

    if document["unreadable"]:
        st.caption(
            f"**{document['unreadable']:,} regions in this filing could not be read** and "
            "are absent from the index. They are recorded as gaps with a reason, so what "
            "is missing is knowable rather than merely absent."
        )

    _sections(document)


def _sections(document: dict[str, Any]) -> None:
    """What the filing actually contains, by retrievable passage count.

    **A page count says how long a document is; this says what is in it.** Whether a
    filing has a risk section, how much of it is notes to the accounts, and therefore
    whether a question about either can be answered at all — which is the thing a reader
    of a filing library wants and could not previously get.

    Counted over retrieval children in the active generation, so a section listed here is
    one a question can actually reach. A section that exists in the document but produced
    no retrievable passages is correctly absent.
    """
    sections = document.get("sections") or []
    if not sections:
        return

    st.container(height=10, border=False)
    st.markdown("**What this filing contains**")
    st.bar_chart(
        [
            {"Section": _shorten(str(name)), "Passages": int(count)}
            for name, count in sections
        ],
        x="Section",
        y="Passages",
        horizontal=True,
        height=max(180, 32 * len(sections)),
        color="#4f46e5",
    )
    st.caption(
        "Top-level sections by retrievable passage count. A section with no passages "
        "cannot be reached by a question, whatever the document holds \N{EM DASH} these "
        "are the ones that can. Use the Section filter on the Ask page to restrict an "
        "answer to one of them."
    )


def _shorten(name: str, limit: int = 54) -> str:
    """Section headings in a filing run long; a chart axis does not."""
    flat = " ".join(name.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}\N{HORIZONTAL ELLIPSIS}"


def _activated(document: dict[str, Any]) -> str:
    """When this generation became queryable, or why it is not."""
    if document["state"] != "active":
        return "nothing about this filing can be retrieved"
    activated = document.get("activated_at")
    return f"activated {str(activated)[:16].replace('T', ' ')}" if activated else "active"
