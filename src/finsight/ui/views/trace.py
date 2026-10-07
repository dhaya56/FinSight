"""Query trace: why each passage is where it is.

**The question this page answers is the one an analyst actually has about a retrieval
system: why that passage, and why in that order.** The response carries, per candidate, the
rank each retriever gave it, the fused score and the reranker's logit — enough to show the
reordering at every stage rather than asserting that it happened.

**Live, including the latency split.** It previously apportioned one measured total across
stages using ratios recorded in ENV-010. Those ratios were real on the query they were taken
from and a guess on every other, so a reader saw a fabricated number beside a measured one
with nothing to tell them apart. The stages are now timed per call.

Still not persisted (§20.13, §31.9): this reads the response held in the browser session, not
a stored trace record. A trace that vanishes when the tab closes is not an audit trail, and
the page says so rather than implying otherwise.
"""

from typing import Any

import streamlit as st

from finsight.ui.theme import page_header
from finsight.ui.views.ask import RESULT_KEY

_STAGE_ORDER = ("lexical", "dense", "fusion", "resolve", "rerank")

_STAGE_MEANING = {
    "lexical": "BM25 over Qdrant sparse vectors, run once per evidence type.",
    "dense": "Dense vectors over Qdrant, run once per evidence type.",
    "fusion": "Reciprocal rank fusion. Arithmetic over ranks, so it is nearly free.",
    "resolve": "Reading chunk text and citations back from PostgreSQL.",
    "rerank": "Cross-encoder scoring. Cost scales with passage length, not count.",
}


def render() -> None:
    """Render the Query trace page."""
    page_header(
        "Query trace",
        "How a question became a ranked list: which retriever found what, how fusion "
        "combined them, and what reranking changed.",
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
    _funnel(result)
    st.container(height=10, border=False)
    _pipeline_strip(result)
    st.container(height=10, border=False)

    _reordering(result)
    st.container(height=10, border=False)

    left, right = st.columns([3, 2], gap="medium")
    with left:
        _latency(result)
    with right:
        _agreement(result)


def _funnel(result: dict[str, Any]) -> None:
    """How many passages were considered, and how many a reader ever sees.

    **This is the number the page was missing.** Everything else here explains the five
    results that came back; none of it said that twenty-five were examined to produce
    them. For an analyst deciding whether to trust a short answer, "was anything else
    found?" is the first question, and the ratio between these two figures is the answer.
    """
    returned = len(result.get("candidates", ()))
    depth = int(result.get("depth", 0))
    collapsed = int(result.get("collapsed_count", 0))
    dropped = max(depth - returned - collapsed, 0)

    columns = st.columns(4)
    columns[0].metric(
        "Considered",
        depth,
        help=(
            "Candidates retrieved before narrowing. Both retrievers search to this depth "
            "so that a passage ranked low by one still reaches the cross-encoder."
        ),
        border=True,
    )
    columns[1].metric(
        "Collapsed as duplicates",
        collapsed,
        help=(
            "Candidates built from the same source regions (§20.9). The same evidence "
            "retrieved twice is still one piece of evidence."
        ),
        border=True,
    )
    columns[2].metric(
        "Ranked below the cut",
        dropped,
        help="Considered, scored, and not returned because the limit was reached.",
        border=True,
    )
    columns[3].metric(
        "Shown",
        returned,
        help="What reached the reader, and what an answer may rest on.",
        border=True,
    )

    if depth:
        st.caption(
            f"**{returned} of {depth} considered passages reached the reader.** The rest "
            "were scored and set aside, not missed \N{EM DASH} widening the limit in the "
            "sidebar admits more of them without re-running retrieval differently."
        )


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
        (
            "Rerank",
            "ran" if reranked else "skipped",
            result.get("reranker_model") or "\N{EM DASH}",
        ),
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


def _reordering(result: dict[str, Any]) -> None:
    """What reranking changed, as movement rather than as two columns of numbers.

    **The movement is the finding.** A table of "fused rank" beside "final rank" is read as
    two lists; the same data as a signed change says at a glance whether the cross-encoder
    agreed with fusion or overruled it — which is the question worth asking of a stage that
    costs most of the query's latency.
    """
    candidates = list(result.get("candidates", ()))
    if not candidates:
        st.info("Nothing matched this query.", icon=":material/info:")
        return

    st.markdown("##### Reordering")
    reranked = bool(result.get("reranked"))
    rows = []
    for position, candidate in enumerate(candidates, start=1):
        contributions: dict[str, int] = candidate.get("contributions") or {}
        fused_rank = min(contributions.values()) if contributions else None
        rows.append(
            {
                "Final": candidate["rank"],
                "Moved": (fused_rank - position) if fused_rank is not None else 0,
                "BM25": contributions.get("bm25"),
                "Dense": contributions.get("dense"),
                "Fused": round(candidate["fused_score"], 5),
                "Rerank": candidate.get("rerank_score"),
                "Passage": " ".join(str(candidate["text"]).split())[:90]
                + "\N{HORIZONTAL ELLIPSIS}",
            }
        )

    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Final": st.column_config.NumberColumn("Final", width="small"),
            "Moved": st.column_config.NumberColumn(
                "Moved",
                help=(
                    "Places gained against the best rank any single retriever gave this "
                    "passage. Positive means fusion and reranking promoted it; negative "
                    "means they pushed it down."
                ),
                format="%+d",
            ),
            "BM25": st.column_config.NumberColumn(
                "BM25 rank", help="Blank means BM25 did not return it."
            ),
            "Dense": st.column_config.NumberColumn(
                "Dense rank", help="Blank means dense did not return it."
            ),
            "Fused": st.column_config.NumberColumn("Fused", format="%.5f"),
            "Rerank": st.column_config.NumberColumn(
                "Rerank logit",
                help=(
                    "A cross-encoder logit. Unbounded, frequently negative, and **not** a "
                    "probability that the passage is correct (§27.13)."
                ),
                format="%+.2f",
            ),
        },
    )

    if reranked:
        st.bar_chart(
            [{"Passage": f"[{row['Final']}]", "Rerank logit": row["Rerank"]} for row in rows],
            x="Passage",
            y="Rerank logit",
            height=190,
            color="#4f46e5",
        )
        st.caption(
            "The cross-encoder's score for each returned passage, in final order. A "
            "descending bar means reranking and the final order agree; a bar out of "
            "sequence is a passage deduplication moved."
        )
    st.caption("A blank retriever column is informative: it means only one side found that "
        "passage, and fusion still promoted it.",
    )


def _agreement(result: dict[str, Any]) -> None:
    """Where the two retrievers agreed, which is what fusion rewards."""
    st.markdown("##### Retriever agreement")
    candidates = result.get("candidates", ())
    both = sum(1 for c in candidates if len(c.get("contributions") or {}) > 1)
    lexical_only = sum(
        1 for c in candidates if list((c.get("contributions") or {}).keys()) == ["bm25"]
    )
    dense_only = sum(
        1 for c in candidates if list((c.get("contributions") or {}).keys()) == ["dense"]
    )

    st.metric(
        "Found by both",
        f"{both} of {len(candidates)}",
        help=(
            "Consensus is what reciprocal rank fusion rewards: a passage both retrievers "
            "found beats one either ranked first alone."
        ),
        border=True,
    )
    with st.container(horizontal=True, gap="small"):
        st.metric(
            "Wording only",
            lexical_only,
            help="BM25 found it; the dense vector did not. Usually exact terminology.",
            border=True,
        )
        st.metric(
            "Meaning only",
            dense_only,
            help="The dense vector found it; BM25 did not. Usually paraphrase.",
            border=True,
        )

    for flag in result.get("degraded", ()):
        st.warning(f"Degraded: {flag}", icon=":material/warning:")


def _latency(result: dict[str, Any]) -> None:
    """Where the time went, measured per stage."""
    timings: dict[str, int] = result.get("timings_ms") or {}
    st.markdown("##### Where the time went")
    total = int(result.get("elapsed_ms", 0))
    if not timings:
        st.metric("Total", f"{total} ms", border=True)
        st.caption("This response carries no per-stage timings; only the total is available.",
        )
        return

    measured: list[tuple[str, int]] = [
        (stage, int(timings[stage])) for stage in _STAGE_ORDER if stage in timings
    ]
    if not measured:
        return

    # **Not a bar chart.** One stage takes 90% of the query and another takes 28 ms, so a
    # shared linear axis renders every stage but the dominant one as no bar at all — which
    # reads as "that stage did not run" rather than "that stage was fast". A per-row bar
    # keeps its own scale and carries the number beside it, so a 1% stage is still visible
    # and still legible.
    st.dataframe(
        [
            {
                "Stage": stage,
                "Share": (value / total) if total else 0.0,
                "ms": value,
                "What it is": _STAGE_MEANING.get(stage, ""),
            }
            for stage, value in measured
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "Share": st.column_config.ProgressColumn(
                "Share of query", min_value=0.0, max_value=1.0, format="%.1f%%"
            ),
            "ms": st.column_config.NumberColumn("Measured", format="%d ms"),
        },
    )

    stage, value = max(measured, key=lambda pair: pair[1])
    if total:
        st.caption(
            f"**{stage} is {value / total * 100:.0f}% of this query** "
            f"({value} ms of {total} ms). Measured per stage on this request, not "
            "apportioned from recorded ratios."
        )
