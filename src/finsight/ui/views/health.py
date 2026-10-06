"""System: dependency health, index state, and the deployment topology.

The readiness probe here is live and unauthenticated, which is deliberate: it answers even
when the token is wrong, so "the server is down" stays distinguishable from "the credential
is wrong". Per-dependency detail is a preview — §28.10 keeps that behind authentication and
no route exposes it yet.

The classification matters more than the colour. An essential dependency failing takes the
service out of readiness; a degradable one does not, because losing it has a defined
fallback. Qdrant is degradable: without it retrieval falls back to PostgreSQL full-text
search and says so.
"""

import streamlit as st

from finsight.ui.client import ApiClient, ApiError, client_from_environment
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge

_DEPENDENCIES = (
    ("PostgreSQL", "essential", "Authoritative for every representation and all state."),
    ("Schema", "essential", "Migrations current; a stale schema is a refusal, not a warning."),
    ("Qdrant", "degradable", "Derived vector index. Losing it costs dense retrieval only."),
    ("Object storage", "essential", "Immutable originals, reached only via the S3 adapter."),
    (
        "Ollama",
        "degradable",
        "Host-native. Embeddings for queries; losing it costs dense retrieval.",
    ),
    (
        "Cross-encoder",
        "degradable",
        "Local reranker. Losing it returns the fused order with a flag.",
    ),
)


def render() -> None:
    """Render the System page."""
    page_header(
        "System",
        "Dependency health, index state and the single-node topology this runs on.",
        Wiring.PARTIAL,
    )

    try:
        client = client_from_environment()
    except ApiError as error:
        st.error(str(error))
        return

    _readiness(client)
    st.container(height=12, border=False)

    left, right = st.columns([3, 2], gap="medium")
    with left:
        _dependencies_panel()
    with right:
        _index_panel()

    _topology_panel()


def _readiness(client: ApiClient) -> None:
    """The live readiness probe."""
    ready = client.is_ready()
    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Readiness")
    with badge:
        state_badge(Wiring.LIVE)

    columns = st.columns(3)
    with columns[0]:
        st.metric(
            "API",
            "Ready" if ready else "Unreachable",
            help="Live, unauthenticated probe. 503 when an essential dependency is unhealthy.",
            border=True,
        )
    columns[1].metric("Address", client.base_url, border=True)
    columns[2].metric(
        "Authentication",
        "Bearer token",
        help="Required on every non-health route. Health stays open for orchestrators.",
        border=True,
    )
    if not ready:
        st.error(
            "The API did not report ready. Either it is not running, or an essential "
            "dependency is unhealthy.",
            icon=":material/error:",
        )


def _dependencies_panel() -> None:
    """Per-dependency classification."""
    header, badge = st.columns([4, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Dependencies")
    with badge:
        state_badge(Wiring.PREVIEW)

    for name, classification, note in _DEPENDENCIES:
        with st.container(border=True):
            with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                st.badge(
                    classification,
                    color="red" if classification == "essential" else "orange",
                    help=(
                        "Failing takes the service out of readiness."
                        if classification == "essential"
                        else "Failing degrades a capability with a defined fallback."
                    ),
                )
                st.markdown(f"**{name}**")
            st.caption(note)
    panel_caption(
        Wiring.PREVIEW,
        "Classifications are real and enforced in the readiness logic; per-dependency "
        "status is not exposed by any route yet (§28.10 keeps it behind authentication).",
    )


def _index_panel() -> None:
    """Index and storage figures measured on this host."""
    header, badge = st.columns([4, 1], vertical_alignment="center")
    with header:
        st.markdown("##### Index")
    with badge:
        state_badge(Wiring.PREVIEW)

    st.metric(
        "Indexed points", "4,969", help="One per passage, in a single collection.", border=True
    )
    with st.container(horizontal=True, gap="small"):
        st.metric("PostgreSQL", "127 MiB", border=True)
        st.metric("Qdrant", "81 MiB", border=True)
    st.caption(
        "Both vectors share one collection: 768-dimension dense with cosine distance, and "
        "BM25 sparse terms with inverse document frequency applied server-side. Lexemes "
        "cost more storage than the text they index."
    )

    st.bar_chart(
        [
            {"store": "Dense vectors", "MiB": 14.6},
            {"store": "Sparse terms", "MiB": 7.1},
            {"store": "Payload", "MiB": 5.9},
            {"store": "Text", "MiB": 5.4},
        ],
        x="store",
        y="MiB",
        height=200,
        color="#6366f1",
    )
    panel_caption(
        Wiring.PREVIEW,
        "Figures measured on this host during Phase 7 validation; not read live.",
    )


def _topology_panel() -> None:
    """What runs where, which is the thing a reviewer most often asks."""
    st.container(height=10, border=False)
    with st.container(border=True):
        header, badge = st.columns([5, 1], vertical_alignment="center")
        with header:
            st.markdown("##### Topology")
        with badge:
            state_badge(Wiring.PARTIAL)
        st.caption(
            "Single node, self-hosted, no third-party inference. Nothing leaves this "
            "machine: no filing, no question and no extracted figure is sent to a hosted "
            "model or an external service."
        )
        services = (
            ("Streamlit UI", "running", "API-only client. Holds no data-store credential."),
            ("FastAPI", "running", "Authenticated. Owns retrieval, planning and evidence."),
            ("PostgreSQL", "container", "Authoritative store, health-checked."),
            ("Qdrant", "container", "Derived index, rebuildable from PostgreSQL."),
            ("Object storage", "container", "Immutable originals via the S3 adapter."),
            ("Ollama", "host-native", "Embeddings and generation on the Windows host."),
            ("Parser worker", "planned", "Non-root, no network, bounded CPU, memory and time."),
            ("Processing worker", "planned", "Normalisation, embeddings, indexing, evaluation."),
        )
        columns = st.columns(4)
        for index, (name, state, note) in enumerate(services):
            with columns[index % 4]:
                css = (
                    "fs-stage fs-stage-done"
                    if state in ("running", "container", "host-native")
                    else "fs-stage"
                )
                st.html(
                    f'<div class="{css}"><span class="fs-stage-label">{name}</span>'
                    f'{state}<br><span class="fs-stage-note">{note}</span></div>'
                )
        st.container(height=6, border=False)
        panel_caption(
            Wiring.PARTIAL,
            "UI, API and the three containers are running now. The two workers are "
            "planned; their work currently runs synchronously from the command line.",
        )
