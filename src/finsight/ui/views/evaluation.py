"""Evaluation: how a change earns its way into production.

**Nothing is admitted on intuition.** A change to the parser, the chunker, the embedding
model, the reranker or the fusion constant is a versioned experiment with a recorded
baseline, a fixed evaluation set, a sample size and a decision — including when the
decision is "no change", which is why the negative results are listed rather than dropped.

Two rules shape this surface. Tuning happens only on the development split; the held-out
split is never opened to choose anything. And a threshold gate stays informational until an
approved baseline gives it a number, because a gate set from a guess fails honest work and
passes bad work.

Preview. The golden set and the experiment runner are specified and not yet built.
"""

import streamlit as st

from finsight.ui import demo
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge


def render() -> None:
    """Render the Evaluation page."""
    page_header(
        "Evaluation",
        "The golden question set, recorded experiments, and the evidence a change needs "
        "before it reaches production.",
        Wiring.PREVIEW,
    )

    summary = demo.evaluation_summary()
    _summary_strip(summary)
    st.container(height=12, border=False)

    tabs = st.tabs(["Experiments", "Golden set", "Trend"])
    with tabs[0]:
        _experiments_tab()
    with tabs[1]:
        _golden_set_tab(summary)
    with tabs[2]:
        _trend_tab(summary)


def _summary_strip(summary: demo.EvaluationSummary) -> None:
    """Headline evaluation figures."""
    columns = st.columns(5)
    columns[0].metric("Questions", summary.questions, border=True)
    columns[1].metric(
        "recall@10",
        f"{summary.recall_at_10:.3f}",
        delta="+0.111 vs first baseline",
        border=True,
        chart_data=list(summary.recall_trend),
        chart_type="line",
    )
    columns[2].metric(
        "Citation resolution",
        f"{summary.citation_resolution:.0%}",
        help="Share of citations that resolve to a live source region.",
        border=True,
    )
    columns[3].metric(
        "Refusal precision",
        f"{summary.refusal_precision:.0%}",
        help="Of the questions refused, the share that were genuinely unanswerable.",
        border=True,
    )
    columns[4].metric(
        "Median latency",
        "2.1 s",
        delta="-1.0 s",
        delta_color="inverse",
        border=True,
        chart_data=list(summary.latency_trend),
        chart_type="area",
    )
    st.caption(
        "Fixture figures. No golden set exists yet, so retrieval quality is currently "
        "unmeasured \N{EM DASH} the honest state, and the reason this page is a preview."
    )


def _experiments_tab() -> None:
    """The experiment register."""
    experiments = demo.demo_experiments()

    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown(f"##### Recorded experiments ({len(experiments)})")
    with badge:
        state_badge(Wiring.PREVIEW)

    rows = [
        {
            "ID": e.identifier,
            "Changed factor": e.changed_factor,
            "Baseline": e.baseline,
            "Candidate": e.candidate,
            "Metric": e.metric,
            "Before": e.baseline_score,
            "After": e.candidate_score,
            "Delta": e.delta,
            "n": e.sample,
            "Decision": e.decision,
            "Recorded": e.recorded,
        }
        for e in experiments
    ]
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Before": st.column_config.NumberColumn(format="%.3f"),
            "After": st.column_config.NumberColumn(format="%.3f"),
            "Delta": st.column_config.NumberColumn("Delta", format="%+.3f"),
            "Decision": st.column_config.TextColumn("Decision", width="medium"),
            "Recorded": st.column_config.DateColumn(format="DD MMM YYYY"),
        },
    )

    st.container(height=8, border=False)
    with st.container(border=True):
        st.markdown("**Admission requires more than a better number**")
        for line in (
            "A recorded baseline, the changed factor isolated, and the corpus and split named.",
            "A sample large enough that the delta is not noise \N{EM DASH} EXP-003's "
            "+0.007 was not.",
            "Resource cost alongside quality: a model that wins on recall and triples "
            "latency may still lose.",
            "Approval by a person. The infrastructure may be built here; the winner is "
            "not picked here.",
            "Negative results kept. EXP-002 made things worse and is retained so it is "
            "not retried.",
        ):
            st.markdown(f"- {line}")
    panel_caption(Wiring.PREVIEW)


def _golden_set_tab(summary: demo.EvaluationSummary) -> None:
    """Composition of the evaluation set, including the negative probes."""
    st.caption(
        "A golden set that only asks answerable questions measures recall and nothing "
        "else. The unanswerable probes are what measure whether the system refuses."
    )
    columns = st.columns([2, 3], gap="medium")
    with columns[0]:
        st.bar_chart(
            [
                {"kind": "Answerable", "count": summary.answerable},
                {"kind": "Unanswerable probes", "count": summary.unanswerable_probes},
            ],
            x="kind",
            y="count",
            height=220,
            color="#6366f1",
        )
    with columns[1]:
        for kind, note in (
            ("Factual lookup", "A figure or statement present in one filing."),
            ("Cross-period", "The same concept across two years of one issuer."),
            ("Cross-issuer", "The same concept across two issuers, basis held constant."),
            ("Narrative", "What the filing says, where no single number answers it."),
            ("Unanswerable", "Plausible questions the corpus genuinely cannot answer."),
            ("Adversarial", "Prompt-injection attempts embedded in document text."),
        ):
            with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                st.badge("", icon=":material/quiz:", color="violet")
                st.markdown(f"**{kind}** \N{EM DASH} {note}")

    st.container(height=8, border=False)
    st.warning(
        "Held-out split: listed, never opened. Answers from it are not available for "
        "tuning, and a single look would spend it permanently.",
        icon=":material/lock:",
    )
    panel_caption(Wiring.PREVIEW)


def _trend_tab(summary: demo.EvaluationSummary) -> None:
    """Quality and cost over successive experiments."""
    st.bar_chart(
        [
            {"run": f"EXP-{index:03d}", "recall@10": value}
            for index, value in enumerate(summary.recall_trend, start=1)
        ],
        x="run",
        y="recall@10",
        height=260,
        color="#22c55e",
    )
    st.caption(
        "Quality is only half of a decision. A change that lifts recall and makes a query "
        "twice as slow is a trade-off to record, not a win to ship."
    )
    st.area_chart(
        [
            {"run": f"EXP-{index:03d}", "median seconds": value}
            for index, value in enumerate(summary.latency_trend, start=1)
        ],
        x="run",
        y="median seconds",
        height=220,
        color="#f59e0b",
    )
    panel_caption(Wiring.PREVIEW)
