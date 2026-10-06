"""Every page renders, and the wired ones stay honest about what they show.

``AppTest.from_function`` runs one view at a time, which is what makes this useful: a view
that raises is caught here rather than discovered as a blank panel during a demonstration.
All eight are exercised, including the preview pages, because a fixture page breaks just as
easily as a live one — a bad Material icon name or a mistyped column config raises at render
time and nothing else in the suite would notice.

**The script function is self-contained, and has to be.** ``AppTest.from_function`` uses the
function's *body* as the page script, so module globals and closure variables are not
available to it: everything it needs is imported inside it and everything else arrives
through ``kwargs``. Writing it as a closure looks correct and fails with ``NameError`` on
every view at once.
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import finsight.ui
from finsight.ui.client import ApiClient
from finsight.ui.views import ask, health

APP_SCRIPT = Path(finsight.ui.__file__).parent / "app.py"

VIEW_NAMES = (
    "ask",
    "trace",
    "library",
    "ingest",
    "ledger",
    "compare",
    "evaluation",
    "health",
)

PREVIEW_VIEWS = ("library", "ingest", "ledger", "compare", "evaluation")


def a_response(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": "credit risk",
        "candidates": [
            {
                "chunk_id": "6f1d8c4e-0000-4000-8000-000000000001",
                "rank": 1,
                "text": "The Company monitors credit risk through counterparty limits.",
                "heading_path": ["7. Risk factors", "7.2 Credit risk"],
                "page_numbers": [41, 42],
                "evidence_type": "narrative",
                "issuer_name": "Probe Limited",
                "fiscal_period": "FY2024-25",
                "fused_score": 0.0328,
                "rerank_score": -2.5,
                "contributions": {"bm25": 1, "dense": 3},
                "citations": [
                    {
                        "source_element_id": "11111111-1111-1111-1111-111111111111",
                        "locator": "p. 41",
                        "position": 0,
                    }
                ],
            }
        ],
        "degraded": [],
        "depth": 25,
        "reranked": True,
        "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "lexical_retriever": "bm25",
        "dense_used": True,
        "fusion_version": "1",
        "collapsed_count": 0,
        "elapsed_ms": 1900,
    }
    body.update(overrides)
    return body


def _script(view_name: str, response: "dict[str, Any]", fail: str) -> None:
    """The page script. Self-contained: no name here comes from the enclosing module.

    The ``response`` annotation is quoted because the ``def`` line is executed in the
    fresh namespace too, where ``Any`` is not bound. An unquoted annotation raises
    ``NameError`` before the body runs, which presents as every view failing at once.
    """
    from contextlib import ExitStack
    from unittest.mock import patch

    from finsight.ui.client import ApiClient, ApiError
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

    views = {
        "ask": ask.render,
        "trace": trace.render,
        "library": library.render,
        "ingest": ingest.render,
        "ledger": ledger.render,
        "compare": compare.render,
        "evaluation": evaluation.render,
        "health": health.render,
    }

    class Stub(ApiClient):
        def search(self, query: str, **kwargs: object) -> dict[str, object]:
            if fail:
                raise ApiError(fail)
            return response

        def is_ready(self) -> bool:
            return True

    stub = Stub(base_url="http://127.0.0.1:8000", token="t" * 40)

    # Patched in each *view's* namespace, not on finsight.ui.client. A view does
    # ``from finsight.ui.client import client_from_environment`` at import time and
    # holds its own binding, so replacing the attribute on the source module has no
    # effect once the view has been imported. Getting this wrong is quiet: the real
    # factory raises for a missing token, the view renders a configuration error, and
    # a test that only counts rendered elements still passes.
    modules = (ask, compare, evaluation, health, ingest, ledger, library, trace)
    with ExitStack() as stack:
        for module in modules:
            if hasattr(module, "client_from_environment"):
                stack.enter_context(
                    patch.object(module, "client_from_environment", return_value=stub)
                )
        views[view_name]()


def run(
    view_name: str,
    *,
    response: dict[str, Any] | None = None,
    fail: str = "",
    query: str = "",
    seeded: dict[str, Any] | None = None,
) -> AppTest:
    """Render one view, optionally seeding session state or running a search."""
    app = AppTest.from_function(
        _script,
        default_timeout=60,
        kwargs={
            "view_name": view_name,
            "response": response if response is not None else a_response(),
            "fail": fail,
        },
    )
    for key, value in (seeded or {}).items():
        app.session_state[key] = value
    app.run()
    if query:
        # Two runs: the Search button is disabled while the box is empty, and a browser
        # user cannot click a disabled button either.
        app.text_input[0].set_value(query).run()
        app.button[0].click().run()
    return app


def rendered_text(app: AppTest) -> str:
    """Everything a reader would see, across every element kind these pages use.

    ``app.get("html")`` is included and matters: the passage card, the heading
    breadcrumb and the pipeline strips are all :func:`streamlit.html`, so a helper that
    scanned only markdown and captions would miss the actual evidence text — and would
    make the "no real issuer in a preview" check pass by looking in the wrong place.

    Tables are included for the same reason: the library and ledger previews put their
    issuer names in a dataframe, not in prose.
    """
    parts = [
        str(element.value)
        for group in (app.markdown, app.caption, app.info, app.warning, app.error)
        for element in group
    ]
    parts.extend(str(element.value) for element in app.get("html"))
    parts.extend(str(element.value) for element in app.dataframe)
    return " ".join(parts)


class _ShellStub(ApiClient):
    """Minimal client for the shell test, which only needs the default page to render."""

    def search(self, query: str, **kwargs: object) -> dict[str, object]:
        return dict(a_response())

    def is_ready(self) -> bool:
        return True


class TestAppShell:
    """The entry point itself, which the per-view tests do not cover.

    Running each view in isolation says nothing about ``st.navigation``, and that gap let
    a real failure ship: every view exposes ``render``, ``st.Page`` infers a URL pathname
    from the callable's name when none is given, and all eight therefore collided and
    ``st.navigation`` rejected the whole set. The page was blank on every route while all
    eighty per-view assertions passed.
    """

    def test_the_shell_builds_its_navigation(self) -> None:
        stub = _ShellStub(base_url="http://127.0.0.1:8000", token="t" * 40)
        app = AppTest.from_file(str(APP_SCRIPT), default_timeout=60)

        with (
            patch.object(ask, "client_from_environment", return_value=stub),
            patch.object(health, "client_from_environment", return_value=stub),
        ):
            app.run()

        assert not app.exception, f"shell raised: {[e.value for e in app.exception]}"

    def test_every_page_declares_a_distinct_url_path(self) -> None:
        """Pinned directly, so the cause is named rather than inferred from a blank page."""
        from finsight.ui.app import _PAGES

        slugs = [slug for _render, slug, *_rest in _PAGES]

        assert len(slugs) == len(set(slugs)), f"duplicate url_path: {slugs}"
        assert all(slugs), "a blank url_path falls back to the callable name"

    def test_the_shell_renders_the_default_page(self) -> None:
        stub = _ShellStub(base_url="http://127.0.0.1:8000", token="t" * 40)
        app = AppTest.from_file(str(APP_SCRIPT), default_timeout=60)

        with (
            patch.object(ask, "client_from_environment", return_value=stub),
            patch.object(health, "client_from_environment", return_value=stub),
        ):
            app.run()

        assert "Ask" in rendered_text(app)


class TestEveryPageRenders:
    @pytest.mark.parametrize("name", VIEW_NAMES)
    def test_the_view_renders_without_raising(self, name: str) -> None:
        """Catches a bad Material icon name or column config before a demonstration."""
        app = run(name)

        assert not app.exception, f"{name} raised: {[e.value for e in app.exception]}"

    @pytest.mark.parametrize("name", VIEW_NAMES)
    def test_the_view_renders_something(self, name: str) -> None:
        """A page that renders nothing passes an exception check and is still broken."""
        app = run(name)

        produced = len(app.markdown) + len(app.caption) + len(app.metric) + len(app.dataframe)
        assert produced > 0, f"{name} rendered no content"

    @pytest.mark.parametrize("name", VIEW_NAMES)
    def test_the_view_renders_no_error(self, name: str) -> None:
        """Counting elements is not enough: an error page renders content too.

        Added after a mis-targeted patch left Ask showing a configuration error on every
        run while the two checks above both passed.
        """
        app = run(name)

        assert not app.error, f"{name} rendered: {[e.value for e in app.error]}"


class TestAsk:
    def test_a_search_renders_the_passage_and_its_provenance(self) -> None:
        app = run("ask", query="credit risk")

        assert not app.exception
        text = rendered_text(app)
        assert "counterparty limits" in text
        assert "7.2 Credit risk" in text

    def test_the_rerank_score_is_labelled_a_logit_not_a_confidence(self) -> None:
        """§27: a support signal is not a probability that the passage is correct."""
        app = run("ask", query="credit risk")

        helps = " ".join(str(element.help or "") for element in app.metric)
        assert "logit" in helps.lower()

    def test_an_api_failure_leaves_the_page_usable(self) -> None:
        app = run("ask", fail="Could not reach the API", query="x")

        assert not app.exception
        assert any("Could not reach the API" in error.value for error in app.error)

    def test_an_empty_result_explains_why_rather_than_looking_broken(self) -> None:
        app = run("ask", response=a_response(candidates=[]), query="photosynthesis")

        info = " ".join(element.value for element in app.info)
        assert "Nothing matched" in info
        assert "ADR-003" in info

    def test_a_degraded_result_warns_and_explains_the_cost(self) -> None:
        """§20.12: flagged in words a reader can act on, not just a flag name."""
        app = run(
            "ask",
            response=a_response(degraded=["dense_unavailable"], dense_used=False),
            query="credit risk",
        )

        warnings = " ".join(element.value for element in app.warning)
        assert "dense_unavailable" in warnings
        assert "lexical only" in warnings

    def test_the_removed_disclaimer_line_is_gone(self) -> None:
        """Removed at the developer's request; they explain it in the room instead."""
        app = run("ask")

        assert "not answers" not in rendered_text(app)


class TestTrace:
    def test_without_a_query_it_says_so_rather_than_erroring(self) -> None:
        app = run("trace")

        assert not app.exception
        assert any("No query in this session" in element.value for element in app.info)

    def test_with_a_query_it_shows_the_reordering(self) -> None:
        app = run("trace", seeded={ask.RESULT_KEY: a_response()})

        assert not app.exception
        assert len(app.dataframe) >= 1


class TestHonesty:
    @pytest.mark.parametrize("name", VIEW_NAMES)
    def test_no_internal_commentary_is_rendered_to_the_reader(self, name: str) -> None:
        """Streamlit's magic renders bare top-level string literals as page content.

        An attribute docstring in a page module is therefore published, which is how a
        paragraph written for a maintainer ended up on the page. Pinned because the
        mistake is invisible in review: the source looks like ordinary documentation.
        """
        text = rendered_text(run(name))

        for giveaway in ("stable identifiers", "CLAUDE.md", "attribute docstring"):
            assert giveaway not in text, f"{name} published maintainer text"

    @pytest.mark.parametrize("name", PREVIEW_VIEWS)
    def test_a_preview_page_says_its_data_is_a_fixture(self, name: str) -> None:
        """The one genuinely misleading option here would be an unlabelled fixture."""
        captions = " ".join(element.value for element in run(name).caption)

        assert "not yet wired" in captions, f"{name} does not disclose its fixtures"

    @pytest.mark.parametrize("name", PREVIEW_VIEWS)
    def test_no_preview_page_names_a_real_issuer(self, name: str) -> None:
        """A fabricated figure beside a real company's name is a fabricated record.

        It stays fabricated once it is screenshotted out of context, which is why the
        fixtures invent the issuer as well as the numbers.
        """
        text = rendered_text(run(name))

        for real in ("Infosys", "HDFC", "Reliance", "TCS"):
            assert real not in text, f"{name} names {real} beside fixture figures"
