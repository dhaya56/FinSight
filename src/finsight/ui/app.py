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

from finsight.ui.theme import Wiring, inject_theme
from finsight.ui.views import (
    ask,
    compare,
    evaluation,
    health,
    ingest,
    ledger,
    library,
    trace,
)

PAGE_TITLE: Final = "FinSight"

# Page order is the order a reader meets the system in: ask something, see why it answered
# that way, then the corpus behind it, then how it is evaluated and operated.
#
# **The url_path is explicit and must stay that way.** st.Page infers a pathname from the
# callable's name when none is given, and every view here exposes ``render`` by design, so
# all eight infer the same path and st.navigation refuses the whole set. Naming the slug is
# also what keeps a page's address stable if its title is ever reworded.
_PAGES: Final = (
    (ask.render, "ask", "Ask", ":material/search:", Wiring.PARTIAL),
    (trace.render, "trace", "Query trace", ":material/account_tree:", Wiring.PARTIAL),
    (library.render, "library", "Library", ":material/folder_open:", Wiring.PREVIEW),
    (ingest.render, "ingest", "Ingest", ":material/upload_file:", Wiring.PREVIEW),
    (ledger.render, "ledger", "Fact Ledger", ":material/table_chart:", Wiring.PREVIEW),
    (compare.render, "compare", "Compare", ":material/compare_arrows:", Wiring.PREVIEW),
    (
        evaluation.render,
        "evaluation",
        "Evaluation",
        ":material/science:",
        Wiring.PREVIEW,
    ),
    (health.render, "system", "System", ":material/monitor_heart:", Wiring.LIVE),
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
        for index, (render, slug, title, icon, _wiring) in enumerate(_PAGES)
    ]
    selected = st.navigation(pages, position="sidebar")

    _sidebar_chrome()
    selected.run()


def _sidebar_chrome() -> None:
    """Brand block above the navigation, legend and build note below it."""
    with st.sidebar:
        st.html(
            '<div class="fs-brand">'
            '<div class="fs-brand-mark">F</div>'
            '<div><div class="fs-brand-name">FinSight</div>'
            '<div class="fs-brand-sub">Evidence-grounded filing analysis</div></div>'
            "</div>"
        )

        st.container(height=1, border=False)
        with st.expander("What is wired", icon=":material/info:"):
            st.caption(
                "Retrieval over the real corpus is live, and so is the trace that explains "
                "it. The other pages show the interface for capabilities that are "
                "specified but not yet connected."
            )
            for wiring, pages in (
                (Wiring.LIVE, "System"),
                (Wiring.PARTIAL, "Ask, Query trace"),
                (Wiring.PREVIEW, "Library, Ingest, Fact Ledger, Compare, Evaluation"),
            ):
                st.markdown(f"**{wiring.value}** \N{EM DASH} {pages}")

        st.caption(
            "Self-hosted. No filing, question or figure leaves this machine, and no "
            "third-party inference service is used."
        )


if __name__ == "__main__":
    # Streamlit executes the entry script as ``__main__``, so this runs under
    # ``streamlit run`` while importing the module stays side-effect free — which is what
    # lets the tests examine it without rendering a page.
    main()
