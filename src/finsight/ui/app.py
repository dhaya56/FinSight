"""FinSight's question surface: type a question, get the passages behind it.

**What this page shows is retrieved source text, not an answer.** No model composes
anything here, no number is restated, and no Evidence Gate runs — that is Phase 8.
The banner at the top of the page says so on every render rather than in
documentation nobody reads during a demonstration, because a page that returns
confident-looking prose invites being read as an answer, and §27 forbids presenting
retrieval output as a correctness judgement.

**Every result carries its provenance inline.** Issuer, period, section path, pages
and the source element ids behind the passage are rendered with the text rather than
hidden behind a detail view, because a citation that takes a click to find is a
citation nobody checks.

**Degradation is surfaced, never swallowed.** If the dense index or the reranker was
unreachable, the page says which and what it costs. A result set that silently
dropped half its retrievers looks exactly like a healthy one.
"""

from typing import Any, Final

import streamlit as st

from finsight.ui.client import ApiClient, ApiError, client_from_environment

PAGE_TITLE: Final = "FinSight"

# Plain-language consequence for each flag the API can return.
#
# The flag names are stable identifiers and mean nothing to a reader. Translating
# them here rather than renaming them keeps the wire contract machine-readable while
# the page stays legible.
#
# Written as comments, not as an attribute docstring: Streamlit's "magic" renders
# bare top-level string literals as page content, so a docstring here is published
# to the reader as a paragraph of internal commentary. Found by the page smoke test,
# which is the only thing that looks at what the page actually contains.
_FLAG_MEANINGS: Final = {
    "dense_unavailable": (
        "The vector index could not be reached. These results are lexical only, so "
        "a passage that matches in meaning but not in wording was not considered."
    ),
    "lexical_fallback_postgres_fts": (
        "BM25 was unreachable and PostgreSQL full-text search answered instead. It "
        "has no term-frequency saturation, no length normalisation and no IDF, so "
        "this ranking is not comparable with a BM25 one."
    ),
    "reranker_unavailable": (
        "The cross-encoder could not be loaded. The order below is the fusion order. "
        "The same passages were considered; only their ranking is less refined."
    ),
}


def main() -> None:
    """Render the page."""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="\N{LEFT-POINTING MAGNIFYING GLASS}",
        layout="wide",
    )
    st.title(PAGE_TITLE)
    st.caption(
        "Evidence retrieval over ingested filings. "
        "**This returns source passages, not answers** \N{EM DASH} nothing on this "
        "page is generated, summarised or calculated."
    )

    try:
        client = client_from_environment()
    except ApiError as error:
        st.error(str(error))
        st.stop()

    request = _sidebar(client)
    query = st.text_input(
        "Question",
        placeholder="What does the company say about credit risk?",
        help="Retrieval is lexical and semantic. Wording matters, but not exactly.",
    )
    if st.button("Search", type="primary", disabled=not query.strip()):
        _run(client, query.strip(), request)

    if "result" in st.session_state:
        _render(st.session_state["result"])


def _sidebar(client: ApiClient) -> dict[str, Any]:
    """Draw the controls and the §20.2 filters, returning the request parameters."""
    with st.sidebar:
        st.subheader("Server")
        if client.is_ready():
            st.success(f"API ready at {client.base_url}")
        else:
            st.warning(
                f"No readiness from {client.base_url}. The API may not be running, "
                f"or an essential dependency is down. Searching will say which."
            )

        st.subheader("Results")
        limit = st.slider("Passages to return", min_value=1, max_value=25, value=5)
        rerank = st.toggle(
            "Rerank with the cross-encoder",
            value=True,
            help=(
                "Measured on this host: about 2.3 s with reranking against 0.18 s "
                "without, so reranking is roughly 93% of the time. Whether it orders "
                "better is unmeasured \N{EM DASH} there is no golden question set yet."
            ),
        )

        st.subheader("Filters")
        st.caption(
            "Hard filters (\N{SECTION SIGN}20.2). These bound what may be returned "
            "and never affect ranking \N{EM DASH} similarity cannot override scope."
        )
        issuer_name = st.text_input("Issuer", placeholder="Exact name as recorded")
        document_type = st.text_input("Document type", placeholder="e.g. annual_report")
        fiscal_year = st.number_input(
            "Fiscal year",
            min_value=1990,
            max_value=2100,
            value=None,
            step=1,
            help="Leave empty for any year.",
        )
        evidence_type = st.selectbox(
            "Evidence type", options=["Any", "narrative", "table_derived"], index=0
        )
        section = st.text_input("Section", placeholder="Any")

    return {
        "limit": limit,
        "rerank": rerank,
        "filters": {
            "issuer_name": issuer_name,
            "document_type": document_type,
            "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
            "evidence_type": None if evidence_type == "Any" else evidence_type,
            "section": section,
        },
    }


def _run(client: ApiClient, query: str, request: dict[str, Any]) -> None:
    """Call the API, keeping the page usable when it fails."""
    with st.spinner("Retrieving\N{HORIZONTAL ELLIPSIS}"):
        try:
            st.session_state["result"] = client.search(
                query,
                limit=request["limit"],
                rerank=request["rerank"],
                filters=request["filters"],
            )
        except ApiError as error:
            st.session_state.pop("result", None)
            st.error(str(error))


def _render(result: dict[str, Any]) -> None:
    """Render one result set: provenance of the run, then the passages."""
    for flag in result.get("degraded", ()):
        st.warning(
            f"**Degraded \N{EM DASH} {flag}.** "
            f"{_FLAG_MEANINGS.get(flag, 'Consult the degradation flags in §20.12.')}"
        )

    candidates = result.get("candidates", ())
    if not candidates:
        st.info(
            "Nothing matched. The corpus holds only the filings that have been "
            "ingested and indexed, and tables are deliberately excluded from "
            "retrieval (ADR-003), so a figure that exists only in a table is not "
            "findable here."
        )
        return

    columns = st.columns(5)
    columns[0].metric("Passages", len(candidates))
    columns[1].metric("Server time", f"{result.get('elapsed_ms', 0) / 1000:.2f} s")
    columns[2].metric("Depth considered", result.get("depth", 0))
    columns[3].metric("Lexical path", result.get("lexical_retriever", "\N{EM DASH}"))
    columns[4].metric("Reranked", "yes" if result.get("reranked") else "no")

    st.caption(
        f"Dense retrieval {'used' if result.get('dense_used') else 'unavailable'} "
        f"\N{BULLET} fusion config v{result.get('fusion_version', '?')} "
        f"\N{BULLET} reranker {result.get('reranker_model') or 'not run'}"
        + (
            f" \N{BULLET} {result['collapsed_count']} candidate(s) collapsed as "
            f"duplicate evidence (\N{SECTION SIGN}20.9)"
            if result.get("collapsed_count")
            else ""
        )
    )
    st.divider()

    for candidate in candidates:
        _render_candidate(candidate)


def _render_candidate(candidate: dict[str, Any]) -> None:
    """Render one passage with its provenance and scores."""
    with st.container(border=True):
        issuer = candidate.get("issuer_name") or "Issuer not recorded"
        period = candidate.get("fiscal_period") or "period not recorded"
        pages = candidate.get("page_numbers") or []
        page_label = (
            f"p. {pages[0]}" if len(pages) == 1
            else f"pp. {pages[0]}\N{EN DASH}{pages[-1]}" if pages
            else "page not recorded"
        )

        st.markdown(
            f"**{candidate['rank']}. {issuer}** "
            f"\N{BULLET} {period} \N{BULLET} {page_label}"
        )

        heading_path = candidate.get("heading_path") or ()
        if heading_path:
            st.caption(" \N{RIGHTWARDS ARROW} ".join(heading_path))

        st.write(candidate["text"])

        rerank_score = candidate.get("rerank_score")
        scores = f"fused {candidate['fused_score']:.4f}"
        if rerank_score is not None:
            # Stated as a logit, not a confidence. §27: a support signal is not a
            # probability that the passage is correct, and this one is not bounded.
            scores += f" \N{BULLET} rerank logit {rerank_score:+.2f}"
        contributions = candidate.get("contributions") or {}
        if contributions:
            found_by = ", ".join(
                f"{name} at rank {rank}" for name, rank in sorted(contributions.items())
            )
            scores += f" \N{BULLET} found by {found_by}"
        st.caption(scores)

        citations = candidate.get("citations") or ()
        with st.expander(
            f"Source regions ({len(citations)})"
            if citations
            else "No source regions recorded"
        ):
            if not citations:
                st.caption(
                    "This passage resolved to no source elements, so it cannot be "
                    "used as evidence."
                )
            for citation in citations:
                st.caption(
                    f"`{citation['source_element_id']}` \N{BULLET} "
                    f"{citation['locator']}"
                )


if __name__ == "__main__":
    # Streamlit executes the entry script as ``__main__``, so this runs under
    # ``streamlit run`` while importing the module stays side-effect free — which is
    # what lets the import-boundary test examine it without rendering a page.
    main()
