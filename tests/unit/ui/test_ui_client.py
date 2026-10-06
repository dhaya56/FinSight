"""The UI's API client: what it sends, and how it reports failure.

Transport is faked at the ``httpx`` call, so these need no server. The cases worth
pinning are the ones a reviewer will hit during a demonstration: the API is not
running, the token is wrong, and a filter left blank in the sidebar must not narrow
the search to the empty string.
"""

from typing import Any

import httpx
import pytest

from finsight.ui.client import (
    API_TOKEN_VARIABLE,
    API_URL_VARIABLE,
    DEFAULT_API_URL,
    ApiClient,
    ApiError,
    client_from_environment,
)

TOKEN = "a-token-long-enough-to-pass-the-length-floor"


class Recorder:
    """Captures the outgoing request and returns a canned response."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.response = response or httpx.Response(200, json={"candidates": []})
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(base_url="http://127.0.0.1:8000", token=TOKEN)


class TestRequestShape:
    def test_the_token_is_sent_as_a_bearer_credential(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder()
        monkeypatch.setattr(httpx, "post", recorder)

        client.search("credit risk")

        assert recorder.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"

    def test_the_route_is_addressed_once_even_with_a_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder()
        monkeypatch.setattr(httpx, "post", recorder)

        ApiClient(base_url="http://127.0.0.1:8000/", token=TOKEN).search("x")

        assert recorder.calls[0]["url"] == "http://127.0.0.1:8000/v1/search"

    def test_blank_filters_are_omitted_rather_than_sent_empty(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty-string filter would narrow the search to nothing."""
        recorder = Recorder()
        monkeypatch.setattr(httpx, "post", recorder)

        client.search("x", filters={"issuer_name": "", "section": None, "document_type": "10-K"})

        sent = recorder.calls[0]["json"]
        assert "issuer_name" not in sent
        assert "section" not in sent
        assert sent["document_type"] == "10-K"

    def test_the_query_limit_and_rerank_choice_are_sent(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder()
        monkeypatch.setattr(httpx, "post", recorder)

        client.search("credit risk", limit=12, rerank=False)

        sent = recorder.calls[0]["json"]
        assert sent == {"query": "credit risk", "limit": 12, "rerank": False}

    def test_the_timeout_is_longer_than_a_reranked_query(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A client timeout under the measured ~2.3 s would misreport slow as broken."""
        recorder = Recorder()
        monkeypatch.setattr(httpx, "post", recorder)

        client.search("x")

        assert recorder.calls[0]["timeout"] > 10.0


class TestFailure:
    def test_an_unreachable_api_says_so_with_its_address(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "post", refuse)

        with pytest.raises(ApiError, match="Could not reach the API"):
            client.search("x")

    def test_a_rejected_token_names_the_variable_to_fix(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx, "post", Recorder(httpx.Response(401)))

        with pytest.raises(ApiError, match=API_TOKEN_VARIABLE):
            client.search("x")

    def test_a_server_error_carries_the_detail(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "post",
            Recorder(httpx.Response(503, json={"detail": "index unreachable"})),
        )

        with pytest.raises(ApiError, match="index unreachable"):
            client.search("x")

    def test_a_non_json_error_body_is_bounded(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A proxy's HTML error page must not be pasted whole into the UI."""
        monkeypatch.setattr(
            httpx, "post", Recorder(httpx.Response(502, text="<html>" + "x" * 5000))
        )

        with pytest.raises(ApiError) as caught:
            client.search("x")

        assert len(str(caught.value)) < 400

    def test_readiness_is_false_when_the_api_is_down(
        self, client: ApiClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unauthenticated, so "server down" stays distinct from "token wrong"."""

        def refuse(url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", refuse)

        assert client.is_ready() is False


class TestEnvironment:
    def test_the_token_is_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_TOKEN_VARIABLE, raising=False)

        with pytest.raises(ApiError, match=API_TOKEN_VARIABLE):
            client_from_environment()

    def test_the_url_defaults_to_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_TOKEN_VARIABLE, TOKEN)
        monkeypatch.delenv(API_URL_VARIABLE, raising=False)

        assert client_from_environment().base_url == DEFAULT_API_URL

    def test_the_url_is_taken_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_TOKEN_VARIABLE, TOKEN)
        monkeypatch.setenv(API_URL_VARIABLE, "http://localhost:9000")

        assert client_from_environment().base_url == "http://localhost:9000"

    def test_a_whitespace_only_token_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_TOKEN_VARIABLE, "   ")

        with pytest.raises(ApiError):
            client_from_environment()
