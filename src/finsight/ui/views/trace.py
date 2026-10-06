"""Query trace: why each passage is where it is.

Almost all of this is real. The retrieval response carries, per candidate, the rank each
retriever gave it, the fused score, and the reranker's logit — which is enough to show
the reordering at every stage rather than asserting that it happened.

What is not yet real is the per-stage timing breakdown: the API reports one wall time for
the whole pipeline, so the split across stages is a fixture and is labelled as one.
Persisting the trace to PostgreSQL (§20.13, §31.9) is also still to come; this reads the
response in the browser session rather than a stored record.
"""

from typing import Any

import streamlit as st

from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge
from finsight.ui.views.ask import RESULT_KEY


def render() -> None:
    """Render the Query trace page."""
    page_header(
        "Query trace",
        "How a question became a ranked list: which retriever found what, how fusion "
        "combined them, and what reranking changed.",
        Wiring.PARTIAL,
    )

    result = st.session_state.get(RESULT_KEY)
    if not result:
        st.info(
            "No query in this session yet. Ask something on the Ask page and the trace "
            "for it appears here.",
            icon=":material/info:",
        )
        return

    st.markdown(f"**Question** \N{EM DASH} {result.get('query', '')}")

    _pipeline_strip(result)
    st.container(height=10, border=False)

    left, right = st.columns([3, 2], gap="medium")
    with left:
        _reordering_panel(result)
    with right:
        _retriever_panel(result)

    _timing_panel(result)


def _pipeline_strip(result: dict[str, Any]) -> None:
    """The stages the query passed through, and whether each ran."""
    dense_used = bool(result.get("dense_used"))
    reranked = bool(result.get("reranked"))
    depth = result.get("depth", 0)
    count = len(result.get("candidates", ()))

    stages = [
        ("Filters", "enforced", "active-generation bound + any scope filters"),
        ("Lexical", result.get("lexical_retriever", "\N{EM DASH}"), f"depth {depth}"),
        ("Dense", "ran" if dense_used else "unavailable", f"depth {depth}"),
        ("Fusion", f"RRF v{result.get('fusion_version', '?')}", "reciprocal rank"),
        ("Rerank", "ran" if reranked else "skipped", result.get("reranker_model") or "\N{EM DASH}"),
        ("Returned", str(count), "after deduplication"),
    ]
    columns = st.columns(len(stages))
    for column, (label, value, note) in zip(columns, stages, strict=True):
        ran = value not in ("unavailable", "skipped")
        css = "fs-stage fs-stage-done" if ran else "fs-stage"
        with column:
            st.html(
                f'<div class="{css}"><span class="fs-stage-label">{label}</span>'
                f'{value}<br><span class="fs-stage-note">{note}</span></div>'
            )


def _reordering_panel(result: dict[str, Any]) -> None:
    """Fused rank against final rank, which is what reranking actually did."""
    header, badge = st.columns([4, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Reordering")
    with badge:
        state_badge(Wiring.LIVE)

    rows = []
    for candidate in result.get("candidates", ()):
        contributions: dict[str, int] = candidate.get("contributions") or {}
        rows.append(
            {
                "Final": candidate["rank"],
                "BM25": contributions.get("bm25"),
                "Dense": contributions.get("dense"),
                "Fused": round(candidate["fused_score"], 5),
                "Rerank": candidate.get("rerank_score"),
                "Passage": (candidate["text"][:60] + "\N{HORIZONTAL ELLIPSIS}").replace("\n", " "),
            }
        )
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Final": st.column_config.NumberColumn("Final", width="small"),
            "BM25": st.column_config.NumberColumn(
                "BM25 rank", help="Blank means BM25 did not return it."
            ),
            "Dense": st.column_config.NumberColumn(
                "Dense rank", help="Blank means dense did not return it."
            ),
            "Fused": st.column_config.NumberColumn("Fused", format="%.5f"),
            "Rerank": st.column_config.NumberColumn("Rerank logit", format="%+.2f"),
        },
    )
    panel_caption(
        Wiring.LIVE,
        "A blank retriever column is informative: it means only one side found that "
        "passage, and fusion still promoted it.",
    )


def _retriever_panel(result: dict[str, Any]) -> None:
    """How much each retriever contributed, and where they agreed."""
    header, badge = st.columns([4, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Agreement")
    with badge:
        state_badge(Wiring.LIVE)

    candidates = result.get("candidates", ())
    both = sum(1 for c in candidates if len(c.get("contributions") or {}) > 1)
    lexical_only = sum(
        1 for c in candidates if list((c.get("contributions") or {}).keys()) == ["bm25"]
    )
    dense_only = sum(
        1 for c in candidates if list((c.get("contributions") or {}).keys()) == ["dense"]
    )

    st.metric(
        "Found by both retrievers",
        both,
        help="Consensus, which is what fusion rewards.",
        border=True,
    )
    with st.container(horizontal=True, gap="small"):
        st.metric("Lexical only", lexical_only, border=True)
        st.metric("Dense only", dense_only, border=True)

    if candidates:
        st.bar_chart(
            [
                {"source": "Both", "count": both},
                {"source": "Lexical only", "count": lexical_only},
                {"source": "Dense only", "count": dense_only},
            ],
            x="source",
            y="count",
            height=170,
            color="#6366f1",
        )

    for flag in result.get("degraded", ()):
        st.warning(f"Degraded: {flag}", icon=":material/warning:")

    panel_caption(Wiring.LIVE)


def _timing_panel(result: dict[str, Any]) -> None:
    """Per-stage latency. The total is live; the split is not yet instrumented."""
    total = result.get("elapsed_ms", 0)
    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Latency")
    with badge:
        state_badge(Wiring.PARTIAL)

    reranked = bool(result.get("reranked"))
    # Proportions from the measurements recorded in ENV-010: reranking dominates a
    # reranked query at roughly 93% of wall time. Applied to this query's real total.
    shares = (
        {"Lexical": 0.03, "Dense": 0.03, "Fusion": 0.005, "Rerank": 0.93, "Resolve": 0.005}
        if reranked
        else {"Lexical": 0.42, "Dense": 0.40, "Fusion": 0.06, "Resolve": 0.12}
    )
    rows = [{"stage": stage, "ms": round(total * share)} for stage, share in shares.items()]

    columns = st.columns([2, 3])
    with columns[0]:
        st.metric("Total, measured", f"{total} ms", border=True)
        st.caption(
            "Reranking is roughly 93% of a reranked query on this host "
            "\N{EM DASH} cost scales with passage length, not candidate count."
        )
    with columns[1]:
        st.bar_chart(rows, x="stage", y="ms", height=200, color="#8b5cf6")

    panel_caption(
        Wiring.PARTIAL,
        "The total is measured per request. The split across stages is apportioned from "
        "recorded ENV-010 ratios, not instrumented per stage yet.",
    )
