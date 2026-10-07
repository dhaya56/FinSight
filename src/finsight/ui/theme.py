"""Shared presentation: stylesheet, page chrome, and the wiring-state badge.

**The wiring state of every panel is part of the design, not a disclaimer bolted on.**
Most of this frontend is ahead of the backend: retrieval and the query trace are live,
and the rest shows the shape of a capability that is built but not yet connected, or
Pages state their own standing in their own words where a reader is reading, rather than
through a chip in the corner: a badge on every page says nothing once every page is live.

Comments rather than attribute docstrings throughout this package: Streamlit's "magic"
renders a bare top-level string literal as page content, so a docstring under a module
constant is published to the reader.
"""

from typing import Final, Literal

import streamlit as st

# Streamlit types its badge colours as a literal union, so the lookup tables below are
# annotated with it rather than with ``str``. Otherwise every call site needs a cast.
BadgeColour = Literal[
    "red", "orange", "yellow", "blue", "green", "violet", "gray", "grey", "primary"
]

__all__ = [
    "BadgeColour",
    "inject_theme",
    "opening_layout",
    "page_header",
]


# One stylesheet, injected once per run.
#
# **Streamlit's generated class names are still not targeted.** They change between
# releases. A small number of `data-testid` hooks are used, which are stable enough that
# Streamlit's own testing API depends on them, and each one is commented with what it is
# for so an upgrade break is diagnosable rather than mysterious.
#
# Sizes are in `rem` and `clamp()` rather than pixels, and widths are capped by a measure
# rather than a fixed pixel width, so a 13-inch laptop and a 27-inch monitor both get a
# readable line instead of one cramped and one stretched.
_STYLESHEET: Final = """
<style>
  :root {
    --fs-bg: #ffffff;
    --fs-surface: #f8fafc;
    --fs-surface-strong: #eef2f8;
    --fs-border: #e2e8f0;
    --fs-text: #0f172a;
    --fs-muted: #64748b;

    --fs-accent: #4f46e5;
    --fs-accent-soft: #eef2ff;
    --fs-green: #059669;
    --fs-green-soft: #ecfdf5;
    --fs-amber: #d97706;
    --fs-amber-soft: #fffbeb;
    --fs-rose: #e11d48;
    --fs-rose-soft: #fff1f2;
    --fs-sky: #0284c7;
    --fs-sky-soft: #f0f9ff;

    --fs-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.06);
  }

  /* The stock top gap wastes a third of a laptop screen, but trimming it too far
     clipped the page title — the ascenders of a 1.6rem heading were cut by the
     container edge. Padding and an explicit line-height together, not either alone. */
  .block-container {
    padding-top: 3rem; padding-bottom: 4rem;
    max-width: 1180px;
  }

  h1, h2, h3 { letter-spacing: -0.015em; font-weight: 680; }
  h1 { font-size: 1.85rem !important; }

  /* Page chrome */
  .fs-head { display: flex; align-items: baseline; gap: 0.65rem; flex-wrap: wrap; }
  .fs-head-title {
    font-size: clamp(1.35rem, 1.1rem + 0.9vw, 1.75rem);
    font-weight: 700; letter-spacing: -0.02em;
    line-height: 1.35; display: inline-block; padding-block: 0.1rem;
    background: linear-gradient(95deg, var(--fs-accent), #7c3aed 45%, var(--fs-sky));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .fs-head-sub {
    color: var(--fs-muted); font-size: 0.93rem; margin: 0.3rem 0 0.1rem;
    max-width: 74ch; line-height: 1.55;
  }
  .fs-rule {
    height: 2px; border: 0; margin: 0.9rem 0 1.4rem; border-radius: 2px;
    background: linear-gradient(90deg, var(--fs-accent), #7c3aed 22%,
                                var(--fs-sky) 42%, transparent 72%);
    opacity: 0.55;
  }

  /* The empty-state hero, which only the Ask page uses. */
  .fs-hero {
    font-size: clamp(1.5rem, 1.1rem + 1.6vw, 2.2rem);
    font-weight: 700; letter-spacing: -0.03em; text-align: center;
    line-height: 1.3; margin: 0 0 1.4rem;
    background: linear-gradient(95deg, var(--fs-accent), #7c3aed 50%, var(--fs-sky));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .fs-hero-sub {
    text-align: center; color: var(--fs-muted); font-size: 0.93rem;
    margin: -0.9rem 0 1.5rem; line-height: 1.55;
  }

  /* ---- Sidebar ------------------------------------------------------------------
     The brand goes in Streamlit's own header slot via `st.logo`, so the slot is used
     rather than left as a band of empty space above the page list. */
  [data-testid="stSidebarHeader"] { padding-top: 0.6rem; padding-bottom: 0.2rem; }
  [data-testid="stSidebarLogo"] { height: 2.4rem; }
  [data-testid="stSidebarNav"] { padding-top: 0.2rem; }
  [data-testid="stSidebarUserContent"] { padding-top: 0.6rem; }

  /* A navigation item has to show which page you are on. Streamlit's default leaves
     every entry the same colour as the sidebar, so the current page is invisible. */
  [data-testid="stSidebarNavLink"] {
    border-radius: 10px; margin: 1px 0; transition: background 120ms ease;
  }
  [data-testid="stSidebarNavLink"]:hover { background: var(--fs-surface-strong); }
  [data-testid="stSidebarNavLink"][aria-current="page"] {
    background: var(--fs-accent-soft);
    box-shadow: inset 3px 0 0 var(--fs-accent);
  }
  [data-testid="stSidebarNavLink"][aria-current="page"] span,
  [data-testid="stSidebarNavLink"][aria-current="page"] svg {
    color: var(--fs-accent) !important; fill: var(--fs-accent) !important;
    font-weight: 650;
  }

  /* Evidence card.
     `white-space` is deliberately NOT `pre-wrap`. Measured: 79.9% of stored source
     elements contain a newline from the PDF's own line breaks, and honouring them put a
     hard break mid-sentence on nearly every passage — the "broken passages" a reader
     sees. The text is also whitespace-collapsed in Python before it arrives, so this is
     belt and braces. */
  .fs-passage {
    border: 1px solid var(--fs-border); border-left: 3px solid var(--fs-accent);
    border-radius: 12px; padding: 0.85rem 1rem; background: var(--fs-surface);
    font-size: 0.94rem; line-height: 1.68; white-space: normal;
    color: var(--fs-text);
  }
  .fs-meta { color: var(--fs-muted); font-size: 0.8rem; }
  .fs-crumb {
    color: var(--fs-accent); font-size: 0.78rem; font-weight: 600;
    letter-spacing: 0.03em; margin-bottom: 0.3rem;
  }

  /* Inline citation chip. `title` carries the cited span, so the browser shows it on
     hover without a click and without a custom component. */
  .fs-cite {
    display: inline-block; padding: 0.04rem 0.4rem; margin: 0 0.12rem;
    border-radius: 6px; background: var(--fs-accent-soft);
    border: 1px solid rgba(79, 70, 229, 0.35); color: var(--fs-accent);
    font-size: 0.72rem; font-weight: 700; vertical-align: 1px;
    font-variant-numeric: tabular-nums;
  }
  .fs-cite[title] { cursor: help; }
  .fs-cite[title]:hover {
    background: var(--fs-accent); color: #fff; border-color: var(--fs-accent);
  }

  /* The composed answer. Prose, not a list of rows: claims are joined into flowing
     paragraphs because a model's sentence boundaries are not a document structure, and
     rendering one block per claim made a seven-claim answer read as seven fragments.
     The measure is capped because a 1,400px line is unreadable regardless of content. */
  .fs-answer {
    font-size: 1.0rem; line-height: 1.78; max-width: 76ch;
    margin: 0.1rem 0 0.9rem;
  }
  .fs-answer + .fs-answer { margin-top: 0.1rem; }

  /* Issuer label above a paragraph, shown only when an answer spans more than one. */
  .fs-issuer {
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--fs-accent);
    margin: 0.7rem 0 0.15rem;
  }

  /* Monospace identifiers: ids are for comparing, so they need to align. */
  .fs-id {
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 0.76rem; color: var(--fs-muted);
  }

  /* Pipeline stage strip */
  .fs-stage {
    border: 1px solid var(--fs-border); border-radius: 10px; padding: 0.55rem 0.7rem;
    background: var(--fs-surface); font-size: 0.82rem; line-height: 1.35;
  }
  .fs-stage-done { border-color: var(--fs-green); background: var(--fs-green-soft); }
  .fs-stage-active { border-color: var(--fs-accent); background: var(--fs-accent-soft); }
  .fs-stage-label { font-weight: 660; display: block; }
  .fs-stage-note { color: var(--fs-muted); font-size: 0.74rem; }

  /* ---- Streamlit surfaces, reached through stable test ids ---------------------
     Generated class names are still avoided. Each hook below is one Streamlit's own
     testing API depends on, so an upgrade that moves it fails visibly rather than
     silently, and each is commented with what it is for. */

  /* Bordered containers are used as cards throughout; give them depth and a radius. */
  [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 14px;
  }

  /* Buttons: the starter chips and every action read as interactive rather than flat. */
  [data-testid="stBaseButton-secondary"] {
    border-radius: 999px; border: 1px solid var(--fs-border);
    background: var(--fs-bg); color: var(--fs-text);
    transition: border-color 120ms ease, background 120ms ease, transform 120ms ease;
  }
  [data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--fs-accent); background: var(--fs-accent-soft);
    color: var(--fs-accent); transform: translateY(-1px);
  }
  [data-testid="stBaseButton-primary"],
  [data-testid="stBaseButton-primaryFormSubmit"] {
    border-radius: 999px; border: 0;
    background: linear-gradient(95deg, var(--fs-accent), #7c3aed);
    box-shadow: var(--fs-shadow);
  }

  /* The question box. Rounded like every product of this shape, and with a border a
     reader can actually see — the default is near-white on white.
     **Exactly one element may carry the border.** Styling the outer test id alone drew a
     second rounded rectangle around Streamlit's own bordered wrapper, which read as two
     boxes stacked on top of each other, so the inner one is flattened here. */
  [data-testid="stChatInput"] {
    border-radius: 28px;
    border: 1.5px solid #cbd5e1;
    box-shadow: var(--fs-shadow);
    background: var(--fs-bg);
    transition: border-color 140ms ease, box-shadow 140ms ease;
    overflow: hidden;
  }
  [data-testid="stChatInput"] > div,
  [data-testid="stChatInput"] > div > div {
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
  }
  [data-testid="stChatInput"]:focus-within {
    border-color: var(--fs-accent);
    box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.12);
  }
  [data-testid="stChatInputSubmitButton"],
  [data-testid="stChatInputStopButton"] {
    border-radius: 999px; color: #fff;
    background: linear-gradient(95deg, var(--fs-accent), #7c3aed);
  }

  /* Jump to the newest turn. An anchor, not a script: `st.html` ignores JavaScript by
     default and enabling it to move a scrollbar would be a poor trade.

     Centred above the box rather than tucked into the right margin, and lifted clear of
     the bottom bar: at 7.5rem it sat behind the bar's own background and was sliced in
     half. The z-index puts it above that background rather than under it. */
  .fs-to-bottom {
    position: fixed; left: 50%; transform: translateX(-50%);
    bottom: 10.5rem; z-index: 1000;
    width: 2.1rem; height: 2.1rem; border-radius: 999px;
    display: grid; place-items: center; text-decoration: none;
    background: var(--fs-bg); color: var(--fs-accent);
    border: 1.5px solid #cbd5e1; box-shadow: var(--fs-shadow);
    font-size: 0.95rem; line-height: 1;
  }
  .fs-to-bottom:hover {
    border-color: var(--fs-accent); background: var(--fs-accent-soft);
  }

  /* The sidebar is 21rem wide by default and the button is centred on the viewport, so
     it drifts left of the content's own centre. Nudged back by half that. */
  @media (min-width: 992px) {
    .fs-to-bottom { margin-left: 10.5rem; }
  }

  /* Metrics read as a figure, not a form field. */
  [data-testid="stMetric"] {
    border-radius: 12px; background: var(--fs-surface);
    border: 1px solid var(--fs-border);
  }

  /* Make dataframes read as tables rather than spreadsheets. */
  [data-testid="stDataFrame"] { border-radius: 10px; }

  /* Narrow laptops: reclaim the side gutters rather than letting content crush. */
  @media (max-width: 1100px) {
    .block-container { padding-left: 1.6rem; padding-right: 1.6rem; }
  }
  @media (max-width: 720px) {
    .block-container { padding-top: 2rem; padding-left: 1rem; padding-right: 1rem; }
    .fs-answer { max-width: 100%; }
  }
</style>
"""


# Emitted only while a chat thread is empty, which is the whole mechanism: a CSS rule
# cannot look upwards for a state class, and setting one on <body> would need JavaScript
# that `st.html` ignores by default. Injecting the rule conditionally does the same job
# with nothing to undo — the next run without it simply does not emit it.
_OPENING_STYLESHEET: Final = """
<style>
  /* Un-pin the question box so it falls into the flow and can sit in the middle of the
     page, as every chat product does before there is a conversation above it. */
  [data-testid="stBottom"] { position: static; background: transparent; }
  [data-testid="stBottomBlockContainer"] { padding-top: 0; padding-bottom: 1rem; }
  [data-testid="stBottom"] > div { box-shadow: none; }
</style>
"""


def inject_theme() -> None:
    """Install the stylesheet. Safe to call once per script run."""
    st.html(_STYLESHEET)


def opening_layout() -> None:
    """Centre the question box for a thread with nothing in it yet.

    Called only while the thread is empty. See :data:`_OPENING_STYLESHEET`.
    """
    st.html(_OPENING_STYLESHEET)


def page_header(title: str, subtitle: str) -> None:
    """Render a page's title and one-line purpose.

    **No wiring badge.** Every page carried a Live/Preview chip, which was scaffolding for
    a frontend built ahead of its backend. The pages that remain are live, and a badge
    saying so on each of them is noise; the one page that is not says so in its own words,
    where a reader is actually reading.
    """
    st.html(
        f'<div class="fs-head"><span class="fs-head-title">{title}</span></div>'
        f'<p class="fs-head-sub">{subtitle}</p>'
        '<hr class="fs-rule" />'
    )


