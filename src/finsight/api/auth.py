"""Bearer-token authentication for every non-health route (§28.2).

**This is applied to a router, not to a route.** ``APIRouter(dependencies=[...])``
runs the check for every path the router carries, present and future, so a route
added later cannot forget it. Decorating individual endpoints would make
authentication something each one has to remember, and the failure mode of
forgetting is an open endpoint that returns correct-looking data — the kind of
defect that is invisible in review and in tests that only ever send a valid token.

**An unconfigured token closes the API rather than opening it.** The settings field
is optional because the CLI and the test suite construct settings without serving
HTTP, so a required field would make every one of them carry a token they never use.
That optionality stops at the door: with no token configured this dependency
refuses every request with 503. The distinction matters because the opposite
default — serve openly when unconfigured — is how retrieval surfaces end up exposed
by omission rather than by decision.

**What this is not.** One shared secret, no accounts, no sessions, no rotation, no
rate limiting, no lockout. §28 does not require accounts at this stage and a
single-node self-hosted deployment has one operator, so this satisfies §28.2's
requirement that non-health routes be authenticated and nothing more. It is not a
user-identity mechanism, and nothing downstream may read an authorization decision
out of it: every authenticated caller is the same caller.
"""

import hmac
from typing import Annotated, Final

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from finsight.config.settings import Settings, get_settings

__all__ = ["require_token"]

_UNAUTHENTICATED: Final = "Invalid or missing credentials"
"""One message for a missing token, a malformed header and a wrong token alike.

Deliberately undifferentiated. "No credentials supplied" and "that credential is
wrong" are different facts, and telling them apart turns a single guess into a
confirmation that the endpoint is guarded by exactly one secret.
"""

_NOT_CONFIGURED: Final = "API authentication is not configured"
"""Said plainly, because the operator is the only one who can see it and fix it.

Unlike the message above there is nothing to protect here: it describes the
server's own misconfiguration and names no secret, no path and no value.
"""

_scheme = HTTPBearer(
    auto_error=False,
    description="Shared bearer token (FINSIGHT_API_TOKEN).",
)
"""``auto_error=False`` so this module owns the rejection.

Left at its default, HTTPBearer raises its own 403 for a missing header and a
different error for a malformed one, which both leaks the distinction above and
answers with the wrong status code for a missing credential.
"""


def require_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_scheme)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Admit a request carrying the configured bearer token.

    Settings are read through a dependency rather than at import time, which is what
    keeps :func:`finsight.api.app.create_app` constructible with no configuration
    present — the property its own docstring claims and the health contract tests
    rely on.

    Raises:
        HTTPException: 503 when no token is configured, 401 when the presented
            credential is absent or wrong.
    """
    configured = settings.api_token
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_NOT_CONFIGURED,
        )

    presented = credentials.credentials if credentials is not None else ""
    # compare_digest rather than ==: a short-circuiting comparison leaks the length
    # of the matching prefix through timing. Both sides are encoded because
    # compare_digest refuses to compare str values that are not ASCII-only, and a
    # token arrives from the network where its characters are not ours to assume.
    if not hmac.compare_digest(
        presented.encode("utf-8"), configured.get_secret_value().encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED,
            headers={"WWW-Authenticate": "Bearer"},
        )
