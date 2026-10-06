"""Ask: the live retrieval surface, with the Phase 8 answer shape alongside it.

The passages are real. They come from the indexed corpus through the authenticated
API, carry the source element ids they were built from, and report honestly when a
retriever was unreachable.

The composed answer above them is not real yet. It shows what Phase 8 releases: a
numeral enters an answer only through a typed placeholder bound to a source region,
and the Evidence Gate refuses release of anything a retrieved region does not support.
It is labelled, and it is rendered from a fixture whose issuer does not exist.
"""

from typing import Any

import streamlit as st

from finsight.ui import demo
from finsight.ui.client import ApiClient, ApiError, client_from_environment
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge

RESULT_KEY = "ask.result"
QUERY_KEY = "ask.query"

_EXAMPLES = (
    "What does the company say about credit risk?",
    "How is revenue recognised?",
    "What are the principal risks to the business?",
    "What dividend was declared?",
)

# Plain-language consequence of each degradation flag the API can return. The flag
# names are stable identifiers and mean nothing to a reader.
_FLAG_MEANINGS = {
    "dense_unavailable": (
        "The vector index could not be reached, so these results are lexical only. A "
        "passage matching in meaning but not in wording was not considered."
    ),
    "lexical_fallback_postgres_fts": (
        "BM25 was unreachable and PostgreSQL full-text search answered instead. It has "
        "no term-frequency saturation, no length normalisation and no IDF, so this "
        "ranking is not comparable with a BM25 one."
    ),
    "reranker_unavailable": (
        "The cross-encoder could not be loaded, so the order below is the fusion order. "
        "The same passages were considered; only their ranking is less refined."
    ),
}


def render() -> None:
    """Render the Ask page."""
    page_header(
        "Ask",
        "Put a question to the ingested filings and read the passages behind it.",
        Wiring.PARTIAL,
    )

    try:
        client = client_from_environment()
    except ApiError as error:
        st.error(str(error))
        return

    options = _controls(client)
    query = st.text_input(
        "Question",
        key=QUERY_KEY,
        placeholder="What does the company say about credit risk?",
        label_visibility="collapsed",
    )

    with st.container(horizontal=True, gap="small"):
        asked = st.button(
            "Search", type="primary", icon=":material/search:", disabled=not query.strip()
        )
        if st.button("Clear", icon=":material/close:", disabled=RESULT_KEY not in st.session_state):
            st.session_state.pop(RESULT_KEY, None)
            st.rerun()

    _example_chips()

    if asked:
        _run(client, query.strip(), options)

    result = st.session_state.get(RESULT_KEY)
    if result is None:
        _empty_state()
        return

    _answer_panel(result)
    _passages_panel(result)


def _controls(client: ApiClient) -> dict[str, Any]:
    """Sidebar controls and the §20.2 hard filters."""
    with st.sidebar:
        st.subheader("Retrieval", divider="grey")
        limit = st.slider("Passages", 1, 25, 5, help="How many results to return.")
        rerank = st.toggle(
            "Cross-encoder reranking",
            value=True,
            help=(
                "Measured on this host: about 1.9 s with reranking against 0.13 s "
                "without. Whether it orders better is unmeasured \N{EM DASH} there is "
                "no golden question set yet."
            ),
        )

        st.subheader("Scope", divider="grey")
        st.caption(
            "Hard filters. These bound what may be returned and never affect ranking "
            "\N{EM DASH} similarity cannot override scope."
        )
        issuer = st.text_input("Issuer", placeholder="Any")
        document_type = st.text_input("Document type", placeholder="Any")
        year = st.number_input("Fiscal year", 1990, 2100, value=None, step=1, help="Any if empty.")
        evidence = st.segmented_control(
            "Evidence type",
            options=["Any", "Narrative", "Table"],
            default="Any",
            help="Table-derived evidence is excluded from retrieval today (ADR-003).",
        )
        section = st.text_input("Section", placeholder="Any")

    mapped = {"Any": None, "Narrative": "narrative", "Table": "table_derived"}
    return {
        "limit": limit,
        "rerank": rerank,
        "filters": {
            "issuer_name": issuer,
            "document_type": document_type,
            "fiscal_year": int(year) if year is not None else None,
            "evidence_type": mapped.get(evidence or "Any"),
            "section": section,
        },
    }


def _example_chips() -> None:
    """Offer a few starting questions, which is what an empty box needs."""
    picked = st.pills(
        "Try",
        options=list(_EXAMPLES),
        default=None,
        label_visibility="collapsed",
        key="ask.examples",
    )
    if picked and st.session_state.get(QUERY_KEY) != picked:
        st.session_state[QUERY_KEY] = picked
        st.rerun()


def _run(client: ApiClient, query: str, options: dict[str, Any]) -> None:
    """Call the API, keeping the page usable when it fails."""
    with st.spinner("Retrieving from the corpus\N{HORIZONTAL ELLIPSIS}"):
        try:
            st.session_state[RESULT_KEY] = client.search(
                query,
                limit=options["limit"],
                rerank=options["rerank"],
                filters=options["filters"],
            )
        except ApiError as error:
            st.session_state.pop(RESULT_KEY, None)
            st.error(str(error), icon=":material/error:")


def _empty_state() -> None:
    """What the page shows before anything has been asked."""
    st.container(height=12, border=False)
    with st.container(border=True):
        st.markdown("#### Nothing asked yet")
        st.caption(
            "Retrieval is hybrid: BM25 over PostgreSQL lexemes and dense vectors over "
            "Qdrant, fused by reciprocal rank and reordered by a local cross-encoder. "
            "Every passage returned names the source regions it was built from."
        )
        columns = st.columns(3)
        for column, (label, value, note) in zip(
            columns,
            (
                ("Indexed passages", "4,969", "across 3 active generations"),
                ("Retrievers", "2 + rerank", "BM25, dense, cross-encoder"),
                ("Typical query", "1.9 s", "0.13 s with reranking off"),
            ),
            strict=True,
        ):
            with column:
                st.metric(label, value, help=note, border=True)
        st.caption("Figures measured on this host, not targets.")


def _answer_panel(result: dict[str, Any]) -> None:
    """The Phase 8 answer shape, rendered from a fixture and labelled as one."""
    question = result.get("query", "")
    preview = demo.answer_preview(question)

    with st.container(border=True):
        head, badge = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.markdown("##### Composed answer")
        with badge:
            state_badge(Wiring.PREVIEW)

        body = " ".join(
            sentence + "".join(f'<span class="fs-cite">{index}</span>' for index in citations)
            for sentence, citations in preview.sentences
        )
        st.html(f'<div style="line-height:1.75;font-size:0.97rem">{body}</div>')

        st.container(height=8, border=False)
        metrics = st.columns(4)
        metrics[0].metric("Support band", preview.support_band, border=True)
        metrics[1].metric("Placeholders bound", preview.placeholders_substituted, border=True)
        metrics[2].metric("Numerals refused", preview.refused_numerals, border=True)
        metrics[3].metric("Model", preview.model, border=True)

        with st.expander("Evidence Gate", icon=":material/verified_user:"):
            st.caption(
                "Every check must pass before an answer is released. A failed check "
                "withholds the answer rather than annotating it."
            )
            for label, passed, detail in preview.gate_checks:
                icon = ":material/check_circle:" if passed else ":material/cancel:"
                with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                    st.badge("", icon=icon, color="green" if passed else "red")
                    st.markdown(f"**{label}** \N{EM DASH} {detail}")

        panel_caption(
            Wiring.PREVIEW,
            "Generation, typed placeholders and the Evidence Gate are Phase 8. The "
            "issuer named above does not exist; the passages below are real.",
        )


def _passages_panel(result: dict[str, Any]) -> None:
    """The live retrieved passages."""
    for flag in result.get("degraded", ()):
        st.warning(
            f"**Degraded \N{EM DASH} {flag}.** "
            f"{_FLAG_MEANINGS.get(flag, 'See the degradation flags in §20.12.')}",
            icon=":material/warning:",
        )

    candidates = result.get("candidates", ())
    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown(f"##### Retrieved evidence ({len(candidates)})")
    with badge:
        state_badge(Wiring.LIVE)

    if not candidates:
        st.info(
            "Nothing matched. The corpus holds only the filings that have been ingested "
            "and indexed, and table cells are deliberately excluded from retrieval "
            "(ADR-003), so a figure that exists only inside a table is not findable yet.",
            icon=":material/info:",
        )
        return

    strip = st.columns(5)
    strip[0].metric("Passages", len(candidates), border=True)
    strip[1].metric("Server time", f"{result.get('elapsed_ms', 0) / 1000:.2f} s", border=True)
    strip[2].metric(
        "Depth", result.get("depth", 0), help="Candidates considered before narrowing.", border=True
    )
    strip[3].metric("Lexical path", result.get("lexical_retriever", "\N{EM DASH}"), border=True)
    strip[4].metric("Reranked", "Yes" if result.get("reranked") else "No", border=True)

    for candidate in candidates:
        _passage_card(candidate)


def _passage_card(candidate: dict[str, Any]) -> None:
    """One passage, with its provenance inline rather than behind a click."""
    pages = candidate.get("page_numbers") or []
    page_label = (
        f"p. {pages[0]}"
        if len(pages) == 1
        else f"pp. {pages[0]}\N{EN DASH}{pages[-1]}"
        if pages
        else "page not recorded"
    )
    issuer = candidate.get("issuer_name") or "Issuer not recorded"
    period = candidate.get("fiscal_period") or "period not recorded"
    heading = candidate.get("heading_path") or ()

    with st.container(border=True):
        top, right = st.columns([6, 1], vertical_alignment="center")
        with top:
            st.markdown(f"**{candidate['rank']}. {issuer}**")
            st.html(
                f'<span class="fs-meta">{period} \N{BULLET} {page_label} '
                f"\N{BULLET} {candidate.get('evidence_type', '')}</span>"
            )
        with right:
            rerank_score = candidate.get("rerank_score")
            if rerank_score is not None:
                st.metric(
                    "Rerank", f"{rerank_score:+.2f}", help="Cross-encoder logit, not a probability."
                )

        if heading:
            st.html(f'<div class="fs-crumb">{" / ".join(heading)}</div>')

        st.html(f'<div class="fs-passage">{candidate["text"]}</div>')

        contributions = candidate.get("contributions") or {}
        found_by = ", ".join(
            f"{name} \N{RIGHTWARDS ARROW} rank {rank}"
            for name, rank in sorted(contributions.items())
        )
        st.caption(
            f"Fused {candidate['fused_score']:.4f}"
            + (f" \N{BULLET} found by {found_by}" if found_by else "")
        )

        citations = candidate.get("citations") or ()
        label = f"Source regions ({len(citations)})" if citations else "No source regions recorded"
        with st.expander(label, icon=":material/description:"):
            if not citations:
                st.caption(
                    "This passage resolved to no source elements, so it cannot be used as evidence."
                )
            for citation in citations:
                st.html(
                    f'<div><span class="fs-id">{citation["source_element_id"]}</span>'
                    f' \N{BULLET} <span class="fs-meta">{citation["locator"]}</span></div>'
                )
