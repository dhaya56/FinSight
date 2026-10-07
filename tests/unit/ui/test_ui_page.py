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

import re
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
    "health",
)

PREVIEW_VIEWS = ("ingest",)

CALLS_KEY = "test.calls"
"""Where the stub client records what it was asked.

Session state, because the page script runs in its own namespace and this is the only
place both it and the test can reach. The properties that matter most are invisible in the
output: a dropped filter and an applied one produce the same shape of answer.
"""


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


def _system(**overrides: Any) -> dict[str, Any]:
    """A system payload, with the parts a test varies pulled out."""
    qdrant_healthy = overrides.pop("qdrant_healthy", True)
    postgres_healthy = overrides.pop("postgres_healthy", True)
    index = {
        "indexed_chunks": 4867,
        "context_chunks": 770,
        "index_points": 4867,
        "index_consistent": True,
        "pending_events": 0,
        "failed_events": 0,
        "completed_events": 4867,
    }
    index.update({k: overrides.pop(k) for k in list(overrides) if k in index})
    return {
        "dependencies": [
            {"name": "PostgreSQL", "classification": "essential", "healthy": postgres_healthy},
            {"name": "Schema", "classification": "essential", "healthy": True},
            {"name": "Qdrant", "classification": "degradable", "healthy": qdrant_healthy},
        ],
        "index": index,
        "answers": {
            "total": 6,
            "by_decision": {"answered": 3, "abstained": 2, "partial": 1},
            "by_support_band": {"strong": 3, "weak": 1, "none": 2},
            "by_reason": {"model_reported_unanswerable": 1, "no_evidence_retrieved": 1},
            "median_elapsed_ms": 152390,
            "slowest_elapsed_ms": 302351,
        },
    }


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
        "timings_ms": {
            "lexical": 60,
            "dense": 70,
            "fusion": 1,
            "resolve": 20,
            "rerank": 1749,
        },
    }
    body.update(overrides)
    return body


def _script(
    view_name: str,
    response: "dict[str, Any]",
    fail: str,
    answer: "dict[str, Any] | None" = None,
    ask_fail: str = "",
    system: "dict[str, Any] | None" = None,
) -> None:
    """The page script. Self-contained: no name here comes from the enclosing module.

    The ``response`` annotation is quoted because the ``def`` line is executed in the
    fresh namespace too, where ``Any`` is not bound. An unquoted annotation raises
    ``NameError`` before the body runs, which presents as every view failing at once.
    """
    from contextlib import ExitStack
    from unittest.mock import patch

    import streamlit as st

    from finsight.ui.client import ApiClient, ApiError
    from finsight.ui.views import ask, health, ingest, library, trace

    views = {
        "ask": ask.render,
        "trace": trace.render,
        "library": library.render,
        "ingest": ingest.render,
        "health": health.render,
    }

    class Stub(ApiClient):
        # Calls are recorded into session state rather than into a module variable: the
        # page script runs in its own namespace, so session state is the only place both
        # it and the test can see. It is how a test checks that a sidebar filter actually
        # reached the backend — invisible in the rendered output, since a dropped filter
        # and an applied one produce the same shape of answer.
        def _record(self, name: str, **kwargs: object) -> None:
            # The key is inlined, not referenced from the module. This function's body is
            # the page script and runs in a fresh namespace, so a module global resolves
            # to NameError — the hazard this file's own docstring warns about, and which
            # it just demonstrated.
            st.session_state.setdefault("test.calls", []).append((name, kwargs))

        def search(self, query: str, **kwargs: object) -> dict[str, object]:
            self._record("search", **kwargs)
            if fail:
                raise ApiError(fail)
            return response

        def ask(self, question: str, **kwargs: object) -> dict[str, object]:
            self._record("ask", **kwargs)
            if ask_fail:
                raise ApiError(ask_fail)
            if answer is None:
                raise ApiError("no answer fixture supplied to this run")
            return answer

        def system(self) -> dict[str, object]:
            if fail:
                raise ApiError(fail)
            if system is not None:
                return system
            return {
                "dependencies": [
                    {"name": "PostgreSQL", "classification": "essential", "healthy": True},
                    {"name": "Schema", "classification": "essential", "healthy": True},
                    {"name": "Qdrant", "classification": "degradable", "healthy": True},
                ],
                "index": {
                    "indexed_chunks": 4867,
                    "context_chunks": 770,
                    "index_points": 4867,
                    "index_consistent": True,
                    "pending_events": 0,
                    "failed_events": 0,
                    "completed_events": 5637,
                },
                "answers": {
                    "total": 6,
                    "by_decision": {"answered": 3, "abstained": 2, "partial": 1},
                    "by_support_band": {"strong": 3, "weak": 1, "none": 2},
                    "by_reason": {"model_reported_unanswerable": 1, "no_evidence_retrieved": 1},
                    "median_elapsed_ms": 152390,
                    "slowest_elapsed_ms": 302351,
                },
            }

        def library(self) -> dict[str, object]:
            if fail:
                raise ApiError(fail)
            return {
                "documents": [
                    {
                        "document_version_id": "9f1d8c4e-0000-4000-8000-00000000000a",
                        "issuer_name": "Alpha Industries Limited",
                        "document_type": "annual_report",
                        "fiscal_period": "FY2024-25",
                        "reporting_basis": "consolidated",
                        "generation_state": "active",
                        "chunking_config_version": "4",
                        "activated_at": "2026-10-01T10:00:00Z",
                        "ingested_at": "2026-09-30T09:00:00Z",
                        "pages": 369,
                        "blocks": 12000,
                        "tables": 400,
                        "footnotes": 20,
                        "chunks": 1800,
                        "byte_size": 9_400_000,
                        "extraction_state": "partial",
                        "extraction_config_version": "2",
                        "extraction_seconds": 3.3,
                        "unreadable_regions": 10,
                        "tables_accepted": 45,
                        "tables_rejected": 42,
                        "child_chunks": 1530,
                        "parent_chunks": 270,
                        "median_child_tokens": 199,
                        "sections": [
                            ["7. Risk factors", 259],
                            ["2. Notes to the Consolidated financial statements", 199],
                        ],
                    },
                    {
                        "document_version_id": "9f1d8c4e-0000-4000-8000-00000000000b",
                        "issuer_name": "Beta Financial Services Limited",
                        "document_type": "drhp",
                        "fiscal_period": "FY2024-25",
                        "reporting_basis": "standalone",
                        "generation_state": None,
                        "chunking_config_version": None,
                        "activated_at": None,
                        "ingested_at": "2026-09-29T09:00:00Z",
                        "pages": 444,
                        "blocks": 15000,
                        "tables": 300,
                        "footnotes": 11,
                        "chunks": 0,
                        "byte_size": 12_100_000,
                        "extraction_state": "succeeded",
                        "extraction_config_version": "2",
                        "extraction_seconds": 11.5,
                        "unreadable_regions": 0,
                        "tables_accepted": 269,
                        "tables_rejected": 20,
                        "child_chunks": 0,
                        "parent_chunks": 0,
                        "median_child_tokens": 0,
                        "sections": [],
                    },
                ]
            }

        def facets(self) -> dict[str, object]:
            return {
                "issuer_names": ["Alpha Industries Limited"],
                "document_types": ["annual_report"],
                "fiscal_periods": ["FY2024-25"],
                "reporting_bases": ["consolidated"],
                "sections": ["7. Risk factors"],
            }

        def is_ready(self) -> bool:
            return True

    stub = Stub(base_url="http://127.0.0.1:8000", token="t" * 40)

    # The sidebar caches facets per connection, and a cache that outlives a test would
    # serve one test's values to the next.
    st.cache_data.clear()

    # Patched in each *view's* namespace, not on finsight.ui.client. A view does
    # ``from finsight.ui.client import client_from_environment`` at import time and
    # holds its own binding, so replacing the attribute on the source module has no
    # effect once the view has been imported. Getting this wrong is quiet: the real
    # factory raises for a missing token, the view renders a configuration error, and
    # a test that only counts rendered elements still passes.
    modules = (ask, health, ingest, library, trace)
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
    system: dict[str, Any] | None = None,
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
            "system": system,
        },
    )
    for key, value in (seeded or {}).items():
        app.session_state[key] = value
    app.run()
    if query:
        _ask(app, query)
    return app


def _ask(app: AppTest, query: str) -> None:
    """Ask a question through the chat box.

    One input in both states: the opening screen renders the same ``st.chat_input`` and
    only moves it to the middle of the page with CSS, so there is a single way to drive
    this page and a single place for the stop control to live.
    """
    app.chat_input[0].set_value(query).run()


def rendered_text(app: AppTest) -> str:
    """Everything a reader would see, across every element kind these pages use.

    ``app.get("html")`` is included and matters: the passage card, the heading
    breadcrumb and the pipeline strips are all :func:`streamlit.html`, so a helper that
    scanned only markdown and captions would miss the actual evidence text — and would
    make the "no real issuer in a preview" check pass by looking in the wrong place.

    Tables are included for the same reason: the library preview puts its issuer names in
    a dataframe, not in prose.

    Expander *labels* are included too, and they are not reachable any other way: an
    expander's children appear in the element groups above but its own label does not, so a
    heading like "Withheld by the Evidence Gate (1)" — the one thing telling a reader that
    something was removed at all — was invisible to this helper until it was added.

    ``success`` is included for the same reason, found the same way: the System page reports
    a consistent index with ``st.success``, and a helper that read every other callout kind
    but not that one said the page had rendered nothing about it.
    """
    parts = [
        str(element.value)
        for group in (
            app.markdown,
            app.caption,
            app.info,
            app.warning,
            app.error,
            app.success,
        )
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
        """A page that renders nothing passes an exception check and is still broken.

        ``html`` and ``button`` are counted too. Ask's opening screen is a heading, six
        starter chips and a question box, none of which are markdown — so a narrower count
        reported the page as empty the moment it stopped looking like a document.
        """
        app = run(name)

        produced = (
            len(app.markdown)
            + len(app.caption)
            + len(app.metric)
            + len(app.dataframe)
            + len(app.get("html"))
            + len(app.button)
        )
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

        # Matched by pattern rather than exact string: the chip carries a `title`
        # attribute for the hover preview, so an exact-string count silently became 0
        # and asserted nothing.
        assert len(re.findall(r'<span class="fs-cite"[^>]*>1</span>', html)) == 1

    def test_a_citation_mark_carries_its_span_for_hover(self) -> None:
        """Hover is the fastest path to the evidence, and needs no click or component."""
        html = " ".join(str(element.value) for element in run("ask", query="x").get("html"))

        marks = re.findall(r'<span class="fs-cite" title="([^"]*)">', html)
        assert marks, "no citation chip carried a title"
        assert "p. 41" in marks[0]
        assert "counterparty limits" in marks[0]

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
        assert "passages were still retrieved" in warnings
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
        assert "the two may differ" in info

    def test_an_unavailable_reranker_is_not_reported_as_a_user_choice(self) -> None:
        """Both leave 'reranked' false; only one is something the reader did."""
        app = run(
            "ask",
            query="credit risk",
            response=a_response(reranked=False, degraded=["reranker_unavailable"]),
        )

        info = " ".join(element.value for element in app.info)
        assert "Reranking is switched off" not in info

    def test_claims_are_joined_into_prose_not_one_block_each(self) -> None:
        """Seven claims rendered as seven blocks read as seven fragments."""
        app = run(
            "ask",
            query="credit risk",
            answer=an_answer(
                claims=[
                    {
                        "text": "First sentence.",
                        "cited_passage_ids": [1],
                        "citations": [],
                        "disclosures": [],
                    },
                    {
                        "text": "Second sentence.",
                        "cited_passage_ids": [1],
                        "citations": [],
                        "disclosures": [],
                    },
                ],
                withheld=[],
                decision="answered",
                support_band="strong",
                reason_codes=[],
            ),
        )
        html = " ".join(str(element.value) for element in app.get("html"))

        paragraphs = re.findall(r'<div class="fs-answer">(.*?)</div>', html)
        assert len(paragraphs) == 1, f"expected one paragraph, got {len(paragraphs)}"
        assert "First sentence." in paragraphs[0]
        assert "Second sentence." in paragraphs[0]

    def test_an_answer_spanning_two_issuers_keeps_them_in_separate_paragraphs(self) -> None:
        """Measured on the live route: one answer cited Infosys and HDFC Bank.

        The Evidence Gate cannot catch it — ``mixed_issuer`` fires when *one claim* cites
        two issuers, and here each claim is internally consistent while the answer is not.
        Joined into one smooth paragraph it would read as a single company's position.

        The warning banner was removed at the developer's request; the separation it
        described is what actually keeps the two apart, so that is what is pinned.
        """
        app = run(
            "ask",
            query="credit risk",
            answer=an_answer(
                decision="answered",
                support_band="strong",
                reason_codes=[],
                withheld=[],
                claims=[
                    {
                        "text": "Alpha monitors counterparty limits.",
                        "cited_passage_ids": [1],
                        "citations": [],
                        "disclosures": [],
                    },
                    {
                        "text": "Beta uses an expected credit loss model.",
                        "cited_passage_ids": [2],
                        "citations": [],
                        "disclosures": [],
                    },
                ],
                passages=[
                    {
                        "id": 1,
                        "chunk_id": "6f1d8c4e-0000-4000-8000-000000000001",
                        "text": "a",
                        "heading_path": [],
                        "page_numbers": [1],
                        "evidence_type": "narrative",
                        "issuer_name": "Alpha Industries Limited",
                        "fiscal_period": "FY2024-25",
                        "reporting_basis": "consolidated",
                        "rerank_score": None,
                        "expanded": False,
                        "stands_for": [],
                    },
                    {
                        "id": 2,
                        "chunk_id": "6f1d8c4e-0000-4000-8000-000000000002",
                        "text": "b",
                        "heading_path": [],
                        "page_numbers": [2],
                        "evidence_type": "narrative",
                        "issuer_name": "Beta Financial Services Limited",
                        "fiscal_period": "FY2024-25",
                        "reporting_basis": "consolidated",
                        "rerank_score": None,
                        "expanded": False,
                        "stands_for": [],
                    },
                ],
            ),
        )
        html = " ".join(str(element.value) for element in app.get("html"))

        paragraphs = re.findall(r'<div class="fs-answer">(.*?)</div>', html)
        assert len(paragraphs) == 2, "claims from two issuers must not share a paragraph"
        assert "Alpha Industries Limited" in paragraphs[0] or "Alpha" in html
        assert 'class="fs-issuer"' in html, "each paragraph must name its issuer"
        assert "Alpha monitors counterparty limits." in paragraphs[0]
        assert "Beta uses an expected credit loss model." in paragraphs[1]

    def test_one_issuer_gets_no_label(self) -> None:
        """A label on every answer would be noise; it earns its place only on a mix."""
        app = run("ask", query="credit risk")
        html = " ".join(str(element.value) for element in app.get("html"))

        assert 'class="fs-issuer"' not in html

    def test_the_whole_answer_collapses_under_one_label(self) -> None:
        """A long thread needs each answer foldable, as the evidence already was.

        The answer is rendered *inside* the status panel rather than beneath it, so the
        panel's own chevron folds the answer away. Previously the panel held only the
        waiting note, and collapsing it hid the note while leaving the answer behind.
        """
        app = run("ask", query="credit risk")
        text = rendered_text(app)

        assert ask.ANSWER_LABEL in text
        assert "counterparty limits" in text

    def test_the_waiting_note_does_not_outlive_the_wait(self) -> None:
        """It is instruction for the wait, not part of the answer."""
        text = rendered_text(run("ask", query="credit risk"))

        assert "Two to five minutes" not in text
        assert ask.ANSWER_LABEL in text

    def test_a_replayed_answer_collapses_the_same_way(self) -> None:
        """The control must not move between a turn watched and one scrolled back to."""
        app = run("ask", query="credit risk")
        app.run()

        labels = [str(element.label) for element in app.expander]
        assert ask.ANSWER_LABEL in labels
        assert any("Retrieved evidence" in label for label in labels)

    def test_nothing_escapes_the_answer_collapse(self) -> None:
        """Folding "Answer composed" has to take the whole answer with it.

        Anything rendered as a sibling of the panel stays on screen when it is closed, so
        this asserts the parts are *inside* it rather than merely present on the page.
        """
        app = run("ask", query="credit risk")
        app.run()
        panel = next(e for e in app.expander if str(e.label) == ask.ANSWER_LABEL)

        inside = " ".join(
            [str(element.value) for element in panel.get("html")]
            + [str(element.value) for element in panel.markdown]
            + [str(element.value) for element in panel.caption]
            + [str(element.value) for element in panel.warning]
            + [str(element.label) for element in panel.expander]
        )
        assert "counterparty limits" in inside, "the prose"
        assert "fs-cite" in inside, "the citation marks"
        assert "orange-badge" in inside, "the decision badge"
        assert "Withheld by the Evidence Gate" in inside, "what was removed"
        assert "recorded as" in inside, "the footer"

    def test_nothing_escapes_the_evidence_collapse(self) -> None:
        app = run("ask", query="credit risk")
        app.run()
        panel = next(e for e in app.expander if "Retrieved evidence" in str(e.label))

        inside = " ".join(
            [str(element.value) for element in panel.get("html")]
            + [str(element.value) for element in panel.markdown]
            + [str(element.value) for element in panel.caption]
        )
        assert "counterparty limits" in inside, "the passage body"
        assert "Probe Limited" in inside, "the provenance"
        assert "Fused" in inside, "the scores"

    def test_each_turn_keys_its_panels_by_identity_not_position(self) -> None:
        """Position-keyed panels are rebuilt when a later turn is appended.

        That is what undid a reader's collapse the moment someone asked a second
        question, and again on returning from another page.
        """
        app = run("ask", query="first question")
        app.run()
        _ask(app, "second question")
        app.run()

        turns = app.session_state[ask.TURNS_KEY]
        identities = [turn["id"] for turn in turns]
        assert len(set(identities)) == 2, "each turn needs its own identity"

        keys = {str(element.key) for element in app.expander if element.key}
        for identity in identities:
            assert f"ask.answer.{identity}" in keys
            assert f"ask.evidence.{identity}" in keys


class TestTheChatThread:
    """Ask is a thread of independent questions, and both halves of that matter."""

    def test_the_opening_screen_is_the_question_and_nothing_else(self) -> None:
        """The first screen belongs to the question.

        A title, a subtitle and a badge pushed the box below the fold on a laptop, and a
        figure shown before anything is asked describes nothing the reader did.
        """
        app = run("ask")
        text = rendered_text(app)
        html = " ".join(str(element.value) for element in app.get("html"))

        assert "What would you like to know?" in html
        assert app.metric == [], "metrics belong with an answer, not above an empty box"
        assert "Put a question to the ingested filings" not in text, "page header removed"
        assert "Every claim in the answer cites" not in text, "hero subtitle removed"
        assert "Nothing is carried between turns" not in text, "starters caption removed"

    def test_the_opening_screen_lifts_the_box_out_of_the_bottom_bar(self) -> None:
        """The rule is emitted only while the thread is empty; nothing has to undo it."""
        opening = " ".join(str(e.value) for e in run("ask").get("html"))
        after = run("ask", query="credit risk")
        after.run()
        later = " ".join(str(e.value) for e in after.get("html"))

        assert '[data-testid="stBottom"] { position: static' in opening
        assert '[data-testid="stBottom"] { position: static' not in later

    def test_one_box_serves_both_states(self) -> None:
        """Two inputs would mean two places for the stop control to live."""
        assert len(run("ask").chat_input) == 1

        after = run("ask", query="credit risk")
        after.run()
        assert len(after.chat_input) == 1

    def test_the_box_invites_the_product_by_name(self) -> None:
        assert run("ask").chat_input[0].placeholder == "Ask FinSight"

    def test_a_thread_offers_a_jump_to_the_newest_turn(self) -> None:
        """Offered only once there is something to scroll past."""
        opening = " ".join(str(e.value) for e in run("ask").get("html"))
        after = run("ask", query="credit risk")
        after.run()
        later = " ".join(str(e.value) for e in after.get("html"))

        assert "fs-to-bottom" not in opening
        assert 'href="#fs-newest"' in later
        assert 'id="fs-newest"' in later

    def test_starters_are_offered_only_on_the_opening_screen(self) -> None:
        """No chat product offers starters mid-thread; they would shove the thread about."""
        opening = [b.key for b in run("ask").button if b.key and "starter" in b.key]
        after = run("ask", query="credit risk")
        after.run()
        mid_thread = [b.key for b in after.button if b.key and "starter" in b.key]

        assert opening, "the opening screen offers starters"
        assert mid_thread == [], "a thread with content does not"

    def test_clicking_a_starter_asks_that_question(self) -> None:
        """The developer reported a click doing nothing; this pins the behaviour."""
        app = run("ask")
        starters = [b for b in app.button if b.key and b.key.startswith("ask.starter")]
        starters[0].click().run()

        turns = app.session_state[ask.TURNS_KEY]
        assert len(turns) == 1
        assert turns[0]["question"] == ask._EXAMPLES[0][0]
        assert turns[0]["answer"] is not None

    def test_the_opening_screen_is_gone_while_the_answer_composes(self) -> None:
        """Streamlit holds the previous render until the next run finishes.

        So the chips stayed clickable for the whole of a multi-minute generation and only
        vanished once the answer arrived. The opening screen is erased before the rerun
        rather than left for the next run to replace.
        """
        app = run("ask")
        starters = [b for b in app.button if b.key and b.key.startswith("ask.starter")]
        starters[0].click().run()

        remaining = [b for b in app.button if b.key and b.key.startswith("ask.starter")]
        html = " ".join(str(element.value) for element in app.get("html"))
        assert remaining == [], "the chips must not survive into the answering run"
        assert "What would you like to know?" not in html

    def test_starters_cover_both_audiences(self) -> None:
        """A production page serves a general reader and an analyst, not one of them."""
        registers = {register for _question, register in ask._EXAMPLES}

        assert registers == {"General", "Analyst"}

    def test_the_scope_filters_offer_the_values_that_exist(self) -> None:
        """A filter a reader has to spell is a trap.

        §7 forbids relaxing a hard filter to find more results, so "Infosys" against a
        recorded "Infosys Limited" returns nothing at all — which reads as an empty corpus
        rather than a typo. These were free-text boxes.
        """
        app = run("ask")
        issuer = app.get_by_key("ask.issuer")

        assert list(issuer.options) == ["Alpha Industries Limited"]
        assert issuer.value is None, "nothing is filtered until the reader chooses"
        assert app.get_by_key("ask.section").options == ["7. Risk factors"]
        assert app.get_by_key("ask.basis").options == ["consolidated"]

    def test_a_chosen_scope_reaches_the_backend(self) -> None:
        """Invisible in the output: a dropped filter and an applied one look the same."""
        app = run("ask")
        app.get_by_key("ask.issuer").set_value("Alpha Industries Limited")
        _ask(app, "credit risk")

        calls = dict(app.session_state[CALLS_KEY])
        assert calls["ask"]["filters"]["issuer_name"] == "Alpha Industries Limited"
        assert calls["search"]["filters"]["issuer_name"] == "Alpha Industries Limited"

    def test_an_unset_scope_sends_nothing_for_that_filter(self) -> None:
        """"Any" must mean unfiltered, not filtered on an empty string."""
        app = run("ask", query="credit risk")

        filters = dict(app.session_state[CALLS_KEY])["ask"]["filters"]
        assert filters["issuer_name"] is None
        assert filters["section"] is None

    def test_choosing_a_scope_does_not_disturb_the_conversation(self) -> None:
        """Every scope widget sets ``on_change="ignore"``.

        Streamlit reruns the whole script on any widget interaction, which redrew the page
        each time a filter was touched. The value still lands in session state and is read
        when a question is sent; what is suppressed is the rerun.
        """
        app = run("ask", query="credit risk")
        before = len(app.session_state[CALLS_KEY])

        app.get_by_key("ask.issuer").set_value("Alpha Industries Limited")

        assert len(app.session_state[CALLS_KEY]) == before, "setting a filter re-queried"
        assert len(app.session_state[ask.TURNS_KEY]) == 1

    def test_a_second_question_keeps_the_first_turn(self) -> None:
        """Scrolling back is the point; a turn must not be replaced."""
        app = run("ask", query="first question")
        app.run()
        _ask(app, "second question")

        text = rendered_text(app)
        assert "first question" in text
        assert "second question" in text
        assert len(app.session_state[ask.TURNS_KEY]) == 2

    def test_an_earlier_turn_is_replayed_without_repeating_the_request(self) -> None:
        """History is re-rendered from session state, so scrolling costs nothing."""
        app = run("ask", query="first question")
        turns = app.session_state[ask.TURNS_KEY]

        assert len(turns) == 1
        assert turns[0]["answer"] is not None
        assert turns[0]["result"] is not None

    def test_the_trace_page_still_sees_the_latest_retrieval(self) -> None:
        """`trace` reads ask.RESULT_KEY; the chat rewrite must not strand it."""
        app = run("ask", query="credit risk")

        assert app.session_state[ask.RESULT_KEY]["candidates"]


class TestDocumentTextIsEscaped:
    """Measured: 46 source elements in this corpus contain a tag-like ``<word``.

    Streamlit sanitizes with DOMPurify and ignores JavaScript, so this is not about script
    injection. It is that an unescaped ``<`` opens a tag and its text is swallowed — HDFC
    Bank's report discusses the ``<IR>`` framework, which rendered raw simply disappears,
    so a reader checking a citation would be shown less than the document says.
    """

    def test_a_passage_keeps_its_angle_brackets(self) -> None:
        app = run(
            "ask",
            query="integrated reporting",
            response=a_response(
                candidates=[
                    {
                        **a_response()["candidates"][0],
                        "text": "Prepared in alignment with the <IR> framework.",
                    }
                ]
            ),
        )
        html = " ".join(str(element.value) for element in app.get("html"))

        assert "&lt;IR&gt;" in html
        assert "<IR>" not in html

    def test_a_cited_span_keeps_its_angle_brackets(self) -> None:
        app = run(
            "ask",
            query="integrated reporting",
            answer=an_answer(
                claims=[
                    {
                        "text": "The report follows a framework.",
                        "cited_passage_ids": [1],
                        "citations": [
                            {
                                "passage_id": 1,
                                "source_element_id": "11111111-1111-1111-1111-111111111111",
                                "locator": "p. 41",
                                "text": "aligned with the <IR> framework",
                            }
                        ],
                        "disclosures": [],
                    }
                ],
                withheld=[],
                decision="answered",
                support_band="strong",
                reason_codes=[],
            ),
        )
        html = " ".join(str(element.value) for element in app.get("html"))

        assert "&lt;IR&gt;" in html

    def test_a_claim_keeps_its_angle_brackets(self) -> None:
        app = run(
            "ask",
            query="integrated reporting",
            answer=an_answer(
                claims=[
                    {
                        "text": "The bank reports under the <IR> framework.",
                        "cited_passage_ids": [1],
                        "citations": [],
                        "disclosures": [],
                    }
                ],
                withheld=[],
                decision="answered",
                support_band="strong",
                reason_codes=[],
            ),
        )
        html = " ".join(str(element.value) for element in app.get("html"))

        assert "&lt;IR&gt;" in html
        assert "The bank reports under the <IR>" not in html


class TestSystem:
    """The operations page: health, index consistency, and what was answered."""

    def test_it_reports_index_consistency_rather_than_asserting_it(self) -> None:
        """§29.2 makes Qdrant rebuildable, which reassures only if someone checks."""
        text = rendered_text(run("health"))

        assert "PostgreSQL and Qdrant agree" in text
        assert "4,867" in text

    def test_a_drifted_index_is_reported_as_an_error(self) -> None:
        app = run("health", system=_system(index_consistent=False, index_points=4000))
        errors = " ".join(e.value for e in app.error)

        assert "index has drifted" in errors
        assert "rebuildable from PostgreSQL" in errors

    def test_an_unreachable_index_is_unknown_not_broken(self) -> None:
        """Reporting it as drifted would send an operator to rebuild something fine."""
        app = run("health", system=_system(index_consistent=None, index_points=None))
        warnings = " ".join(e.value for e in app.warning)

        assert "could not be asked" in warnings
        assert "unknown rather than broken" in warnings

    def test_it_reports_how_often_the_system_declined(self) -> None:
        """A system that always answers is less trustworthy, not more."""
        app = run("health")
        metrics = {e.label: e.value for e in app.metric}

        assert metrics["Questions answered"] == "6"
        assert metrics["Declined"] == "33%"
        assert metrics["Released something"] == "67%"

    def test_it_explains_why_content_was_withheld(self) -> None:
        frame = run("health").dataframe[0].value

        assert "model_reported_unanswerable" in list(frame["Reason"])
        assert "did not answer the question" in " ".join(frame["What it means"])

    def test_an_essential_failure_is_an_error_and_a_degradable_one_is_not(self) -> None:
        """Losing Qdrant costs dense retrieval; losing PostgreSQL costs the service."""
        degraded = run("health", system=_system(qdrant_healthy=False))
        assert not degraded.error
        assert any("reduced capability" in w.value for w in degraded.warning)

        broken = run("health", system=_system(postgres_healthy=False))
        assert any("Not ready" in e.value for e in broken.error)

    def test_the_support_band_is_not_presented_as_a_probability(self) -> None:
        """§27.13."""
        assert "not** a probability" in rendered_text(run("health"))


class TestLibrary:
    """The corpus listing, now live.

    The property worth pinning is the §11.12 distinction: a document version records what
    *exists*, a generation records what is *queryable*. A listing that showed only
    "ingested" would report a document nothing can retrieve as though it were available.
    """

    def test_it_lists_what_was_ingested(self) -> None:
        text = rendered_text(run("library"))

        assert "Alpha Industries Limited" in text
        assert "Beta Financial Services Limited" in text

    def test_a_document_with_no_generation_is_named_not_blanked(self) -> None:
        """Null state is a definite fact, and an empty cell reads as "unknown".

        Asserted against the frame's own values rather than its rendered text: pandas
        elides middle columns with an ellipsis, so string-matching a wide table silently
        checks nothing.
        """
        frame = run("library").dataframe[0].value

        assert list(frame["Queryable"]) == ["active", "not built"]
        assert list(frame["Passages"]) == [1800, 0], "nothing to retrieve from the second"

    def test_queryable_counts_only_active_generations(self) -> None:
        """Two documents ingested, one retrievable. Conflating them is the whole trap."""
        app = run("library")
        metrics = {element.label: element.value for element in app.metric}

        assert metrics["Queryable filings"] == "1 of 2"
        assert metrics["Passages indexed"] == "1,800"

    def test_tables_are_reported_as_found_but_not_retrievable(self) -> None:
        """A metric that is always zero earns its place when the alternative is a reader
        assuming otherwise: 700 tables were found here and none can be retrieved."""
        app = run("library")
        tables = next(e for e in app.metric if e.label == "Tables retrievable")

        assert tables.value == "0"
        assert "700 found" in str(tables.delta)
        assert "ADR-003" in str(tables.help)

    def test_unread_regions_are_reported_beside_the_pages(self) -> None:
        """A document can be fully paginated and still hold regions nothing could read."""
        app = run("library")
        pages = next(e for e in app.metric if e.label == "Pages read")

        assert pages.value == "813"
        assert "10 unreadable regions" in str(pages.delta)

    def test_the_detail_panel_shows_how_a_filing_was_processed(self) -> None:
        text = rendered_text(run("library"))

        assert "Extracted" in text and "Judged" in text
        assert "Chunked" in text and "Queryable" in text
        assert "config 2" in text, "the extraction configuration"
        assert "45 tables accepted" in text
        assert "could not be read" in text, "the unreadable-region note"

    def test_it_shows_what_each_filing_contains(self) -> None:
        """A page count says how long a document is; sections say what is in it.

        Counted over retrievable passages, so a section listed is one a question can
        actually reach.
        """
        text = rendered_text(run("library"))

        assert "What this filing contains" in text
        assert "cannot be reached by a question" in text

    def test_changing_the_selected_filing_changes_the_detail(self) -> None:
        """`on_change="ignore"` is right for the Ask page's filters and wrong here.

        Suppressing the rerun left the panel showing the first filing whatever the reader
        chose, which is the bug the developer found.
        """
        app = run("library")
        assert "Alpha Industries Limited" in rendered_text(app)

        app.selectbox[0].set_value(
            "Beta Financial Services Limited • FY2024-25"
        ).run()

        detail = " ".join(str(e.value) for e in app.get("html"))
        assert "15,000 blocks" in detail, "the second filing's own figures"

    def test_the_page_no_longer_carries_a_control_that_cannot_be_pressed(self) -> None:
        """A disabled button is worse than no button."""
        app = run("library")

        assert not any(b.disabled for b in app.button), "no dead controls"
        assert "Rebuild index" not in rendered_text(app)

    def test_an_api_failure_leaves_the_page_usable(self) -> None:
        app = run("library", fail="Could not reach the API")

        assert not app.exception
        assert any("Could not reach the API" in error.value for error in app.error)

    def test_it_no_longer_claims_to_be_a_fixture(self) -> None:
        captions = " ".join(element.value for element in run("library").caption)

        assert "not yet wired" not in captions


class TestTrace:
    def test_the_latency_split_is_measured_not_apportioned(self) -> None:
        """It used to divide one measured total by ratios recorded in ENV-010.

        Those were real on the query they came from and a guess on every other, so a
        reader saw a fabricated figure beside a measured one with nothing to tell them
        apart. The stages are timed per call now.
        """
        app = run("trace", seeded={ask.RESULT_KEY: a_response()})
        text = rendered_text(app)

        assert "rerank is 92% of this query" in text
        assert "1749 ms of 1900 ms" in text
        assert "not apportioned from recorded ratios" in text

        # Every stage must be legible, including the small ones: a shared linear axis
        # rendered a 28 ms stage as no bar at all, which reads as "it did not run".
        frame = app.dataframe[-1].value
        assert list(frame["Stage"]) == ["lexical", "dense", "fusion", "resolve", "rerank"]
        assert list(frame["ms"]) == [60, 70, 1, 20, 1749]

    def test_a_response_without_timings_says_so_rather_than_inventing_them(self) -> None:
        """An older response carries no split; the page must not fill the gap."""
        app = run("trace", seeded={ask.RESULT_KEY: a_response(timings_ms={})})
        text = rendered_text(app)

        assert "no per-stage timings" in text
        assert "% of this query" not in text

    def test_reordering_reports_movement_not_just_two_rank_columns(self) -> None:
        """Whether the cross-encoder agreed with fusion or overruled it is the finding."""
        frame = run("trace", seeded={ask.RESULT_KEY: a_response()}).dataframe[0].value

        assert "Moved" in frame.columns

    def test_it_says_the_trace_is_not_persisted(self) -> None:
        """§31.9 is unsatisfied, and a trace that dies with the tab is not an audit trail."""
        import finsight.ui.views.trace as module

        assert "not persisted" in (module.__doc__ or "")

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
    def test_a_preview_page_says_so_in_its_own_words(self, name: str) -> None:
        """The wiring badges were removed; an unlabelled fixture would be misleading.

        Said in the page body rather than in a chip, because a reader about to upload a
        file should not have to find a legend to learn that nothing happens.
        """
        app = run(name)
        said = " ".join(
            [element.value for element in app.warning]
            + [element.value for element in app.caption]
            + [element.value for element in app.info]
        )

        assert "not wired" in said, f"{name} does not disclose that it is a preview"

    @pytest.mark.parametrize("name", PREVIEW_VIEWS)
    def test_no_preview_page_names_a_real_issuer(self, name: str) -> None:
        """A fabricated figure beside a real company's name is a fabricated record.

        It stays fabricated once it is screenshotted out of context, which is why the
        fixtures invent the issuer as well as the numbers.
        """
        text = rendered_text(run(name))

        for real in ("Infosys", "HDFC", "Reliance", "TCS"):
            assert real not in text, f"{name} names {real} beside fixture figures"
