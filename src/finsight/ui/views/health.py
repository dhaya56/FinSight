"""System: is the deployment healthy, is the index consistent, and what has it answered.

Three questions, and the third is the one that is not usually on a status page.

**Dependency health** is classified by what loss costs. An *essential* dependency failing
takes the service out of readiness; a *degradable* one does not, because losing it has a
defined fallback — without Qdrant, retrieval falls back to PostgreSQL full-text search and
says so on every result.

**Index consistency** is the check that makes §29.2 mean anything. Qdrant is a derived,
rebuildable index, which is reassuring only if someone verifies it still agrees with the
authority. A silent drift between the two is exactly what the transactional outbox exists to
prevent, so the comparison is shown rather than assumed.

**Answer activity** is the part a reader rather than an operator cares about. The audit
record holds every decision and its reasons, and how often the system *declines* — and why —
is the question worth asking before trusting anything it says. A system that always answers
is not more trustworthy than one that sometimes refuses; it is less, because the refusals are
where the grounding does its work.
"""

from typing import Any

import streamlit as st

from finsight.ui.client import ApiError, client_from_environment
from finsight.ui.theme import page_header

_DECISION_MEANING = {
    "answered": "Every claim survived the Evidence Gate.",
    "partial": "Some claims were released; others were removed as unsupported.",
    "abstained": "Nothing was released.",
}

_REASON_MEANING = {
    "no_evidence_retrieved": "Retrieval returned nothing to answer from.",
    "model_reported_unanswerable": "The passages did not answer the question.",
    "no_claim_survived_validation": "Every claim was removed by the Gate.",
    "unsupported_numeral": "A figure appeared in no span its claim cited.",
    "no_citation": "A claim rested on no resolvable source region.",
    "citation_not_in_evidence": "A claim cited a passage never supplied.",
    "cited_span_has_no_text": "A cited passage carries no checkable text.",
    "scale_not_in_cited_span": "A scale word its cited spans do not use.",
    "currency_not_in_cited_span": "A currency its cited spans do not use.",
    "mixed_issuer": "Cited passages came from more than one issuer.",
    "mixed_period": "Cited passages described more than one period.",
    "mixed_basis": "Cited passages mixed standalone and consolidated.",
}


def render() -> None:
    """Render the System page."""
    page_header(
        "System",
        "Whether the deployment is healthy, whether the derived index still agrees with "
        "the authority, and what the system has actually been answering.",
    )

    try:
        payload = client_from_environment().system()
    except ApiError as error:
        st.error(str(error), icon=":material/error:")
        return

    _dependencies(payload.get("dependencies") or ())
    st.container(height=14, border=False)
    _index(payload.get("index") or {})
    st.container(height=14, border=False)
    _answers(payload.get("answers") or {})


def _dependencies(dependencies: Any) -> None:
    """Each dependency, and what its loss would actually cost."""
    st.markdown("##### Dependencies")
    entries = list(dependencies)
    if not entries:
        st.info("No dependency detail was returned.", icon=":material/info:")
        return

    blocking = [
        d for d in entries if not d.get("healthy") and d.get("classification") == "essential"
    ]
    degraded = [
        d
        for d in entries
        if not d.get("healthy") and d.get("classification") == "degradable"
    ]

    if blocking:
        st.error(
            "**Not ready.** "
            + ", ".join(str(d.get("name")) for d in blocking)
            + " is essential and is not answering, so the service cannot serve correctly.",
            icon=":material/error:",
        )
    elif degraded:
        st.warning(
            "**Serving, with reduced capability.** "
            + ", ".join(str(d.get("name")) for d in degraded)
            + " is degradable: its loss has a defined fallback and every affected result "
            "carries a flag.",
            icon=":material/warning:",
        )
    else:
        st.success("Every dependency is answering.", icon=":material/check_circle:")

    columns = st.columns(len(entries))
    for column, dependency in zip(columns, entries, strict=True):
        healthy = bool(dependency.get("healthy"))
        css = "fs-stage fs-stage-done" if healthy else "fs-stage"
        with column:
            st.html(
                f'<div class="{css}">'
                f'<span class="fs-stage-label">{dependency.get("name")}</span>'
                f'{"healthy" if healthy else "not answering"}<br>'
                f'<span class="fs-stage-note">{dependency.get("classification")}</span>'
                "</div>"
            )


def _index(index: dict[str, Any]) -> None:
    """Whether the derived index still agrees with the authority."""
    st.markdown("##### Index consistency")

    chunks = int(index.get("indexed_chunks") or 0)
    context = int(index.get("context_chunks") or 0)
    points = index.get("index_points")
    consistent = index.get("index_consistent")

    if consistent is True:
        st.success(
            f"**PostgreSQL and Qdrant agree: {chunks:,} indexed passages.** The derived "
            "index matches the authority.",
            icon=":material/check_circle:",
        )
    elif consistent is False:
        st.error(
            f"**The index has drifted.** PostgreSQL holds {chunks:,} passages in active "
            f"generations and Qdrant holds {int(points or 0):,} points. Qdrant is "
            "rebuildable from PostgreSQL (§29.2), so the authority is intact — but a "
            "query may not see everything it should until the index is rebuilt.",
            icon=":material/error:",
        )
    else:
        st.warning(
            f"**Qdrant could not be asked**, so consistency is unknown rather than "
            f"broken. PostgreSQL holds {chunks:,} passages in active generations.",
            icon=":material/help:",
        )

    pending = int(index.get("pending_events") or 0)
    failed = int(index.get("failed_events") or 0)
    completed = int(index.get("completed_events") or 0)

    columns = st.columns(4)
    columns[0].metric(
        "Indexed passages",
        f"{chunks:,}",
        help=(
            f"Retrieval children. A further {context:,} parents are stored for context "
            "expansion and deliberately not indexed — they are fetched by "
            "identifier, never searched."
        ),
        border=True,
    )
    columns[1].metric(
        "Index points",
        f"{int(points):,}" if points is not None else "\N{EM DASH}",
        help="Null means Qdrant was unreachable, which is not the same as empty.",
        border=True,
    )
    columns[2].metric(
        "Indexing work pending",
        pending,
        help="Outbox events not yet processed (§29.8). Zero is the steady state.",
        border=True,
    )
    columns[3].metric(
        "Failed",
        failed,
        help=(
            "Work that will not happen without a retry: "
            "`python -m finsight.cli.main index <generation> --retry`."
        ),
        border=True,
    )
    if completed:
        st.caption(
            f"{completed:,} indexing events have completed. The outbox is what keeps the "
            "two stores in step: a chunk and its indexing event commit together, and the "
            "indexer runs outside that transaction (§29.7, §29.8)."
        )


def _answers(activity: dict[str, Any]) -> None:
    """What the system has been answering, and how often it declined."""
    st.markdown("##### Answer activity")

    total = int(activity.get("total") or 0)
    if not total:
        st.info(
            "No answers recorded yet. Every answer is written to the audit record with "
            "its decision and reasons, and this reports on them.",
            icon=":material/inbox:",
        )
        return

    decisions: dict[str, int] = activity.get("by_decision") or {}
    answered = int(decisions.get("answered", 0))
    partial = int(decisions.get("partial", 0))
    abstained = int(decisions.get("abstained", 0))
    released = answered + partial

    columns = st.columns(4)
    columns[0].metric("Questions answered", f"{total:,}", border=True)
    columns[1].metric(
        "Released something",
        f"{released / total * 100:.0f}%",
        help=f"{released:,} of {total:,} put at least one cited claim before a reader.",
        border=True,
    )
    columns[2].metric(
        "Declined",
        f"{abstained / total * 100:.0f}%",
        help=(
            "Abstentions. Declining is a correct outcome, not a failure — it is where "
            "the grounding does its work."
        ),
        border=True,
    )
    columns[3].metric(
        "Median time",
        f"{int(activity.get('median_elapsed_ms') or 0) / 1000:.0f} s",
        help=(
            f"Slowest {int(activity.get('slowest_elapsed_ms') or 0) / 1000:.0f} s. "
            "A median rather than a mean: one long answer drags an average somewhere no "
            "request actually was."
        ),
        border=True,
    )

    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown("**Outcomes**")
        st.bar_chart(
            [
                {"Decision": name, "Answers": count}
                for name, count in decisions.items()
            ],
            x="Decision",
            y="Answers",
            horizontal=True,
            height=170,
            color="#4f46e5",
        )
        st.caption(
            " \N{BULLET} ".join(
                f"**{name}** {_DECISION_MEANING.get(name, '')}" for name in decisions
            )
        )

    with right:
        st.markdown("**Why content was withheld or declined**")
        reasons: dict[str, int] = activity.get("by_reason") or {}
        if not reasons:
            st.caption(
                "No reason codes recorded \N{EM DASH} every answer so far was released "
                "whole, with nothing removed by the Evidence Gate."
            )
        else:
            st.dataframe(
                [
                    {
                        "Reason": name,
                        "Answers": count,
                        "What it means": _REASON_MEANING.get(name, ""),
                    }
                    for name, count in reasons.items()
                ],
                hide_index=True,
                width="stretch",
            )

    bands: dict[str, int] = activity.get("by_support_band") or {}
    if bands:
        st.caption(
            "Support bands: "
            + ", ".join(f"{name} {count}" for name, count in bands.items())
            + ". A band is a rule over what survived \N{EM DASH} whether anything was "
            "removed, whether a conflict was disclosed, whether retrieval or the model "
            "was degraded. It is **not** a probability that the answer is correct "
            "(§27.13)."
        )
