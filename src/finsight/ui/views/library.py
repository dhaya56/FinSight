"""Library: what has been ingested, and which generation of it is queryable.

The distinction this page exists to make visible is the one §11.12 draws: a document
version records what *exists*, a generation records what is *queryable*. Reprocessing
builds a shadow generation beside the active one and activates it atomically, so a
re-extraction never leaves a reader with no evidence.

Preview throughout: the corpus listing, generation states and extraction quality signals
all exist in PostgreSQL today, but no API route exposes them yet. Every issuer named here
is invented.
"""

from typing import Final

import streamlit as st

from finsight.ui import demo
from finsight.ui.theme import (
    BadgeColour,
    Wiring,
    page_header,
    panel_caption,
    state_badge,
)

_STATE_COLOUR: Final[dict[str, BadgeColour]] = {
    "active": "green",
    "shadow": "blue",
    "superseded": "grey",
    "failed": "red",
}

_STATE_MEANING = {
    "active": "Queryable. Retrieval is bound to active generations only.",
    "shadow": "Built and reconciling. Not yet visible to any reader.",
    "superseded": "Replaced by a newer generation. Retained for audit, never served.",
    "failed": "Did not complete. Its partial index points are never served.",
}


def render() -> None:
    """Render the Library page."""
    page_header(
        "Library",
        "Ingested filings, the generation of each that is queryable, and the extraction "
        "quality signals recorded for it.",
        Wiring.PREVIEW,
    )

    documents = demo.demo_documents()
    _summary(documents)
    st.container(height=12, border=False)

    left, right = st.columns([3, 1], gap="medium")
    with right:
        st.markdown("##### Filter")
        issuers = st.multiselect(
            "Issuer", sorted({d.issuer for d in documents}), placeholder="All issuers"
        )
        states = st.pills(
            "Generation state",
            sorted({d.generation_state for d in documents}),
            selection_mode="multi",
            default=None,
        )
        include_heldout = st.toggle(
            "Show held-out split",
            value=False,
            help=(
                "Held-out documents are listed but never opened for tuning. Keeping them "
                "visible and unusable is the point \N{EM DASH} §8 forbids tuning on them."
            ),
        )

        st.container(height=8, border=False)
        st.markdown("##### Generation states")
        for state, meaning in _STATE_MEANING.items():
            st.badge(state, color=_STATE_COLOUR[state])
            st.caption(meaning)

    shown = [
        d
        for d in documents
        if (not issuers or d.issuer in issuers)
        and (not states or d.generation_state in states)
        and (include_heldout or d.split != "held-out")
    ]

    with left:
        header, badge = st.columns([4, 1], vertical_alignment="center")
        with header:
            st.markdown(f"##### Documents ({len(shown)})")
        with badge:
            state_badge(Wiring.PREVIEW)

        if not shown:
            st.info("No documents match this filter.", icon=":material/filter_alt_off:")
        else:
            rows = [
                {
                    "Issuer": d.issuer,
                    "Document": d.document_type,
                    "Period": d.period,
                    "State": d.generation_state,
                    "Pages": d.pages,
                    "Chunks": d.chunks,
                    "Tables": d.tables_found,
                    "Reading order": d.reading_order_divergence,
                    "Split": d.split,
                    "Ingested": d.ingested,
                }
                for d in shown
            ]
            st.dataframe(
                rows,
                hide_index=True,
                width="stretch",
                column_config={
                    "Reading order": st.column_config.ProgressColumn(
                        "Reading-order divergence",
                        help=(
                            "Share of pages whose recovered reading order diverges from "
                            "the printed order. Chunk boundaries on those pages may be "
                            "wrong in a way nothing currently detects."
                        ),
                        min_value=0.0,
                        max_value=0.5,
                        format="%.0f%%",
                    ),
                    "Split": st.column_config.TextColumn(
                        "Split", help="Held-out is never tuned on."
                    ),
                    "Ingested": st.column_config.DateColumn("Ingested", format="DD MMM YYYY"),
                },
            )
        panel_caption(
            Wiring.PREVIEW,
            "These values exist in PostgreSQL today; no API route exposes them yet.",
        )

        _reprocessing_panel()


def _summary(documents: list[demo.DemoDocument]) -> None:
    """Headline counts across the corpus."""
    active = [d for d in documents if d.generation_state == "active"]
    columns = st.columns(5)
    columns[0].metric("Documents", len(documents), border=True)
    columns[1].metric("Active generations", len(active), border=True)
    columns[2].metric("Indexed passages", f"{sum(d.chunks for d in active):,}", border=True)
    columns[3].metric("Pages extracted", f"{sum(d.pages for d in documents):,}", border=True)
    columns[4].metric(
        "Tables found",
        f"{sum(d.tables_found for d in documents):,}",
        help="Found, not admitted. No table detector passes the bar yet (ADR-003).",
        border=True,
    )


def _reprocessing_panel() -> None:
    """Show the shadow-generation mechanism, which is the hard part of reprocessing."""
    st.container(height=10, border=False)
    with st.container(border=True):
        header, badge = st.columns([5, 1], vertical_alignment="center")
        with header:
            st.markdown("##### Reprocessing")
        with badge:
            state_badge(Wiring.PREVIEW)
        st.caption(
            "Changing a parser, a chunking rule or an embedding model means re-deriving "
            "every passage. A new generation is built in the shadow of the active one and "
            "swapped in only once both stores reconcile, so a reader is never left "
            "without evidence and a half-built index is never served."
        )
        stages = [
            ("Active", "Northwind Q2 FY2025-26, config v3", True),
            ("Shadow building", "re-chunking under config v4", True),
            ("Reconciling", "PostgreSQL 398 / Qdrant 398", False),
            ("Activate", "atomic pointer swap", False),
            ("Supersede", "previous generation retained for audit", False),
        ]
        columns = st.columns(len(stages))
        for column, (label, note, done) in zip(columns, stages, strict=True):
            css = "fs-stage fs-stage-done" if done else "fs-stage"
            with column:
                st.html(
                    f'<div class="{css}"><span class="fs-stage-label">{label}</span>'
                    f'<span class="fs-stage-note">{note}</span></div>'
                )
        st.container(height=6, border=False)
        st.button(
            "Rebuild index from PostgreSQL",
            icon=":material/sync:",
            disabled=True,
            help="Not wired. Qdrant is a derived, rebuildable index (§29.2).",
        )
