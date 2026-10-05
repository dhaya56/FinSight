"""The bearer-token dependency.

Tested through a throwaway app rather than against the real router, so these stay
about the credential check itself and need no pipeline, database or index. The cases
that matter are the ones a happy-path test never reaches: no token configured, no
header sent, and a token that is almost right.
"""

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.auth import require_token
from finsight.config.settings import Settings, get_settings

TOKEN = "a-token-long-enough-to-pass-the-length-floor"


@pytest.fixture
def app() -> FastAPI:
    """An app whose only route is guarded, mirroring how the real router guards."""
    built = FastAPI()

    @built.get("/guarded", dependencies=[Depends(require_token)])
    def _guarded() -> dict[str, str]:
        return {"ok": "yes"}

    return built


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _configure(app: FastAPI, token: str | None) -> None:
    """Supply settings carrying ``token`` without touching the environment."""
    settings = Settings(
        postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
        api_token=SecretStr(token) if token is not None else None,
    )
    app.dependency_overrides[get_settings] = lambda: settings


class TestConfigured:
    def test_the_right_token_is_admitted(self, app: FastAPI, client: TestClient) -> None:
        _configure(app, TOKEN)

        response = client.get("/guarded", headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 200

    def test_a_wrong_token_is_refused(self, app: FastAPI, client: TestClient) -> None:
        _configure(app, TOKEN)

        response = client.get(
            "/guarded", headers={"Authorization": "Bearer " + "x" * len(TOKEN)}
        )

        assert response.status_code == 401

    def test_a_token_that_is_a_prefix_of_the_real_one_is_refused(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """A length-only or prefix comparison would admit this."""
        _configure(app, TOKEN)

        response = client.get(
            "/guarded", headers={"Authorization": f"Bearer {TOKEN[:-1]}"}
        )

        assert response.status_code == 401

    def test_no_header_is_refused_as_unauthorized_not_forbidden(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """401 with a challenge, which is what "you did not authenticate" means."""
        _configure(app, TOKEN)

        response = client.get("/guarded")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_the_wrong_scheme_is_refused(self, app: FastAPI, client: TestClient) -> None:
        _configure(app, TOKEN)

        response = client.get("/guarded", headers={"Authorization": f"Basic {TOKEN}"})

        assert response.status_code == 401

    def test_a_missing_and_a_wrong_token_are_indistinguishable(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Telling them apart confirms the endpoint is guarded by one secret."""
        _configure(app, TOKEN)

        absent = client.get("/guarded")
        wrong = client.get("/guarded", headers={"Authorization": "Bearer nope"})

        assert absent.json() == wrong.json()


class TestUnconfigured:
    def test_an_unset_token_closes_the_route_rather_than_opening_it(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """The defect this guards against is serving openly when unconfigured."""
        _configure(app, None)

        response = client.get("/guarded")

        assert response.status_code == 503

    def test_an_unset_token_is_not_bypassed_by_sending_one(
        self, app: FastAPI, client: TestClient
    ) -> None:
        _configure(app, None)

        response = client.get("/guarded", headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 503


class TestTokenValidation:
    def test_a_short_token_is_refused_at_configuration_time(self) -> None:
        """Rejected where it is set, not where it is used."""
        with pytest.raises(Exception, match="at least 32 characters"):
            Settings(
                postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
                api_token=SecretStr("short"),
            )

    def test_a_known_default_token_is_refused(self) -> None:
        with pytest.raises(Exception, match="known default"):
            Settings(
                postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
                api_token=SecretStr("changeme"),
            )

    def test_the_error_does_not_contain_the_token(self) -> None:
        """§10: a rejected credential must not be rendered into a message or log."""
        secret = "x" * 8

        with pytest.raises(Exception) as caught:
            Settings(
                postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
                api_token=SecretStr(secret),
            )

        assert secret not in str(caught.value)

    def test_an_absent_token_is_valid_configuration(self) -> None:
        """The CLI and the test suite construct settings without serving HTTP."""
        settings = Settings(
            postgres_password=SecretStr("not-a-real-password-just-a-fixture")
        )

        assert settings.api_token is None
