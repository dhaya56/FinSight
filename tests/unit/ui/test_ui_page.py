"""The page renders, and renders the right disclaimers.

``AppTest`` executes the script the way Streamlit does, which makes this the only
check that the entry point actually runs — a page guarded by ``if __name__ ==
"__main__"`` that Streamlit happened to import under another name would render
blank, and nothing else in the suite would notice until a demonstration.

The API client is replaced, so this needs no server. What is asserted beyond "it
renders" is the two things §27 and §7 require to be on the page rather than in
documentation: that this returns passages rather than answers, and that a
degradation is visible when one occurred.
"""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import finsight.ui
from finsight.ui.client import ApiClient, ApiError

APP_SCRIPT = Path(finsight.ui.__file__).parent / "app.py"
TOKEN = "a-token-long-enough-to-pass-the-length-floor"


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


class StubClient(ApiClient):
    """An ApiClient that answers from memory instead of over HTTP."""

    def __init__(self, response: dict[str, Any] | None = None, *, fail: str = "") -> None:
        super().__init__(base_url="http://127.0.0.1:8000", token=TOKEN)
        object.__setattr__(self, "_response", response or a_response())
        object.__setattr__(self, "_fail", fail)

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        if self._fail:
            raise ApiError(self._fail)
        return self._response

    def is_ready(self) -> bool:
        return True


def run(client: ApiClient, *, query: str = "") -> AppTest:
    """Render the page with ``client`` substituted, optionally running a search."""
    app = AppTest.from_file(str(APP_SCRIPT), default_timeout=30)
    app.session_state["_test_client"] = client
    app.run()
    if query:
        # Two runs, deliberately. The Search button is disabled while the box is
        # empty, and a browser user cannot click a disabled button either — so the
        # value has to land and the script re-run before the click is possible. A
        # single run here would assert against a page no user can reach.
        app.text_input[0].set_value(query).run()
        app.button[0].click().run()
    return app


@pytest.fixture(autouse=True)
def _substitute_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the page's client factory at whatever the session state holds.

    **Patched on** :mod:`finsight.ui.client`, **not on the page module.**
    ``AppTest`` executes the script's source in a fresh namespace rather than
    importing it, so an attribute replaced on the already-imported page module would
    never be consulted. The script's own ``from finsight.ui.client import ...`` does
    reach this, because that import runs during the fresh execution.

    Patching the factory rather than ``httpx`` keeps the test about the page: the
    client's own behaviour is covered in ``test_ui_client.py``.
    """

    def from_state() -> ApiClient:
        import streamlit as st

        held = st.session_state.get("_test_client")
        if held is None:
            raise ApiError("no client configured for this test")
        return held

    monkeypatch.setattr(
        "finsight.ui.client.client_from_environment", from_state
    )


class TestRender:
    def test_the_page_runs_at_all(self) -> None:
        """The check that a blank page cannot pass."""
        app = run(StubClient())

        assert not app.exception
        assert app.title[0].value == "FinSight"

    def test_the_page_says_it_returns_passages_not_answers(self) -> None:
        """§27: nothing here may be read as a generated or judged answer."""
        app = run(StubClient())

        captions = " ".join(element.value for element in app.caption)
        assert "not answers" in captions

    def test_a_search_renders_the_passage_with_its_provenance(self) -> None:
        app = run(StubClient(), query="credit risk")

        assert not app.exception
        rendered = " ".join(str(element.value) for element in app.markdown)
        assert "Probe Limited" in rendered
        assert "counterparty limits" in rendered
        # The heading path is a caption, which AppTest reports separately.
        captions = " ".join(element.value for element in app.caption)
        assert "7.2 Credit risk" in captions

    def test_no_internal_commentary_is_rendered_to_the_reader(self) -> None:
        """Streamlit's magic renders bare top-level string literals as page content.

        An attribute docstring in the page module is therefore published, which is
        how a paragraph explaining the degradation flags to a maintainer ended up on
        the page itself. Pinned because the mistake is invisible in review: the
        source looks like ordinary documentation.
        """
        app = run(StubClient(), query="credit risk")

        everything = " ".join(
            str(element.value)
            for group in (app.markdown, app.caption, app.info, app.warning)
            for element in group
        )
        for giveaway in ("machine-readable", "stable identifiers", "§20.12."):
            assert giveaway not in everything

    def test_the_rerank_score_is_labelled_a_logit_not_a_confidence(self) -> None:
        app = run(StubClient(), query="credit risk")

        captions = " ".join(element.value for element in app.caption)
        assert "logit" in captions


class TestHonesty:
    def test_a_degraded_result_warns_and_explains_the_cost(self) -> None:
        """§20.12: flagged in words the reader can act on, not just a flag name."""
        app = run(
            StubClient(a_response(degraded=["dense_unavailable"], dense_used=False)),
            query="credit risk",
        )

        warnings = " ".join(element.value for element in app.warning)
        assert "dense_unavailable" in warnings
        assert "lexical only" in warnings

    def test_an_empty_result_explains_why_rather_than_looking_broken(self) -> None:
        app = run(StubClient(a_response(candidates=[])), query="photosynthesis")

        info = " ".join(element.value for element in app.info)
        assert "Nothing matched" in info
        assert "ADR-003" in info

    def test_an_api_failure_leaves_the_page_usable(self) -> None:
        app = run(StubClient(fail="Could not reach the API"), query="credit risk")

        assert not app.exception
        assert any("Could not reach the API" in e.value for e in app.error)
