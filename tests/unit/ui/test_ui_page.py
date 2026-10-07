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


def an_answer(**overrides: Any) -> dict[str, Any]:
    """A released answer as ``POST /v1/ask`` returns one.

    ``cited_passage_ids`` is distinct from ``citations`` on purpose, and the fixture keeps
    them inconsistent in the way the real route is: two citations naming one passage. A
    fixture with one citation per passage would let a view that marked every citation pass,
    and that view renders ``[1][1]`` against the live API.
    """
    body: dict[str, Any] = {
        "question": "credit risk",
        "decision": "partial",
        "support_band": "weak",
        "reason_codes": ["unsupported_numeral"],
        "degraded": [],
        "claims": [
            {
                "text": "The Company monitors credit risk through counterparty limits.",
                "cited_passage_ids": [1],
                "citations": [
                    {
                        "passage_id": 1,
                        "source_element_id": "11111111-1111-1111-1111-111111111111",
                        "locator": "p. 41",
                        "text": "Credit risk is monitored through counterparty limits.",
                    },
                    {
                        "passage_id": 1,
                        "source_element_id": "11111111-1111-1111-1111-111111111112",
                        "locator": "p. 41",
                        "text": "Limits are reviewed annually.",
                    },
                ],
                "disclosures": [],
            }
        ],
        "withheld": [
            {
                "text": "Revenue was 99,999 crore.",
                "findings": [
                    {
                        "code": "unsupported_numeral",
                        "severity": "remove",
                        "detail": "states 99,999, which appears in none of its cited spans",
                    }
                ],
            }
        ],
        "passages": [
            {
                "id": 1,
                "chunk_id": "6f1d8c4e-0000-4000-8000-000000000001",
                "text": "The Company monitors credit risk through counterparty limits.",
                "heading_path": ["7. Risk factors"],
                "page_numbers": [41],
                "evidence_type": "narrative",
                "issuer_name": "Probe Limited",
                "fiscal_period": "FY2024-25",
                "reporting_basis": "consolidated",
                "rerank_score": -2.5,
                "expanded": False,
                "stands_for": [],
            }
        ],
        "model": "llama3.1:8b",
        "answer_id": "33333333-3333-3333-3333-333333333333",
        "evidence_budget_chars": 22000,
        "evidence_used_chars": 6362,
        "passages_considered": 4,
        "passages_dropped_for_budget": 0,
        "passages_merged": 0,
        "passages_expanded": 0,
        "prompt_tokens": 1971,
        "completion_tokens": 437,
        "timings_ms": {"retrieval_ms": 1500, "generation_ms": 287185, "total_ms": 302351},
    }
    body.update(overrides)
    return body


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


def _script(
    view_name: str,
    response: "dict[str, Any]",
    fail: str,
    answer: "dict[str, Any] | None" = None,
    ask_fail: str = "",
) -> None:
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

        def ask(self, question: str, **kwargs: object) -> dict[str, object]:
            if ask_fail:
                raise ApiError(ask_fail)
            if answer is None:
                raise ApiError("no answer fixture supplied to this run")
            return answer

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
    answer: dict[str, Any] | None = None,
    ask_fail: str = "",
) -> AppTest:
    """Render one view, optionally seeding session state or asking a question."""
    app = AppTest.from_function(
        _script,
        default_timeout=60,
        kwargs={
            "view_name": view_name,
            "response": response if response is not None else a_response(),
            "fail": fail,
            "answer": answer if answer is not None else an_answer(),
            "ask_fail": ask_fail,
        },
    )
    for key, value in (seeded or {}).items():
        app.session_state[key] = value
    app.run()
    if query:
        # Two runs: the Ask button is disabled while the box is empty, and a browser
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

    Expander *labels* are included too, and they are not reachable any other way: an
    expander's children appear in the element groups above but its own label does not, so a
    heading like "Withheld by the Evidence Gate (1)" — the one thing telling a reader that
    something was removed at all — was invisible to this helper until it was added.
    """
    parts = [
        str(element.value)
        for group in (app.markdown, app.caption, app.info, app.warning, app.error)
        for element in group
    ]
    parts.extend(str(element.value) for element in app.get("html"))
    parts.extend(str(element.value) for element in app.dataframe)
    parts.extend(str(element.label) for element in app.expander)
    parts.extend(str(element.label) for element in app.status)
    return " ".join(parts)


class _ShellStub(ApiClient):
    """Minimal client for the shell test, which only needs the default page to render."""

    def search(self, query: str, **kwargs: object) -> dict[str, object]:
        return dict(a_response())

    def ask(self, question: str, **kwargs: object) -> dict[str, object]:
        return dict(an_answer())

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


class TestTheAnswerPanel:
    """The composed answer, which replaced a fixture panel in this phase.

    These assert the three things a reader cannot verify for themselves: that the decision is
    stated rather than implied by whether text appeared, that the Gate's removals are visible,
    and that a citation mark leads to the stored span rather than being decoration.
    """

    def test_a_released_claim_and_its_decision_are_shown(self) -> None:
        app = run("ask", query="credit risk")

        assert not app.exception
        text = rendered_text(app)
        assert "Composed answer" in text
        assert "counterparty limits" in text
        # Streamlit renders a badge as markdown directive text rather than its own
        # element kind, so the decision is asserted where a reader would see it.
        assert "orange-badge" in text and "Partial" in text

    def test_the_support_band_is_not_presented_as_a_probability(self) -> None:
        """§27.13. A band beside a percentage would be read as one."""
        text = rendered_text(run("ask", query="credit risk"))

        assert "not a probability of correctness" in text

    def test_a_withheld_claim_is_shown_with_its_reason_in_plain_words(self) -> None:
        """An invisible removal is indistinguishable from a model that never said it."""
        text = rendered_text(run("ask", query="credit risk"))

        assert "Withheld by the Evidence Gate (1)" in text
        assert "99,999" in text
        assert "appears in none of the spans" in text

    def test_a_citation_mark_is_rendered_once_per_passage_not_once_per_span(self) -> None:
        """The claim cites one passage through two source elements.

        Marking each citation would render ``[1][1]``, which is what the live route
        produced before the contract exposed the distinct ids. Pinned because the fixture
        deliberately keeps the two counts different.
        """
        html = " ".join(str(element.value) for element in run("ask", query="x").get("html"))

        assert html.count('<span class="fs-cite">1</span>') == 1

    def test_the_cited_span_text_is_available_to_the_reader(self) -> None:
        """A citation a reader cannot follow is a reference, not evidence (§14.9)."""
        text = rendered_text(run("ask", query="credit risk"))

        assert "Limits are reviewed annually." in text

    def test_an_abstention_says_why_in_words(self) -> None:
        app = run(
            "ask",
            query="what is the chief executive's pay",
            answer=an_answer(
                decision="abstained",
                support_band="none",
                reason_codes=["model_reported_unanswerable"],
                claims=[],
                withheld=[],
            ),
        )

        info = " ".join(element.value for element in app.info)
        assert "No claim was released" in info
        assert "do not answer the question" in info

    def test_a_lost_model_warns_and_keeps_the_evidence(self) -> None:
        """§26.10: the passages are still worth reading when no prose was composed."""
        app = run(
            "ask",
            query="credit risk",
            answer=an_answer(
                decision="abstained",
                support_band="none",
                reason_codes=[],
                claims=[],
                withheld=[],
                degraded=["generation_unavailable"],
            ),
        )

        warnings = " ".join(element.value for element in app.warning)
        assert "generation_unavailable" in warnings
        assert "passages below were still retrieved" in warnings
        assert "counterparty limits" in rendered_text(app)

    def test_a_failed_ask_leaves_the_retrieved_evidence_on_the_page(self) -> None:
        """The whole point of two requests: losing the answer must not lose the evidence."""
        app = run("ask", query="credit risk", ask_fail="The API returned 503")

        assert not app.exception
        assert any("503" in error.value for error in app.error)
        assert "counterparty limits" in rendered_text(app)

    def test_reranking_off_discloses_that_the_answer_used_a_different_order(self) -> None:
        """POST /v1/ask carries no rerank field, so the answer always uses reranked order.

        Without this the page shows a fusion-ordered evidence list beside an answer built
        from a reranked one, and a reader would reasonably assume the answer came from what
        they can see.
        """
        app = run(
            "ask",
            query="credit risk",
            response=a_response(reranked=False, rerank_score=None),
        )

        info = " ".join(element.value for element in app.info)
        assert "Reranking is switched off" in info
        assert "the two lists may differ" in info

    def test_an_unavailable_reranker_is_not_reported_as_a_user_choice(self) -> None:
        """Both leave 'reranked' false; only one is something the reader did."""
        app = run(
            "ask",
            query="credit risk",
            response=a_response(reranked=False, degraded=["reranker_unavailable"]),
        )

        info = " ".join(element.value for element in app.info)
        assert "Reranking is switched off" not in info

    def test_the_wait_is_stated_before_it_is_endured(self) -> None:
        """Two to four minutes with no warning reads as a hung page."""
        text = rendered_text(run("ask", query="credit risk"))

        assert "Two to four minutes" in text


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
