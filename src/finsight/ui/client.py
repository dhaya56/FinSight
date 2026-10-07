"""The UI's only way to reach FinSight: HTTP to the authenticated API.

**This module's import list is the control, not a style choice** (§28.12, CLAUDE.md
§6). The UI holds no data-store credentials, and the way that is enforced is that
nothing under :mod:`finsight.ui` imports :mod:`finsight.config.settings`,
:mod:`finsight.persistence`, :mod:`finsight.vector_index` or
:mod:`finsight.object_store` — so there is no code path by which it could acquire
one. Configuration is read from the environment directly for the same reason:
``Settings`` carries the PostgreSQL password, and importing it to find a URL would
put that password in the UI process's reach. A test asserts the import boundary,
because a convenience import added later would dissolve it silently.
"""

import os
from dataclasses import dataclass
from typing import Any, Final

import httpx

__all__ = [
    "ASK_TIMEOUT_SECONDS",
    "ApiClient",
    "ApiError",
    "client_from_environment",
]

API_URL_VARIABLE: Final = "FINSIGHT_API_URL"
API_TOKEN_VARIABLE: Final = "FINSIGHT_API_TOKEN"
DEFAULT_API_URL: Final = "http://127.0.0.1:8000"

DEFAULT_TIMEOUT_SECONDS: Final = 120.0
"""Generous on purpose, and not a target.

A reranked query is measured at roughly 2.3 seconds steady state, but the first
request of a process also loads the cross-encoder from the local cache, and
httpx's own default of 5 seconds would abandon that load and report a timeout as
though retrieval had failed. A client timeout shorter than the work it waits for
turns a slow answer into a wrong diagnosis.
"""

ASK_TIMEOUT_SECONDS: Final = 900.0
"""Separate from the retrieval timeout, and far larger, because generation is.

Measured against the live route on this host: 302 seconds for one question over four
passages, of which 287 was the model. Prompt evaluation runs at roughly 26 tokens per
second and decode at roughly 3.3, because no GPU offload is available here and an 8B
model will not fit 2 GB of integrated video memory.

So the retrieval timeout would abandon a request **the server goes on to complete and
record** — the client gives up, the answer lands in the audit table, and the reader is
told the model failed when it did not. 900 seconds leaves headroom over the worst
measured case rather than sitting just above it.
"""


class ApiError(RuntimeError):
    """The API could not be reached, or refused the request.

    One exception type for transport failure and HTTP rejection alike: the UI's
    response to both is the same — say what happened and keep the page usable.
    """


@dataclass(frozen=True, slots=True)
class ApiClient:
    """A thin, synchronous client for the retrieval route.

    Synchronous because Streamlit's execution model is synchronous; there is no
    event loop to cooperate with and an async client would need one.
    """

    base_url: str
    token: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    ask_timeout: float = ASK_TIMEOUT_SECONDS

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        rerank: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Retrieve passages for ``query``.

        Raises:
            ApiError: the API was unreachable, rejected the credentials, or
                returned a status outside 2xx.
        """
        payload: dict[str, Any] = {"query": query, "limit": limit, "rerank": rerank}
        return self._post("/v1/search", _with_filters(payload, filters), self.timeout)

    def ask(
        self,
        question: str,
        *,
        limit: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Answer ``question`` from the corpus, with citations.

        **Minutes, not seconds** — see :data:`ASK_TIMEOUT_SECONDS`. A caller must not
        present this as interactive.

        An abstention is a successful response, not an error: the body carries the
        decision and the reason, and the evidence is attached even when no prose was
        composed. Only transport failure and a refused or failed request raise.

        Raises:
            ApiError: the API was unreachable, rejected the credentials, or returned a
                status outside 2xx.
        """
        payload: dict[str, Any] = {"question": question, "limit": limit}
        return self._post("/v1/ask", _with_filters(payload, filters), self.ask_timeout)

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """One POST, with the error handling both routes need.

        Shared so the two routes cannot drift on how a 401 is reported: the message tells
        the operator which end of the connection to look at, and getting that wrong sends
        them to the server when the token is the problem.
        """
        try:
            response = httpx.post(
                f"{self.base_url.rstrip('/')}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timeout,
            )
        except httpx.RequestError as error:
            raise ApiError(f"Could not reach the API at {self.base_url}: {error}") from error

        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise ApiError(
                f"The API rejected the token. Check {API_TOKEN_VARIABLE} matches "
                f"the one the server was started with."
            )
        if not response.is_success:
            raise ApiError(f"The API returned {response.status_code}: {_detail(response)}")

        decoded: dict[str, Any] = response.json()
        return decoded

    def facets(self) -> dict[str, Any]:
        """The values the scope filters can usefully take.

        Returns empty lists rather than raising when the API cannot be reached: a chooser
        with no options is a degraded control, while an exception here would take down the
        whole page over a sidebar.
        """
        try:
            response = httpx.get(
                f"{self.base_url.rstrip('/')}/v1/facets",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
        except httpx.RequestError:
            return {}
        if not response.is_success:
            return {}
        decoded: dict[str, Any] = response.json()
        return decoded

    def library(self) -> dict[str, Any]:
        """Every ingested document and the generation of each that is queryable.

        Raises:
            ApiError: the API was unreachable, rejected the credentials, or returned a
                status outside 2xx. Unlike :meth:`facets`, a failure here is reported: the
                Library page has nothing else to show, so swallowing it would leave an
                empty table that reads as an empty corpus.
        """
        return self._get("/v1/library", self.timeout)

    def system(self) -> dict[str, Any]:
        """Dependency health, index consistency and answer activity.

        Raises:
            ApiError: the API was unreachable, rejected the credentials, or returned a
                status outside 2xx.
        """
        return self._get("/v1/system", self.timeout)

    def _get(self, path: str, timeout: float) -> dict[str, Any]:
        """One GET, with the error handling every read route needs."""
        try:
            response = httpx.get(
                f"{self.base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timeout,
            )
        except httpx.RequestError as error:
            raise ApiError(f"Could not reach the API at {self.base_url}: {error}") from error

        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise ApiError(
                f"The API rejected the token. Check {API_TOKEN_VARIABLE} matches "
                f"the one the server was started with."
            )
        if not response.is_success:
            raise ApiError(f"The API returned {response.status_code}: {_detail(response)}")

        decoded: dict[str, Any] = response.json()
        return decoded

    def is_ready(self) -> bool:
        """Whether the API's readiness probe currently passes.

        Unauthenticated, so this answers even when the token is wrong, which is what
        makes "the server is down" distinguishable from "the token is wrong" in the
        UI rather than leaving one error to mean both.
        """
        try:
            response = httpx.get(f"{self.base_url.rstrip('/')}/health/ready", timeout=5.0)
        except httpx.RequestError:
            return False
        return response.is_success


def _with_filters(
    payload: dict[str, Any], filters: dict[str, Any] | None
) -> dict[str, Any]:
    """Add only the filters the caller actually set.

    Sending nulls would be equivalent to the API, but an absent key states "unfiltered"
    rather than "filtered on nothing", and both request contracts forbid unknown fields —
    so a stray key is a 422 rather than a silently widened scope.
    """
    payload.update(
        {key: value for key, value in (filters or {}).items() if value not in (None, "")}
    )
    return payload


def _detail(response: httpx.Response) -> str:
    """Pull FastAPI's ``detail`` out of an error body, falling back to the text.

    Bounded because the body is not guaranteed to be the API's own: a proxy or an
    error page can answer instead, and an unbounded paste of it into the UI is how
    an HTML document ends up rendered inside an error message.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:200]
    return response.text[:200]


def client_from_environment() -> ApiClient:
    """Build the client from the environment.

    Raises:
        ApiError: no token is configured. Refused rather than defaulted, because a
            placeholder token would turn a configuration mistake into a 401 and send
            the operator looking at the wrong end of the connection.
    """
    token = os.environ.get(API_TOKEN_VARIABLE, "").strip()
    if not token:
        raise ApiError(
            f"{API_TOKEN_VARIABLE} is not set. The UI reaches FinSight only through "
            f"the authenticated API and has no other credentials."
        )
    return ApiClient(
        base_url=os.environ.get(API_URL_VARIABLE, DEFAULT_API_URL).strip() or DEFAULT_API_URL,
        token=token,
    )
