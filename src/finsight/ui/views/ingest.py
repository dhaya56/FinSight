"""Ingest: bring a filing in, and watch it become queryable.

The pipeline shown here is real and runs today on the command line. What is missing is
the HTTP route and the background worker: indexing a large filing takes about 40 minutes
at the measured throughput, which is far longer than a request may hold, so it belongs to
a worker reporting progress rather than to a synchronous upload.

Preview. Nothing uploaded on this page is stored, parsed or indexed; the staged run is a
simulation over recorded stage timings.
"""

import streamlit as st

from finsight.ui import demo
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge

_RUN_KEY = "ingest.completed"

_CONTROLS = (
    (
        "Signature and declared type",
        "A PDF that is not a PDF is refused before any parser sees it.",
    ),
    (
        "Structural limits",
        "Page count, nesting depth, stream sizes and decompression ratios bounded.",
    ),
    ("Archive and workbook safety", "Zip paths and spreadsheet formulas treated as hostile."),
    ("Safe XML", "External entities and DTDs disabled; no network fetch during parse."),
    (
        "Parser isolation",
        "Non-root worker, no outbound network, bounded CPU, memory, disk and time.",
    ),
    ("Sanitisation", "HTML and XML content sanitised before it reaches any representation."),
)


def render() -> None:
    """Render the Ingest page."""
    page_header(
        "Ingest",
        "Add a filing to the corpus. Every document is treated as untrusted until it has "
        "passed validation and been parsed in isolation.",
        Wiring.PREVIEW,
    )

    left, right = st.columns([3, 2], gap="large")

    with left:
        with st.container(border=True):
            st.markdown("##### Upload")
            st.file_uploader(
                "Filing",
                type=["pdf", "xlsx", "xls", "htm", "html", "xml"],
                accept_multiple_files=True,
                label_visibility="collapsed",
                help="Not wired: files selected here are not stored, parsed or indexed.",
            )
            meta = st.columns(2)
            with meta[0]:
                st.selectbox("Issuer", options=demo.DEMO_ISSUERS, index=0)
                st.selectbox(
                    "Document type",
                    options=["Annual report", "Quarterly results", "Offer document"],
                )
            with meta[1]:
                st.selectbox("Reporting basis", options=["Consolidated", "Standalone", "Both"])
                st.selectbox(
                    "Split",
                    options=["development", "held-out"],
                    help=(
                        "Assigned before any golden-set question is written (§8). A document "
                        "cannot be moved between splits afterwards."
                    ),
                )
            st.container(height=6, border=False)
            if st.button("Run pipeline", type="primary", icon=":material/play_arrow:"):
                _simulate()

        if st.session_state.get(_RUN_KEY):
            _result_panel()

    with right, st.container(border=True):
        header, badge = st.columns([4, 1], vertical_alignment="center")
        with header:
            st.markdown("##### Document safety")
        with badge:
            state_badge(Wiring.PREVIEW)
        st.caption(
            "Every filing is untrusted input. These controls are specified and partly "
            "implemented; the panel reports the intended set, not a live scan result."
        )
        for name, note in _CONTROLS:
            with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                st.badge("", icon=":material/shield:", color="blue")
                st.markdown(f"**{name}**")
            st.caption(note)
        panel_caption(Wiring.PREVIEW)


def _simulate() -> None:
    """Walk the recorded stage timings, so the shape of a real run is visible.

    Deliberately not sped up to look fast and not slowed down to look busy: each stage's
    label carries the duration actually recorded for it, and the simulation itself returns
    immediately. Faking a forty-minute wait would teach the reader nothing.
    """
    stages = demo.ingest_stages()
    progress = st.progress(0.0, text="Starting\N{HORIZONTAL ELLIPSIS}")
    for index, stage in enumerate(stages, start=1):
        with st.status(f"{stage.label} \N{EM DASH} {stage.produces}", state="complete"):
            st.caption(stage.detail)
            st.caption(f"Recorded duration: {_duration(stage.seconds)}")
        progress.progress(index / len(stages), text=f"{stage.label} complete")
    progress.empty()
    st.session_state[_RUN_KEY] = True


def _duration(seconds: float) -> str:
    """Render a duration the way an operator reads one."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{seconds / 60:.1f} min"


def _result_panel() -> None:
    """What a completed run produced."""
    stages = demo.ingest_stages()
    total = sum(stage.seconds for stage in stages)
    st.container(height=8, border=False)
    with st.container(border=True):
        header, badge = st.columns([5, 1], vertical_alignment="center")
        with header:
            st.markdown("##### Run complete")
        with badge:
            state_badge(Wiring.PREVIEW)
        columns = st.columns(4)
        columns[0].metric("Wall time", _duration(total), border=True)
        columns[1].metric("Source elements", "4,118", border=True)
        columns[2].metric("Passages indexed", "2,841", border=True)
        columns[3].metric("Generation", "active", border=True)
        st.caption(
            "Indexing dominates at about 24 minutes of the total, which is why this "
            "belongs to a background worker rather than a request. Measured throughput on "
            "this host is 1.93 passages per second."
        )
        panel_caption(
            Wiring.PREVIEW,
            "Simulated from recorded stage timings. No document was stored or parsed.",
        )
