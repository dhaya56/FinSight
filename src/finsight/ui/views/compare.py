"""Compare: the same concept across issuers and periods, on a comparable basis.

**A comparison is only as good as the dimensions it holds constant.** Two revenue figures
can differ because one is consolidated and the other standalone, because one is restated
and the other as originally reported, or because the units differ. This surface refuses the
comparison rather than rendering a misleading chart, which is the only honest behaviour
available: a chart with a footnote still gets screenshotted without the footnote.

Preview. The ledger this reads from is not yet built, and every issuer here is invented.
"""

from decimal import Decimal

import streamlit as st

from finsight.ui import demo
from finsight.ui.theme import Wiring, page_header, panel_caption, state_badge


def render() -> None:
    """Render the Compare page."""
    page_header(
        "Compare",
        "Put one concept side by side across periods, with the reporting basis held "
        "constant and refusals shown rather than hidden.",
        Wiring.PREVIEW,
    )

    controls = st.columns([2, 2, 1.4])
    with controls[0]:
        issuers = st.multiselect(
            "Issuers",
            options=demo.DEMO_ISSUERS,
            default=list(demo.DEMO_ISSUERS[:2]),
            placeholder="Choose at least one",
        )
    with controls[1]:
        concept = st.selectbox(
            "Concept",
            options=[c for c in demo.ledger_concepts if c != "Return on net worth"],
            index=0,
        )
    with controls[2]:
        # Falls back rather than allowing None: the control is clearable, and a
        # comparison with no basis fixed is the one thing this page must not render.
        basis = (
            st.segmented_control(
                "Basis", options=["Consolidated", "Standalone"], default="Consolidated"
            )
            or "Consolidated"
        )

    if not issuers:
        st.info("Choose at least one issuer to compare.", icon=":material/info:")
        return

    rows, refusals = _assemble(issuers, concept, basis.lower())

    if refusals:
        for issuer, reason in refusals:
            st.warning(f"**{issuer} excluded.** {reason}", icon=":material/block:")

    if not rows:
        st.info(
            "Nothing comparable. Every candidate was excluded for a reason listed above.",
            icon=":material/info:",
        )
        panel_caption(Wiring.PREVIEW)
        return

    header, badge = st.columns([5, 1], vertical_alignment="center")
    with header:
        st.markdown(f"##### {concept} \N{EM DASH} {basis}")
    with badge:
        state_badge(Wiring.PREVIEW)

    chart, table = st.columns([3, 2], gap="medium")
    with chart:
        # Grouped by issuer natively rather than pivoted: one bar per issuer per
        # period, which is the comparison, and no reshaping step to get it wrong.
        st.bar_chart(rows, x="Period", y="Value", color="Issuer", height=300, stack=False)
        st.caption("INR crore, as presented. Axes are not indexed or rebased.")
    with table:
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            column_config={
                "Value": st.column_config.NumberColumn("Value", format="%.0f"),
                "Growth": st.column_config.NumberColumn("YoY", format="%+.1f%%"),
            },
        )

    with st.expander("What this comparison holds constant", icon=":material/rule:"):
        for line in (
            f"**Reporting basis** \N{EM DASH} {basis.lower()} for every figure shown.",
            "**Unit and currency** \N{EM DASH} INR crore throughout; a differing unit "
            "is a refusal, not a conversion.",
            "**Value state** \N{EM DASH} as reported. A restated figure is a different "
            "fact and is not mixed in.",
            "**Period nature** \N{EM DASH} full year against full year; a quarter is "
            "never compared to a year.",
            "**Assurance** \N{EM DASH} audited figures only, so an unaudited quarter "
            "cannot flatter a trend.",
        ):
            st.markdown(f"- {line}")

    panel_caption(
        Wiring.PREVIEW,
        "Fixture values for invented issuers. The refusal logic shown is the intended "
        "behaviour, not a live check.",
    )


def _assemble(
    issuers: list[str], concept: str, basis: str
) -> tuple[list[dict[str, object]], list[tuple[str, str]]]:
    """Build the comparison, recording why anything was left out.

    The refusals are the interesting part. Northwind is carried as a deliberate case where
    the requested basis does not exist for that issuer, so the page has something real to
    refuse rather than always succeeding.
    """
    rows: list[dict[str, object]] = []
    refusals: list[tuple[str, str]] = []

    for issuer in issuers:
        facts = [
            f
            for f in demo.demo_facts(issuer)
            if f.concept == concept and f.basis == basis and f.as_presented
        ]
        if not facts:
            refusals.append(
                (
                    issuer,
                    f"No {concept.lower()} recorded on a {basis} basis as presented. "
                    f"Substituting the other basis would make the comparison invalid.",
                )
            )
            continue
        ordered = sorted(facts, key=lambda f: f.period)
        previous: Decimal | None = None
        for fact in ordered:
            growth = (
                float((fact.value - previous) / previous * Decimal(100))
                if previous not in (None, Decimal(0))
                else None
            )
            rows.append(
                {
                    "Issuer": issuer,
                    "Period": fact.period,
                    "Value": float(fact.value),
                    "Growth": growth,
                }
            )
            previous = fact.value

    return rows, refusals
