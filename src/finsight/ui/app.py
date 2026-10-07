"""FinSight's frontend: navigation shell and sidebar chrome.

Retrieval and the query trace answer from the indexed corpus. The remaining pages show the
shape of capabilities that are specified but not yet wired, each marked with a badge saying
so, because a reader needs to know which numbers came from a filing and which are fixtures.
The sidebar carries the same legend, so the distinction is available without visiting a page.

Entry point only: each page's content lives in :mod:`finsight.ui.views`, one ``render()``
per module, which is what lets a page be rendered in isolation by the tests.
"""

from typing import Final

import streamlit as st

from finsight.ui.theme import inject_theme
from finsight.ui.views import ask, health, ingest, library, trace

PAGE_TITLE: Final = "FinSight"

# Page order is the order a reader meets the system in: ask something, see why it answered
# that way, then the corpus behind it, then how it is evaluated and operated.
#
# **The url_path is explicit and must stay that way.** st.Page infers a pathname from the
# callable's name when none is given, and every view here exposes ``render`` by design, so
# all eight infer the same path and st.navigation refuses the whole set. Naming the slug is
# also what keeps a page's address stable if its title is ever reworded.
# **Fact Ledger, Compare and Evaluation were removed**, not hidden. Each showed the
# interface for a capability with no implementation behind it at all — no `ledger`
# package, no `evaluation` package, and Compare depends on the Ledger — so they were
# further from working than any other page by a wide margin, and a page that cannot be
# finished in the foreseeable future is a promise rather than a preview. The pages that
# remain each have their backend either built or one route away.
_PAGES: Final = (
    (ask.render, "ask", "Ask", ":material/search:"),
    (trace.render, "trace", "Query trace", ":material/account_tree:"),
    (library.render, "library", "Library", ":material/folder_open:"),
    (ingest.render, "ingest", "Ingest", ":material/upload_file:"),
    (health.render, "system", "System", ":material/monitor_heart:"),
)


def main() -> None:
    """Build the shell and run the selected page."""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=":material/query_stats:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_theme()

    pages = [
        st.Page(render, title=title, icon=icon, url_path=slug, default=index == 0)
        for index, (render, slug, title, icon) in enumerate(_PAGES)
    ]
    selected = st.navigation(pages, position="sidebar")

    _sidebar_chrome()
    selected.run()


# The brand, drawn as an SVG and passed to `st.logo` so it lands in the sidebar's own
# header slot — above the navigation, where every product of this shape puts it.
#
# Rendering it as sidebar *content* put it below the page list and left Streamlit's empty
# header slot as a band of blank space at the top. A data URI rather than a file because a
# wordmark is six shapes and a second asset to keep in step is not worth it.
_BRAND_SVG: Final = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 188 34'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='%234f46e5'/><stop offset='1' stop-color='%237c3aed'/>"
    "</linearGradient></defs>"
    "<rect x='0' y='3' width='28' height='28' rx='8' fill='url(%23g)'/>"
    "<text x='14' y='22.5' font-family='system-ui,sans-serif' font-size='16'"
    " font-weight='700' fill='white' text-anchor='middle'>F</text>"
    "<text x='36' y='17' font-family='system-ui,sans-serif' font-size='15'"
    " font-weight='700' fill='%230f172a'>FinSight</text>"
    "<text x='36' y='28' font-family='system-ui,sans-serif' font-size='8.5'"
    " fill='%2364748b'>Evidence-grounded filing analysis</text>"
    "</svg>"
)


def _sidebar_chrome() -> None:
    """Put the brand in the sidebar's header slot, above the navigation.

    The "What is wired" legend and the self-hosting note were removed at the developer's
    request. Neither claim is lost: every page still carries its own wiring badge, which
    is where a reader meets the distinction anyway, and the badge's tooltip says what the
    state means. The sidebar is a navigation surface, not a place to read paragraphs.
    """
    st.logo(_BRAND_SVG, size="large")


if __name__ == "__main__":
    # Streamlit executes the entry script as ``__main__``, so this runs under
    # ``streamlit run`` while importing the module stays side-effect free — which is what
    # lets the tests examine it without rendering a page.
    main()
