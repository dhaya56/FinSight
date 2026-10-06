"""Fact Ledger: the bounded set of concepts FinSight will compare and compute on.

**The boundary is the design.** A figure may be normalised, compared across periods or
used in a calculation only if its concept is in an approved catalogue and the value is
bound to the exact source region it was read from. Everything else in a filing stays
searchable narrative that can be quoted but not arithmetic — which is what stops a
number being silently reinterpreted into a comparison it does not support.

Every dimension on a fact is carried, not collapsed: issuer, period, period nature, basis,
currency, unit, value state, assurance, and whether the figure appeared as presented or was
derived. Two numbers that differ only in basis are different facts.

Preview. The ledger tables are specified and not yet built; every issuer here is invented.
"""

from decimal import Decimal

import streamlit as st

from finsight.ui import demo
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge


def render() -> None:
    """Render the Fact Ledger page."""
    page_header(
        "Fact Ledger",
        "Canonical financial facts, each bound to the source region it was read from and "
        "carrying every dimension needed to compare it safely.",
        Wiring.PREVIEW,
    )

    issuer = st.selectbox("Issuer", options=demo.DEMO_ISSUERS, index=0)
    facts = demo.demo_facts(issuer)

    tabs = st.tabs(["Facts", "Concept catalogue", "Calculations"])

    with tabs[0]:
        _facts_tab(facts)
    with tabs[1]:
        _catalogue_tab(facts)
    with tabs[2]:
        _calculations_tab(facts)


def _facts_tab(facts: list[demo.DemoFact]) -> None:
    """The fact table, filterable by the dimensions that make a fact what it is."""
    controls = st.columns(3)
    with controls[0]:
        periods = st.multiselect(
            "Period", sorted({f.period for f in facts}), placeholder="All periods"
        )
    with controls[1]:
        bases = st.multiselect(
            "Reporting basis", sorted({f.basis for f in facts}), placeholder="All bases"
        )
    with controls[2]:
        only_presented = st.toggle(
            "As presented only",
            value=False,
            help=(
                "Excludes derived values. A derived figure is legitimate but is not what "
                "the filing printed, and the two must never be mixed in one comparison."
            ),
        )

    shown = [
        f
        for f in facts
        if (not periods or f.period in periods)
        and (not bases or f.basis in bases)
        and (not only_presented or f.as_presented)
    ]

    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown(f"##### Facts ({len(shown)})")
    with badge:
        state_badge(Wiring.PREVIEW)

    if not shown:
        st.info("No facts match this filter.", icon=":material/filter_alt_off:")
        return

    rows = [
        {
            "Concept": f.concept,
            "Period": f.period,
            "Basis": f.basis,
            "Value": float(f.value),
            "Unit": f"{f.currency} {f.unit}",
            "State": f.value_state,
            "Assurance": f.assurance,
            "As presented": f.as_presented,
            "Source": f.source,
        }
        for f in shown
    ]
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Value": st.column_config.NumberColumn("Value", format="%.2f"),
            "As presented": st.column_config.CheckboxColumn(
                "As presented", help="False means FinSight derived it rather than reading it."
            ),
            "Source": st.column_config.TextColumn("Source region", width="medium"),
        },
    )
    panel_caption(
        Wiring.PREVIEW,
        "Arithmetic on these values uses Decimal, never floating point and never model "
        "arithmetic. The float column above is a display artefact of the preview table.",
    )


def _catalogue_tab(facts: list[demo.DemoFact]) -> None:
    """Which concepts are in the ledger, and what happens to everything else."""
    st.caption(
        "A concept enters this catalogue by decision, not by appearing in a filing. "
        "Numbers outside it remain quotable from their source region but are never "
        "normalised, compared or computed on."
    )
    present = {f.concept for f in facts}
    columns = st.columns(2)
    for index, concept in enumerate(demo.ledger_concepts):
        with (
            columns[index % 2],
            st.container(horizontal=True, gap="small", vertical_alignment="center"),
        ):
            in_ledger = concept in present
            st.badge(
                "in ledger" if in_ledger else "catalogued",
                icon=":material/check:" if in_ledger else ":material/schedule:",
                color="green" if in_ledger else "grey",
            )
            st.markdown(concept)

    st.container(height=8, border=False)
    with st.container(border=True):
        st.markdown("**Outside the catalogue**")
        st.caption(
            "Segment tables, ratio disclosures, related-party totals, employee counts and "
            "every other figure in a filing. These stay in Narrative RAG: retrievable and "
            "quotable with a citation, never reinterpreted. Discarding them would lose "
            "most of the document; promoting them silently would be worse."
        )
    panel_caption(Wiring.PREVIEW)


def _calculations_tab(facts: list[demo.DemoFact]) -> None:
    """Derived metrics, with the inputs that produced them."""
    st.caption(
        "A derived figure records its formula and its inputs, so it can be recomputed and "
        "checked rather than trusted. The model never performs the arithmetic."
    )
    by_key = {(f.concept, f.period): f for f in facts}

    rows = []
    for period in sorted({f.period for f in facts}, reverse=True):
        pat = by_key.get(("Profit after tax", period))
        equity = by_key.get(("Total equity", period))
        revenue = by_key.get(("Revenue from operations", period))
        pbt = by_key.get(("Profit before tax", period))
        if not (pat and equity and revenue and pbt):
            continue
        rows.append(
            {
                "Period": period,
                "Return on net worth": _percent(pat.value, equity.value),
                "Net margin": _percent(pat.value, revenue.value),
                "Tax rate (effective)": _percent(pbt.value - pat.value, pbt.value),
                "Inputs": f"PAT {pat.value}, equity {equity.value}, revenue {revenue.value}",
            }
        )

    if not rows:
        st.info("Not enough facts in the ledger to derive these.", icon=":material/info:")
        return

    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Return on net worth": st.column_config.NumberColumn(format="%.2f%%"),
            "Net margin": st.column_config.NumberColumn(format="%.2f%%"),
            "Tax rate (effective)": st.column_config.NumberColumn(format="%.2f%%"),
            "Inputs": st.column_config.TextColumn("Inputs, in INR crore", width="large"),
        },
    )
    st.caption(
        "Every ratio here is computed from the two facts named beside it, both on the same "
        "reporting basis and in the same unit. A comparison that mixed bases would be "
        "refused rather than footnoted."
    )
    panel_caption(Wiring.PREVIEW)


def _percent(numerator: Decimal, denominator: Decimal) -> float:
    """A percentage, computed in Decimal and converted only for display."""
    if denominator == 0:
        return 0.0
    return float(numerator / denominator * Decimal(100))
