"""Ask: a conversation of questions, each answered from the corpus with its evidence.

Everything here is real. Passages come from the indexed corpus through the authenticated API;
the answer is composed by a local model, its citations resolved to stored source spans by code,
and every numeral checked against a span its own claim cites before release (ADR-009).

**A thread of independent questions, not a conversation.** ``st.chat_input`` pins the box to the
bottom and earlier turns stay above it, so a reader can scroll back — but nothing carries context
between turns. Each question is sent to the backend alone. "What about the previous year?" after
"what was revenue?" retrieves on those five words and will usually abstain. The page says so
rather than letting the shape of a chat imply a memory that does not exist.

**Two requests per turn, in that order, and the order is the point.** A composed answer takes two
to five minutes on a host without GPU offload, because prompt evaluation runs at roughly 27 tokens
per second. Retrieval answers in about two seconds, so the passages are drawn first and the answer
composes into a slot reserved above them. The reader has citable evidence in seconds rather than a
frozen page, and the arrangement states the architecture: evidence is primary, prose is the
convenience built on top of it.

**Claims are joined into prose, and grouped by issuer.** The model emits discrete sentences;
rendering one block per sentence made a seven-claim answer read as seven fragments. Joining them
is presentational — each sentence keeps its own citations — but joining claims about *different
companies* into one smooth paragraph would read as one company's position, so the grouping is by
issuer and a multi-issuer answer says so. The Evidence Gate cannot catch this: ``mixed_issuer``
fires when one claim cites two issuers, and here each claim is internally consistent while the
answer as a whole is not.

**Document text is escaped before it reaches ``st.html``.** Streamlit sanitizes with DOMPurify and
ignores JavaScript, so this is not about script injection. It is that an unescaped ``<`` is read as
the start of a tag and its text is swallowed: 46 source elements in this corpus contain one, and
HDFC Bank's report discusses the ``<IR>`` framework, which rendered raw simply vanishes from the
page. Evidence shown must be evidence stored.

**Streaming the model's output is deliberately not an option.** The Evidence Gate must parse the
complete response and verify every numeral before anything is released, so streaming tokens would
put unverified figures in front of a reader — the failure this design exists to prevent.
"""

from html import escape
from typing import Any
from uuid import uuid4

import streamlit as st

from finsight.ui.client import ApiClient, ApiError, client_from_environment
from finsight.ui.theme import BadgeColour, opening_layout

RESULT_KEY = "ask.result"
TURNS_KEY = "ask.turns"
STARTER_KEY = "ask.starter"
ANSWER_LABEL = "Answer composed"

_SPAN_PREVIEW = 420
_HOVER_PREVIEW = 300

# Starters in two registers, because the audience is two audiences: a general reader
# asking what a company says, and an analyst asking about a specific disclosure.
#
# **Every one of these is answerable today, and that is the constraint.** A genuinely
# analyst-grade prompt — "revenue growth between FY2024 and FY2025", "compare Infosys and
# HDFC Bank" — is refused by design: §7 forbids model arithmetic, no query planner reads a
# year or an issuer out of the question, and tables are excluded from retrieval (ADR-003).
# Offering those as starters would advertise a capability the system does not have, which
# is a worse failure than offering fewer.
_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("What does the company say about credit risk?", "General"),
    ("How is revenue recognised?", "General"),
    ("What are the principal risks to the business?", "General"),
    ("What is disclosed about the expected credit loss model?", "Analyst"),
    ("What does the company disclose about related party transactions?", "Analyst"),
    ("How is foreign exchange risk managed?", "Analyst"),
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
    "generation_unavailable": (
        "The language model could not be reached, so no answer was composed. The "
        "passages were still retrieved and are unaffected \N{EM DASH} they are the "
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

_DECISION_STYLE: dict[str, tuple[BadgeColour, str]] = {
    "answered": ("green", ":material/check_circle:"),
    "partial": ("orange", ":material/rule:"),
    "abstained": ("grey", ":material/block:"),
}


def render() -> None:
    # Render the Ask page.
    #
    # No page header. The title, subtitle and badge pushed the question box below the fold
    # on a laptop, and a chat surface's first screen belongs to the question — every page
    # still carries its wiring state in the sidebar's page list.
    try:
        client = client_from_environment()
    except ApiError as error:
        st.error(str(error))
        return

    turns: list[dict[str, Any]] = st.session_state.setdefault(TURNS_KEY, [])
    opening = not turns

    # A starter chip stores its question and reruns, because `st.chat_input` cannot be
    # prefilled. Read before the box is drawn so the run that renders the thread is the
    # run that asks.
    picked = str(st.session_state.pop(STARTER_KEY, "") or "").strip()

    if opening and not picked:
        # Un-pin the box so it falls into the flow and sits in the middle, as every chat
        # product does before there is a conversation above it.
        opening_layout()
        # The opening screen lives in a placeholder so a starter click can erase it
        # immediately. Streamlit keeps the previous render on screen until the next run
        # *finishes*, so without this the chips stayed clickable for the whole of a
        # multi-minute generation and only vanished once the answer arrived.
        slot = st.empty()
        with slot.container():
            st.container(height=70, border=False)
            st.html('<div class="fs-hero">What would you like to know?</div>')
            chosen = _starters()
        if chosen:
            slot.empty()
            st.session_state[STARTER_KEY] = chosen
            st.rerun()
    else:
        for turn in turns:
            _replay(turn)
        st.html('<div id="fs-newest"></div>')

    options = _controls(client)

    typed = st.chat_input(
        "Ask FinSight",
        max_chars=2000,
        # The submit control becomes a stop control while the script is running, which is
        # what stops a second question being sent into a multi-minute wait.
        submit_mode="stop",
    )
    question = (picked or str(typed or "")).strip()

    if not opening:
        # Jump to the newest turn. A link rather than a script: `st.html` ignores
        # JavaScript by default and enabling it to move a scrollbar is a poor trade.
        st.html(
            '<a class="fs-to-bottom" href="#fs-newest" title="Jump to the newest">'
            "\N{DOWNWARDS ARROW}</a>"
        )

    if question:
        turns.append(_run_turn(client, question, options))


@st.cache_data(ttl=300, show_spinner=False)
def _facets(_client: ApiClient, cache_key: str) -> dict[str, Any]:
    """The values each filter can take, cached so a sidebar redraw is not a request.

    The leading underscore tells Streamlit not to hash the client; ``cache_key`` carries
    the connection, so the cache turns over when the server does. A first version built
    its own client from the url and token instead, which cached correctly and quietly
    ignored the client it was given — including the one the tests inject.
    """
    return _client.facets()


def _controls(client: ApiClient) -> dict[str, Any]:
    # Sidebar controls and the §20.2 hard filters.
    #
    # **Every scope widget sets `on_change="ignore"`.** Streamlit reruns the whole script
    # on any widget interaction, which redrew the page each time a filter was touched. The
    # values still land in session state and are read when a question is sent; what is
    # suppressed is the rerun, so choosing a scope no longer disturbs the conversation.
    facets = _facets(client, client.base_url)

    with st.sidebar:
        st.subheader("Evidence", divider="grey")
        limit = st.slider(
            "Passages",
            1,
            25,
            4,
            key="ask.limit",
            on_change="ignore",
            help=(
                "How many passages an answer may rest on. Every one is prompt the model "
                "must read, and reading is the dominant cost: four passages measured 302 "
                "seconds end to end on this host."
            ),
        )
        rerank = st.toggle(
            "Cross-encoder reranking",
            value=True,
            key="ask.rerank",
            on_change="ignore",
            help=(
                "Measured on this host: about 1.9 s with reranking against 0.13 s "
                "without. Whether it orders better is unmeasured \N{EM DASH} there is "
                "no golden question set yet. Applies to the retrieved list only; an "
                "answer always uses the reranked order."
            ),
        )

        st.subheader("Scope", divider="grey")
        issuer = _choose("Issuer", facets.get("issuer_names"), "ask.issuer")
        document_type = _choose(
            "Document type", facets.get("document_types"), "ask.document_type"
        )
        period = _choose("Fiscal period", facets.get("fiscal_periods"), "ask.period")
        basis = _choose(
            "Reporting basis", facets.get("reporting_bases"), "ask.basis"
        )
        section = _choose("Section", facets.get("sections"), "ask.section")
        # A selectbox rather than a segmented control, and not only for consistency:
        # `st.segmented_control` types `on_change` as a callback alone, so it cannot be
        # told not to rerun, and one widget redrawing the page would undo the point of
        # the rest.
        evidence = _choose(
            "Evidence type",
            ["Narrative", "Table"],
            "ask.evidence_type",
            help="Table-derived evidence is excluded from retrieval today (ADR-003).",
        )

        if st.session_state.get(TURNS_KEY):
            st.subheader("Session", divider="grey")
            if st.button("Clear conversation", icon=":material/delete_sweep:"):
                st.session_state.pop(TURNS_KEY, None)
                st.session_state.pop(RESULT_KEY, None)
                st.rerun()

    mapped = {"Narrative": "narrative", "Table": "table_derived"}
    return {
        "limit": limit,
        "rerank": rerank,
        "filters": {
            "issuer_name": issuer,
            "document_type": document_type,
            "fiscal_period": period,
            "reporting_basis": basis,
            "evidence_type": mapped.get(evidence or ""),
            "section": section,
        },
    }


def _choose(label: str, options: Any, key: str, *, help: str | None = None) -> str | None:
    """One optional hard filter, offered as a list of values that actually exist.

    **A filter a reader has to spell is a trap.** These were free-text boxes, so "Infosys"
    matched nothing while "Infosys Limited" matched — and §7 forbids relaxing a filter to
    find more results, so the near-miss returned an empty set that reads as "the corpus
    holds nothing" rather than "that is not the recorded name".

    ``index=None`` leaves it unset and gives the control its own clear affordance, so a
    chosen value can be taken back off without a reset button. Disabled rather than hidden
    when the API offered nothing, because a missing control is indistinguishable from a
    filter that does not exist.
    """
    values = [str(value) for value in (options or [])]
    chosen = st.selectbox(
        label,
        options=values,
        index=None,
        key=key,
        on_change="ignore",
        help=help,
        placeholder="Any" if values else "Unavailable",
        disabled=not values,
    )
    return str(chosen) if chosen is not None else None


def _starters() -> str:
    """Questions to begin with. Returns the one clicked, or an empty string.

    Shown only on the opening screen: no chat product offers starters mid-thread, and they
    would push the conversation around. They sit above the box rather than below it, which
    is a consequence rather than a preference — ``st.chat_input`` lives in Streamlit's
    bottom container, so anything the page renders is necessarily before it.

    No metrics here either: a figure shown before anything has been asked describes
    nothing the reader did.

    The click is **returned** rather than acted on, so the caller can erase the opening
    screen before rerunning. Setting state and rerunning from inside left the chips on
    screen for the whole generation.
    """
    st.container(height=8, border=False)
    chosen = ""
    for row_start in range(0, len(_EXAMPLES), 3):
        columns = st.columns(3)
        for column, (example, register) in zip(
            columns, _EXAMPLES[row_start : row_start + 3], strict=False
        ):
            with column:
                if st.button(
                    example,
                    key=f"ask.starter.{row_start}.{example[:24]}",
                    help=f"{register} question",
                    width="stretch",
                ):
                    chosen = example
    return chosen


def _run_turn(
    client: ApiClient, question: str, options: dict[str, Any]
) -> dict[str, Any]:
    # Retrieve, draw the evidence, then compose into the slot above it.
    turn: dict[str, Any] = {
        # A stable identity for this turn, which is what lets its panels keep a key that
        # does not shift when another turn is appended. Without one, Streamlit identifies
        # an expander by its position, so every new question rebuilt the earlier panels
        # and re-applied `expanded=True` — the reader's collapse undone by someone else's
        # question.
        "id": uuid4().hex,
        "question": question,
        "result": None,
        "answer": None,
        "error": None,
        "ask_error": None,
    }

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar=":material/plagiarism:"):
        answer_slot = st.container()
        evidence_slot = st.container()

        with st.spinner("Retrieving from the corpus\N{HORIZONTAL ELLIPSIS}", show_time=True):
            try:
                turn["result"] = client.search(
                    question,
                    limit=options["limit"],
                    rerank=options["rerank"],
                    filters=options["filters"],
                )
            except ApiError as error:
                turn["error"] = str(error)

        if turn["error"]:
            with answer_slot:
                st.error(turn["error"], icon=":material/error:")
            return turn

        # Kept for the Trace page, which reads the most recent retrieval.
        st.session_state[RESULT_KEY] = turn["result"]
        with evidence_slot:
            _evidence(
                turn["result"], expanded=True, key=f"ask.evidence.{turn['id']}"
            )

        # The answer is rendered *inside* the status panel, not beneath it. That is what
        # makes it collapsible afterwards: the panel's own chevron then folds the whole
        # answer away under "Answer composed", which is what a reader working through a
        # long thread needs. Previously the panel held only the waiting note, so
        # collapsing it hid the note and left the answer behind.
        with answer_slot, st.status(
            "Composing the answer\N{HORIZONTAL ELLIPSIS}", expanded=True
        ) as status:
            waiting = st.empty()
            with waiting:
                st.caption(
                    "Two to five minutes on this host. The model has no GPU to offload "
                    "to, so it reads the evidence at about 27 tokens per second. Nothing "
                    "is shown until every numeral has been checked against the span its "
                    "claim cites \N{EM DASH} the retrieved evidence below is already "
                    "readable."
                )
            try:
                turn["answer"] = client.ask(
                    question, limit=options["limit"], filters=options["filters"]
                )
            except ApiError as error:
                turn["ask_error"] = str(error)

            # The waiting note is instruction for the wait, not part of the answer, so it
            # is cleared rather than left above the result a reader came for.
            waiting.empty()
            if turn["answer"] is not None:
                _answer(turn["answer"])
                status.update(label=ANSWER_LABEL, state="complete", expanded=True)
            else:
                st.error(str(turn["ask_error"]), icon=":material/error:")
                status.update(label="The answer could not be composed", state="error")

    return turn


def _replay(turn: dict[str, Any]) -> None:
    # One completed turn, re-rendered from session state. No request is repeated.
    with st.chat_message("user"):
        st.markdown(str(turn["question"]))

    with st.chat_message("assistant", avatar=":material/plagiarism:"):
        if turn["error"]:
            st.error(str(turn["error"]), icon=":material/error:")
            return
        if turn["answer"] is not None:
            # The same collapsible shape the live run ends in, so a reader working back
            # through a long thread can fold any answer away and the control does not move
            # between a turn they watched and one they scrolled to.
            #
            # Keyed on the turn, not on its position: `expanded` is the value a *new*
            # panel opens with, and a keyed panel keeps whatever the reader did to it.
            with st.expander(
                ANSWER_LABEL,
                icon=":material/auto_awesome:",
                expanded=True,
                key=f"ask.answer.{turn['id']}",
            ):
                _answer(turn["answer"])
        elif turn["ask_error"]:
            st.error(str(turn["ask_error"]), icon=":material/error:")
        if turn["result"] is not None:
            _evidence(turn["result"], expanded=False, key=f"ask.evidence.{turn['id']}")


def _answer(answer: dict[str, Any]) -> None:
    # The composed answer: prose, what was withheld, and the decision behind both.
    decision = str(answer.get("decision", "abstained"))
    colour, icon = _DECISION_STYLE.get(decision, ("grey", ":material/help:"))

    for flag in answer.get("degraded", ()):
        st.warning(
            f"**Degraded \N{EM DASH} {flag}.** "
            f"{_FLAG_MEANINGS.get(str(flag), 'See the degradation flags in §20.12.')}",
            icon=":material/warning:",
        )

    claims = list(answer.get("claims") or ())
    if claims:
        _prose(answer, claims)
    else:
        st.info(_abstention_note(answer), icon=":material/info:")

    _withheld(answer.get("withheld") or ())
    _reasons(answer.get("reason_codes") or (), released=bool(claims))

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge(decision.capitalize(), icon=icon, color=colour)
        st.html(
            '<span class="fs-meta">Support band '
            f'<strong>{escape(str(answer.get("support_band", "none")))}</strong> '
            "\N{BULLET} a rule over what survived, not a probability of "
            "correctness</span>"
        )
    _footer(answer)


def _prose(answer: dict[str, Any], claims: list[dict[str, Any]]) -> None:
    # Claims as flowing paragraphs, broken only where the issuer changes.
    passages = {
        int(passage["id"]): passage for passage in (answer.get("passages") or ())
    }
    groups = _grouped(claims, passages)
    labelled = len(groups) > 1

    for issuers, members in groups:
        if labelled:
            label = " and ".join(issuers) if issuers else "Issuer not recorded"
            st.html(f'<div class="fs-issuer">{escape(label)}</div>')
        body = " ".join(_sentence(claim) for claim in members)
        st.html(f'<div class="fs-answer">{body}</div>')

    # No banner when an answer spans two issuers: the developer found it intrusive and
    # removed it. The *separation* is kept — a paragraph never mixes companies and each
    # carries its issuer label — so the distinction a reader needs is still on the page,
    # in the content rather than in a warning box above it.
    _span_chips(claims)


def _grouped(
    claims: list[dict[str, Any]], passages: dict[int, dict[str, Any]]
) -> list[tuple[tuple[str, ...], list[dict[str, Any]]]]:
    # Consecutive claims resting on the same issuer, so a paragraph never spans two.
    groups: list[tuple[tuple[str, ...], list[dict[str, Any]]]] = []
    for claim in claims:
        issuers = tuple(
            sorted(
                {
                    str(passages[identifier]["issuer_name"])
                    for identifier in claim.get("cited_passage_ids") or ()
                    if identifier in passages and passages[identifier].get("issuer_name")
                }
            )
        )
        if groups and groups[-1][0] == issuers:
            groups[-1][1].append(claim)
        else:
            groups.append((issuers, [claim]))
    return groups


def _sentence(claim: dict[str, Any]) -> str:
    # One claim with its citation marks, escaped and ready to sit inside a paragraph.
    #
    # `cited_passage_ids` rather than the citation list: there is one citation per source
    # element, so a claim resting on a passage built from eleven table rows has eleven
    # citations naming the same passage, and marking each would render [4] eleven times.
    text = escape(str(claim.get("text", "")))
    hovers = _hover_text(claim)
    marks = "".join(
        f'<span class="fs-cite" title="{hovers.get(identifier, "")}">{identifier}</span>'
        for identifier in claim.get("cited_passage_ids") or ()
    )
    return f"{text}{marks}"


def _hover_text(claim: dict[str, Any]) -> dict[int, str]:
    # The cited span, trimmed, as an attribute the browser shows on hover. Plain text
    # only — a title attribute carries no markup — so this is the quickest path to the
    # evidence and the popovers below carry the full spans.
    collected: dict[int, list[str]] = {}
    for citation in claim.get("citations") or ():
        identifier = int(citation.get("passage_id", 0))
        locator = str(citation.get("locator", ""))
        body = " ".join(str(citation.get("text", "")).split())
        collected.setdefault(identifier, []).append(f"{locator} \N{EM DASH} {body}")

    rendered: dict[int, str] = {}
    for identifier, parts in collected.items():
        joined = "\n".join(parts)
        if len(joined) > _HOVER_PREVIEW:
            joined = f"{joined[:_HOVER_PREVIEW]}\N{HORIZONTAL ELLIPSIS}"
        rendered[identifier] = escape(joined, quote=True)
    return rendered


def _span_chips(claims: list[dict[str, Any]]) -> None:
    # One control per cited passage, opening the stored spans behind it. Collected
    # across the whole answer rather than per claim, so the row is not repeated.
    spans: dict[int, list[dict[str, Any]]] = {}
    for claim in claims:
        for citation in claim.get("citations") or ():
            identifier = int(citation.get("passage_id", 0))
            seen = spans.setdefault(identifier, [])
            if all(
                existing.get("source_element_id") != citation.get("source_element_id")
                for existing in seen
            ):
                seen.append(citation)

    if not spans:
        return

    st.caption("Sources \N{EM DASH} hover a number above for the span, or open one here.")
    with st.container(horizontal=True, gap="small"):
        for identifier in sorted(spans):
            with st.popover(
                f"[{identifier}]", help="The stored spans this answer rests on"
            ):
                st.caption(
                    f"Passage {identifier} \N{BULLET} {len(spans[identifier])} source "
                    "region(s). Read from the source representation; the model wrote "
                    "none of this."
                )
                for span in spans[identifier]:
                    _span(span)


def _flow(text: object) -> str:
    """Collapse a stored passage's whitespace so it reads as prose.

    A PDF line break is a property of the page, not of the sentence. 79.9% of stored
    elements carry one and 7.1% carry runs of spaces from column gaps; rendered verbatim
    they put a hard break mid-sentence on nearly every passage. The CLI has always done
    this and the page did not, which is why the same evidence read correctly in a terminal
    and broken in the browser.
    """
    return " ".join(str(text).split())


def _span(span: dict[str, Any]) -> None:
    # One stored source span, with the address a reader is shown and its element id.
    body = _flow(span.get("text", ""))
    shown = body if len(body) <= _SPAN_PREVIEW else f"{body[:_SPAN_PREVIEW]}\N{HORIZONTAL ELLIPSIS}"
    st.html(
        f'<div class="fs-meta">{escape(str(span.get("locator", "")))}</div>'
        f'<div class="fs-passage" style="margin:0.2rem 0 0.5rem">{escape(shown)}</div>'
        f'<div><span class="fs-id">{escape(str(span.get("source_element_id", "")))}</span></div>'
    )


def _withheld(withheld: tuple[dict[str, Any], ...]) -> None:
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
                '<div class="fs-passage" style="border-left-color:var(--fs-rose);'
                'background:var(--fs-rose-soft)">'
                f'{escape(_flow(claim.get("text", "")))}</div>'
            )
            for finding in claim.get("findings") or ():
                code = str(finding.get("code", ""))
                st.caption(
                    f"**{code}** \N{EM DASH} "
                    f"{_REASON_MEANINGS.get(code, str(finding.get('detail', '')))}"
                )


def _reasons(codes: tuple[str, ...], *, released: bool) -> None:
    # Reason codes in plain language. Only when something was released and something was
    # withheld or qualified: on an abstention the note above already carries the reason.
    if not codes or not released:
        return
    with st.expander(f"Why content was withheld or qualified ({len(codes)})"):
        for code in codes:
            st.markdown(f"**{code}** \N{EM DASH} {_REASON_MEANINGS.get(str(code), str(code))}")


def _abstention_note(answer: dict[str, Any]) -> str:
    # Why there is no answer, in words rather than codes.
    codes = [str(code) for code in (answer.get("reason_codes") or ())]
    if not codes:
        return (
            "No answer was composed. The degradation flag above says why; the evidence "
            "was still retrieved."
        )
    explained = " ".join(_REASON_MEANINGS.get(code, code) for code in codes)
    return f"**No claim was released.** {explained}"


def _footer(answer: dict[str, Any]) -> None:
    # What the answer cost and where the time went.
    timings = answer.get("timings_ms") or {}
    total = int(timings.get("total_ms", 0))
    generation = int(timings.get("generation_ms", 0))

    st.caption(
        f"{len(answer.get('claims') or ())} claim(s) released, "
        f"{len(answer.get('withheld') or ())} withheld \N{BULLET} "
        f"{len(answer.get('passages') or ())} passages, "
        f"{answer.get('evidence_used_chars', 0)} of "
        f"{answer.get('evidence_budget_chars', 0)} characters \N{BULLET} "
        f"{total / 1000:.0f} s, of which the model took {generation / 1000:.0f} s "
        "\N{BULLET} "
        f"{answer.get('prompt_tokens', 0)} prompt and "
        f"{answer.get('completion_tokens', 0)} completion tokens \N{BULLET} "
        f"model {answer.get('model', '\N{EM DASH}')} \N{BULLET} "
        f"recorded as {answer.get('answer_id', '\N{EM DASH}')}"
    )


def _evidence(result: dict[str, Any], *, expanded: bool, key: str) -> None:
    # The retrieved passages, collapsed once the answer is in place. Keyed on the turn so
    # the reader's collapse survives the next question and a trip to another page.
    candidates = list(result.get("candidates") or ())
    with st.expander(
        f"Retrieved evidence ({len(candidates)})",
        icon=":material/manage_search:",
        expanded=expanded,
        key=key,
    ):
        for flag in result.get("degraded", ()):
            st.warning(
                f"**Degraded \N{EM DASH} {flag}.** "
                f"{_FLAG_MEANINGS.get(str(flag), 'See the degradation flags in §20.12.')}",
                icon=":material/warning:",
            )

        if not result.get("reranked") and "reranker_unavailable" not in (
            result.get("degraded") or ()
        ):
            st.info(
                "Reranking is switched off for this list, so it is in fusion order. An "
                "answer always uses the reranked order \N{EM DASH} the two may differ.",
                icon=":material/swap_vert:",
            )

        if not candidates:
            st.info(
                "Nothing matched. The corpus holds only the filings that have been "
                "ingested and indexed, and table cells are deliberately excluded from "
                "retrieval (ADR-003), so a figure that exists only inside a table is not "
                "findable yet.",
                icon=":material/info:",
            )
            return

        st.caption(
            f"{len(candidates)} passage(s) in "
            f"{int(result.get('elapsed_ms', 0)) / 1000:.2f} s \N{BULLET} depth "
            f"{result.get('depth', 0)} \N{BULLET} lexical "
            f"{result.get('lexical_retriever', '\N{EM DASH}')} \N{BULLET} "
            f"reranked {'yes' if result.get('reranked') else 'no'}"
        )
        for candidate in candidates:
            _passage_card(candidate)


def _passage_card(candidate: dict[str, Any]) -> None:
    # One passage, with its provenance inline rather than behind a click.
    pages = list(candidate.get("page_numbers") or [])
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
                f'<span class="fs-meta">{escape(str(period))} \N{BULLET} {page_label} '
                f"\N{BULLET} {escape(str(candidate.get('evidence_type', '')))}</span>"
            )
        with right:
            rerank_score = candidate.get("rerank_score")
            if rerank_score is not None:
                st.metric(
                    "Rerank", f"{rerank_score:+.2f}", help="Cross-encoder logit, not a probability."
                )

        if heading:
            st.html(f'<div class="fs-crumb">{escape(" / ".join(str(h) for h in heading))}</div>')

        # Whitespace collapsed, not preserved. Measured: 79.9% of stored source elements
        # carry a newline from the PDF's own line breaks, and 7.1% carry runs of spaces
        # from column gaps. Honouring them broke nearly every passage mid-sentence. The
        # stored text is unchanged — this is how it is read, not what it is.
        st.html(f'<div class="fs-passage">{escape(_flow(candidate["text"]))}</div>')

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
                    f'<div><span class="fs-id">'
                    f'{escape(str(citation["source_element_id"]))}</span>'
                    f' \N{BULLET} <span class="fs-meta">'
                    f'{escape(str(citation["locator"]))}</span></div>'
                )
