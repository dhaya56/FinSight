"""Ask: evidence first, then the composed answer when it lands.

Everything on this page is real. Passages come from the indexed corpus through the
authenticated API; the answer above them is composed by a local model, its citations
resolved to stored source spans by code, and every numeral checked against a span its own
claim cites before release (ADR-009).

**Two requests, in that order, and the order is the point.** A composed answer takes two to
four minutes on this host — no GPU offload is available, so prompt evaluation runs at roughly
26 tokens per second. One blocking call would leave the page empty for all of it. Retrieval
answers in about two seconds, so the passages are drawn first into the lower slot while the
answer composes into the slot reserved above them. The reader has citable evidence in seconds
rather than a frozen page, and the arrangement states the architecture: evidence is primary,
prose is the convenience built on top of it.

The cost is one extra retrieval. Measured, that is about two seconds against the answer's two
hundred and ninety — the server caches the pipeline per process, so the second retrieval does
not reload the cross-encoder.

**Streaming the model's output is not available to us, and that is deliberate.** The Evidence
Gate must parse the complete response and verify every numeral before anything is released, so
streaming tokens would put unverified figures in front of a reader — the failure this whole
design exists to prevent. The wait is the cost of not doing that.
"""

from typing import Any

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from finsight.ui.client import ApiClient, ApiError, client_from_environment
from finsight.ui.theme import BadgeColour, Wiring, page_header, state_badge

RESULT_KEY = "ask.result"
ANSWER_KEY = "ask.answer"
QUERY_KEY = "ask.query"

_EXAMPLES = (
    "What does the company say about credit risk?",
    "How is revenue recognised?",
    "What are the principal risks to the business?",
    "What dividend was declared?",
)

_SPAN_PREVIEW = 420

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
    "generation_unavailable": (
        "The language model could not be reached, so no answer was composed. The "
        "passages below were still retrieved and are unaffected \N{EM DASH} they are the "
        "evidence an answer would have been built from."
    ),
    "generation_prompt_truncated": (
        "The evidence did not fit the model's context window, so it would have answered "
        "from only part of it while appearing to cite all of it. Refused rather than "
        "released. Reduce the passage count."
    ),
    "generation_contract_violated": (
        "The model returned a shape the response schema forbids, which means its output "
        "is not being constrained. A configuration fault, not a bad question."
    ),
}

# Plain-language meaning of each reason code the Evidence Gate can return. A reader
# told "unsupported_numeral" has been told nothing.
_REASON_MEANINGS = {
    "no_evidence_retrieved": (
        "Retrieval returned nothing, so there was nothing to answer from. The filters or "
        "the corpus are the place to look, not the question."
    ),
    "model_reported_unanswerable": (
        "The model read the passages and said they do not answer the question. That is a "
        "permitted and correct outcome, not a failure."
    ),
    "no_claim_survived_validation": (
        "Every claim the model produced was removed by the Evidence Gate, so nothing "
        "could be released."
    ),
    "unsupported_numeral": (
        "A figure appeared in a claim that appears in none of the spans that claim cites."
    ),
    "no_citation": "A claim rested on no resolvable source region.",
    "citation_not_in_evidence": (
        "A claim cited a passage that was never supplied \N{EM DASH} the reference was "
        "invented."
    ),
    "cited_span_has_no_text": (
        "A cited passage resolved, but its source regions carry no text, so nothing could "
        "be checked against it."
    ),
    "scale_not_in_cited_span": (
        "A claim stated a scale \N{EM DASH} crore, lakh, million \N{EM DASH} that none of "
        "its cited spans use. The same numeral under a different scale is a different "
        "amount."
    ),
    "currency_not_in_cited_span": (
        "A claim stated a currency that none of its cited spans use."
    ),
    "mixed_issuer": "The cited passages come from more than one issuer.",
    "mixed_period": "The cited passages describe more than one reporting period.",
    "mixed_basis": (
        "The cited passages mix standalone and consolidated reporting, which are not "
        "comparable unless the document compares them itself."
    ),
}

#: Badge colour and icon per decision. Annotated with Streamlit's own colour literal
#: rather than ``str``, so a typo is a type error instead of a runtime one.
_DECISION_STYLE: dict[str, tuple[BadgeColour, str]] = {
    "answered": ("green", ":material/check_circle:"),
    "partial": ("orange", ":material/rule:"),
    "abstained": ("grey", ":material/block:"),
}


def render() -> None:
    # Render the Ask page.
    page_header(
        "Ask",
        "Put a question to the ingested filings and read the evidence behind every claim.",
        Wiring.LIVE,
    )

    try:
        client = client_from_environment()
    except ApiError as error:
        st.error(str(error))
        return

    options = _controls()
    query = st.text_input(
        "Question",
        key=QUERY_KEY,
        placeholder="What does the company say about credit risk?",
        label_visibility="collapsed",
    )

    with st.container(horizontal=True, gap="small"):
        asked = st.button(
            "Ask", type="primary", icon=":material/send:", disabled=not query.strip()
        )
        if st.button("Clear", icon=":material/close:", disabled=RESULT_KEY not in st.session_state):
            st.session_state.pop(RESULT_KEY, None)
            st.session_state.pop(ANSWER_KEY, None)
            st.rerun()

    _example_chips()

    # Both slots are reserved before either request runs, so the answer can be written
    # above evidence that was drawn while it was still composing.
    answer_slot = st.container()
    evidence_slot = st.container()

    if asked:
        _ask(client, query.strip(), options, answer_slot, evidence_slot)
        return

    result = st.session_state.get(RESULT_KEY)
    if result is None:
        with evidence_slot:
            _empty_state()
        return

    answer = st.session_state.get(ANSWER_KEY)
    with answer_slot:
        if answer is not None:
            _answer_panel(answer)
    with evidence_slot:
        _passages_panel(result)


def _ask(
    client: ApiClient,
    query: str,
    options: dict[str, Any],
    answer_slot: DeltaGenerator,
    evidence_slot: DeltaGenerator,
) -> None:
    # Retrieve, draw the evidence, then compose into the slot above it.
    st.session_state.pop(ANSWER_KEY, None)
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
            return

    with evidence_slot:
        _passages_panel(st.session_state[RESULT_KEY])

    with answer_slot, st.status(
        "Composing the answer\N{HORIZONTAL ELLIPSIS}", expanded=True
    ) as status:
        st.caption(
            "Two to four minutes on this host. The model has no GPU to offload to, so it "
            "reads the evidence at about 26 tokens per second. Nothing is shown until "
            "every numeral has been checked against the span its claim cites \N{EM DASH} "
            "the evidence below is already readable."
        )
        try:
            st.session_state[ANSWER_KEY] = client.ask(
                query, limit=options["limit"], filters=options["filters"]
            )
        except ApiError as error:
            status.update(label="The answer could not be composed", state="error")
            st.error(str(error), icon=":material/error:")
            return
        status.update(label="Answer composed", state="complete", expanded=False)

    with answer_slot:
        _answer_panel(st.session_state[ANSWER_KEY])


def _controls() -> dict[str, Any]:
    # Sidebar controls and the §20.2 hard filters.
    with st.sidebar:
        st.subheader("Evidence", divider="grey")
        limit = st.slider(
            "Passages",
            1,
            25,
            4,
            help=(
                "How many passages the answer may rest on. Every one is prompt the model "
                "must read, and reading is the dominant cost: four passages measured 302 "
                "seconds end to end on this host."
            ),
        )
        rerank = st.toggle(
            "Cross-encoder reranking",
            value=True,
            help=(
                "Measured on this host: about 1.9 s with reranking against 0.13 s "
                "without. Whether it orders better is unmeasured \N{EM DASH} there is "
                "no golden question set yet. Applies to retrieval only."
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
    # Offer a few starting questions, which is what an empty box needs.
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


def _empty_state() -> None:
    # What the page shows before anything has been asked.
    st.container(height=12, border=False)
    with st.container(border=True):
        st.markdown("#### Nothing asked yet")
        st.caption(
            "Retrieval is hybrid: BM25 over PostgreSQL lexemes and dense vectors over "
            "Qdrant, fused by reciprocal rank and reordered by a local cross-encoder. The "
            "answer is composed under a JSON schema by a local model that writes no "
            "figures of its own \N{EM DASH} every citation is resolved by code and every "
            "numeral checked against the span its claim cites before release."
        )
        columns = st.columns(3)
        for column, (label, value, note) in zip(
            columns,
            (
                ("Indexed passages", "4,969", "across 3 active generations"),
                ("Retrieval", "1.9 s", "0.13 s with reranking off"),
                ("Composed answer", "2\N{EN DASH}4 min", "CPU only; no GPU offload here"),
            ),
            strict=True,
        ):
            with column:
                st.metric(label, value, help=note, border=True)
        st.caption("Figures measured on this host, not targets.")


def _answer_panel(answer: dict[str, Any]) -> None:
    # The composed answer, what was withheld, and the decision behind both.
    decision = str(answer.get("decision", "abstained"))
    colour, icon = _DECISION_STYLE.get(decision, ("grey", ":material/help:"))

    for flag in answer.get("degraded", ()):
        st.warning(
            f"**Degraded \N{EM DASH} {flag}.** "
            f"{_FLAG_MEANINGS.get(flag, 'See the degradation flags in §20.12.')}",
            icon=":material/warning:",
        )

    with st.container(border=True):
        head, badge = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.markdown("##### Composed answer")
        with badge:
            state_badge(Wiring.LIVE)

        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.badge(decision.capitalize(), icon=icon, color=colour)
            st.html(
                '<span class="fs-meta">Support band '
                f'<strong>{answer.get("support_band", "none")}</strong> \N{BULLET} '
                "a rule over what survived, not a probability of correctness</span>"
            )

        claims = answer.get("claims") or ()
        if claims:
            st.container(height=4, border=False)
            for claim in claims:
                _claim(claim)
        else:
            st.info(_abstention_note(answer), icon=":material/info:")

        _withheld_panel(answer.get("withheld") or ())
        _reasons(answer.get("reason_codes") or (), released=bool(claims))
        _answer_footer(answer)


def _claim(claim: dict[str, Any]) -> None:
    # One released claim, its inline citation marks, and the spans behind them.
    #
    # ``cited_passage_ids`` rather than the citation list: there is one citation per
    # source element, so a claim resting on a passage built from eleven table rows has
    # eleven citations naming the same passage, and marking each would render [4] eleven
    # times. The API supplies the distinct ids for exactly this reason.
    ids = list(claim.get("cited_passage_ids") or ())
    marks = "".join(f'<span class="fs-cite">{identifier}</span>' for identifier in ids)
    st.html(
        f'<div style="line-height:1.75;font-size:0.97rem;margin:0.35rem 0">'
        f"{claim.get('text', '')} {marks}</div>"
    )

    for finding in claim.get("disclosures") or ():
        code = str(finding.get("code", ""))
        st.warning(
            f"**Shown with a qualification \N{EM DASH} {code}.** "
            f"{_REASON_MEANINGS.get(code, finding.get('detail', ''))}",
            icon=":material/info:",
        )

    citations = claim.get("citations") or ()
    if not citations:
        return

    with st.container(horizontal=True, gap="small"):
        for identifier in ids:
            spans = [
                citation
                for citation in citations
                if citation.get("passage_id") == identifier
            ]
            with st.popover(f"[{identifier}]", help="The stored spans this claim rests on"):
                st.caption(
                    f"Passage {identifier} \N{BULLET} {len(spans)} source region(s). "
                    "Read from the source representation; the model wrote none of this."
                )
                for span in spans:
                    _span(span)


def _span(span: dict[str, Any]) -> None:
    # One stored source span, with the address a reader is shown and its element id.
    body = str(span.get("text", ""))
    shown = body if len(body) <= _SPAN_PREVIEW else f"{body[:_SPAN_PREVIEW]}\N{HORIZONTAL ELLIPSIS}"
    st.html(
        f'<div class="fs-meta">{span.get("locator", "")}</div>'
        f'<div class="fs-passage" style="margin:0.2rem 0 0.5rem">{shown}</div>'
        f'<div><span class="fs-id">{span.get("source_element_id", "")}</span></div>'
    )


def _withheld_panel(withheld: tuple[dict[str, Any], ...]) -> None:
    # What the Evidence Gate removed. Shown, not hidden: a reader who cannot see the
    # removal cannot tell a complete answer from a dismantled one.
    if not withheld:
        return

    with st.expander(
        f"Withheld by the Evidence Gate ({len(withheld)})",
        icon=":material/shield:",
        expanded=True,
    ):
        st.caption(
            "The model wrote these and they were not released. They are shown so the "
            "answer above can be judged for completeness \N{EM DASH} do not read them as "
            "findings."
        )
        for claim in withheld:
            st.html(
                '<div class="fs-passage" style="border-left-color:#ef4444">'
                f'{claim.get("text", "")}</div>'
            )
            for finding in claim.get("findings") or ():
                code = str(finding.get("code", ""))
                st.caption(
                    f"**{code}** \N{EM DASH} "
                    f"{_REASON_MEANINGS.get(code, finding.get('detail', ''))}"
                )


def _reasons(codes: tuple[str, ...], *, released: bool) -> None:
    # Reason codes in plain language. Only when something was withheld or qualified:
    # on a clean answer the list is empty and a heading over nothing reads as a warning.
    if not codes or not released:
        return
    with st.expander(f"Why content was withheld or qualified ({len(codes)})"):
        for code in codes:
            st.markdown(f"**{code}** \N{EM DASH} {_REASON_MEANINGS.get(code, code)}")


def _abstention_note(answer: dict[str, Any]) -> str:
    # Why there is no answer, in words rather than codes.
    codes = [str(code) for code in (answer.get("reason_codes") or ())]
    if not codes:
        return (
            "No answer was composed. The degradation flag above says why; the evidence "
            "below was still retrieved."
        )
    explained = " ".join(_REASON_MEANINGS.get(code, code) for code in codes)
    return f"**No claim was released.** {explained}"


def _answer_footer(answer: dict[str, Any]) -> None:
    # What the answer cost and where the time went.
    timings = answer.get("timings_ms") or {}
    total = timings.get("total_ms", 0)
    generation = timings.get("generation_ms", 0)

    strip = st.columns(4)
    strip[0].metric("Claims released", len(answer.get("claims") or ()), border=True)
    strip[1].metric("Withheld", len(answer.get("withheld") or ()), border=True)
    strip[2].metric(
        "Evidence",
        f"{len(answer.get('passages') or ())} passages",
        help=(
            f"{answer.get('evidence_used_chars', 0)} of "
            f"{answer.get('evidence_budget_chars', 0)} characters of budget used; "
            f"{answer.get('passages_dropped_for_budget', 0)} dropped."
        ),
        border=True,
    )
    strip[3].metric(
        "Answer time",
        f"{total / 1000:.0f} s",
        help=f"Of which the model took {generation / 1000:.0f} s.",
        border=True,
    )

    st.caption(
        f"Model {answer.get('model', '\N{EM DASH}')} \N{BULLET} "
        f"{answer.get('prompt_tokens', 0)} prompt and "
        f"{answer.get('completion_tokens', 0)} completion tokens \N{BULLET} "
        f"recorded as {answer.get('answer_id', '\N{EM DASH}')}"
    )


def _passages_panel(result: dict[str, Any]) -> None:
    # The retrieved passages the answer was composed from.
    for flag in result.get("degraded", ()):
        st.warning(
            f"**Degraded \N{EM DASH} {flag}.** "
            f"{_FLAG_MEANINGS.get(flag, 'See the degradation flags in §20.12.')}",
            icon=":material/warning:",
        )

    # Reranking off is a property of *this panel only*: POST /v1/ask carries no rerank
    # field, so a composed answer always rests on the reranked order. Said out loud,
    # because a reader comparing an answer against a differently ordered evidence list
    # would reasonably assume the answer was built from what they can see.
    if not result.get("reranked") and "reranker_unavailable" not in (
        result.get("degraded") or ()
    ):
        st.info(
            "Reranking is switched off for the passages below, so they are in fusion "
            "order. A composed answer always uses the reranked order \N{EM DASH} the two "
            "lists may differ, and the answer's own passage count is shown above.",
            icon=":material/swap_vert:",
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
    # One passage, with its provenance inline rather than behind a click.
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
