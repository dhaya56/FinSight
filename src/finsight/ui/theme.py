"""Shared presentation: stylesheet, page chrome, and the wiring-state badge.

**The wiring state of every panel is part of the design, not a disclaimer bolted on.**
Most of this frontend is ahead of the backend: retrieval and the query trace are live,
and the rest shows the shape of a capability that is built but not yet connected, or
not yet built at all. :func:`state_badge` renders that distinction consistently, so a
reader can tell at a glance which numbers came from the corpus and which are fixtures.
Leaving it implicit would be the one genuinely misleading choice available here.

Comments rather than attribute docstrings throughout this package: Streamlit's "magic"
renders a bare top-level string literal as page content, so a docstring under a module
constant is published to the reader.
"""

from enum import StrEnum
from typing import Final, Literal

import streamlit as st

# Streamlit types its badge colours as a literal union, so the lookup tables below are
# annotated with it rather than with ``str``. Otherwise every call site needs a cast.
BadgeColour = Literal[
    "red", "orange", "yellow", "blue", "green", "violet", "gray", "grey", "primary"
]

__all__ = [
    "BadgeColour",
    "Wiring",
    "inject_theme",
    "page_header",
    "panel_caption",
    "state_badge",
]


class Wiring(StrEnum):
    """How much of a panel is real.

    LIVE answers from the corpus through the authenticated API. PREVIEW is rendered
    from fixtures in :mod:`finsight.ui.demo` so the interaction and layout can be
    reviewed before the backend exists. PARTIAL mixes the two and says which half.
    """

    LIVE = "Live"
    PARTIAL = "Partly live"
    PREVIEW = "Preview"


# Badge colour per wiring state. Green reads as "trust this", grey as "shape only",
# amber as "read the caption".
_BADGE_COLOUR: Final[dict[Wiring, BadgeColour]] = {
    Wiring.LIVE: "green",
    Wiring.PARTIAL: "orange",
    Wiring.PREVIEW: "grey",
}

_BADGE_ICON: Final = {
    Wiring.LIVE: ":material/bolt:",
    Wiring.PARTIAL: ":material/timelapse:",
    Wiring.PREVIEW: ":material/visibility:",
}

_BADGE_HELP: Final = {
    Wiring.LIVE: "Answers from the indexed corpus through the FinSight API.",
    Wiring.PARTIAL: "Some values are live; the caption says which are not.",
    Wiring.PREVIEW: (
        "Interface preview rendered from fixtures. The capability is planned and not "
        "yet wired, so no value here came from a filing."
    ),
}

# One stylesheet, injected once per run. Streamlit's own classes are deliberately not
# targeted by name: they are generated and change between releases, so anything that
# depends on them breaks on upgrade. Everything below styles either a semantic element
# or a class this application adds itself.
_STYLESHEET: Final = """
<style>
  :root {
    --fs-border: rgba(148, 163, 184, 0.22);
    --fs-surface: rgba(148, 163, 184, 0.06);
    --fs-surface-strong: rgba(148, 163, 184, 0.12);
    --fs-accent: #6366f1;
    --fs-muted: rgba(230, 233, 239, 0.62);
  }

  /* Tighten the default page padding: the stock top gap wastes a third of a
     laptop screen before any content appears. */
  .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }

  h1, h2, h3 { letter-spacing: -0.015em; font-weight: 650; }
  h1 { font-size: 1.85rem !important; }

  /* Page chrome */
  .fs-head { display: flex; align-items: baseline; gap: 0.65rem; flex-wrap: wrap; }
  .fs-head-title { font-size: 1.6rem; font-weight: 660; letter-spacing: -0.02em; }
  .fs-head-sub {
    color: var(--fs-muted); font-size: 0.92rem; margin: 0.25rem 0 0.1rem;
    max-width: 78ch; line-height: 1.5;
  }
  .fs-rule {
    height: 1px; border: 0; margin: 0.9rem 0 1.3rem;
    background: linear-gradient(90deg, var(--fs-border), transparent);
  }

  /* Sidebar brand block */
  .fs-brand { display: flex; align-items: center; gap: 0.6rem; margin: 0.2rem 0 0.1rem; }
  .fs-brand-mark {
    width: 30px; height: 30px; border-radius: 8px; flex: none;
    background: linear-gradient(135deg, var(--fs-accent), #8b5cf6);
    display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 0.95rem;
  }
  .fs-brand-name { font-weight: 660; font-size: 1.05rem; letter-spacing: -0.01em; }
  .fs-brand-sub { color: var(--fs-muted); font-size: 0.72rem; margin-top: -2px; }

  /* Evidence card */
  .fs-passage {
    border: 1px solid var(--fs-border); border-left: 3px solid var(--fs-accent);
    border-radius: 10px; padding: 0.85rem 1rem; background: var(--fs-surface);
    font-size: 0.94rem; line-height: 1.62; white-space: pre-wrap;
  }
  .fs-meta { color: var(--fs-muted); font-size: 0.8rem; }
  .fs-crumb {
    color: var(--fs-muted); font-size: 0.78rem; font-variant: all-small-caps;
    letter-spacing: 0.04em;
  }

  /* Inline citation chip, used by the generated-answer preview */
  .fs-cite {
    display: inline-block; padding: 0.02rem 0.38rem; margin: 0 0.1rem;
    border-radius: 5px; background: rgba(99, 102, 241, 0.18);
    border: 1px solid rgba(99, 102, 241, 0.4);
    font-size: 0.74rem; font-weight: 600; vertical-align: 1px;
    font-variant-numeric: tabular-nums;
  }

  /* Monospace identifiers: ids are for comparing, so they need to align. */
  .fs-id {
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 0.76rem; color: var(--fs-muted);
  }

  /* Pipeline stage strip */
  .fs-stage {
    border: 1px solid var(--fs-border); border-radius: 9px; padding: 0.55rem 0.7rem;
    background: var(--fs-surface); font-size: 0.82rem; line-height: 1.35;
  }
  .fs-stage-done { border-color: rgba(34, 197, 94, 0.45); }
  .fs-stage-active { border-color: rgba(99, 102, 241, 0.6); background: rgba(99,102,241,0.1); }
  .fs-stage-label { font-weight: 620; display: block; }
  .fs-stage-note { color: var(--fs-muted); font-size: 0.74rem; }

  /* Make dataframes read as tables rather than spreadsheets. */
  [data-testid="stDataFrame"] { border-radius: 10px; }
</style>
"""


def inject_theme() -> None:
    """Install the stylesheet. Safe to call once per script run."""
    st.html(_STYLESHEET)


def state_badge(wiring: Wiring) -> None:
    """Render the wiring badge for a panel."""
    st.badge(
        wiring.value,
        icon=_BADGE_ICON[wiring],
        color=_BADGE_COLOUR[wiring],
        help=_BADGE_HELP[wiring],
    )


def page_header(title: str, subtitle: str, wiring: Wiring) -> None:
    """Render a page's title, one-line purpose, and wiring state."""
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        st.html(
            f'<div class="fs-head"><span class="fs-head-title">{title}</span></div>'
            f'<p class="fs-head-sub">{subtitle}</p>'
        )
    with right:
        state_badge(wiring)
    st.html('<hr class="fs-rule" />')


def panel_caption(wiring: Wiring, note: str = "") -> None:
    """State plainly what a panel's numbers are, beneath the panel.

    Carried on every preview panel rather than once at the top of the page, because a
    reader who scrolls to a chart has not necessarily read the header above it.
    """
    if wiring is Wiring.LIVE:
        if note:
            st.caption(note)
        return
    prefix = (
        "Fixture data \N{EM DASH} this capability is not yet wired."
        if wiring is Wiring.PREVIEW
        else "Partly live."
    )
    st.caption(f"{prefix} {note}".strip())
