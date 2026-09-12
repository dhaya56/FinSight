"""Tests for foundation application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from finsight.config.settings import (
    REJECTED_PASSWORD_VALUES,
    Environment,
    Settings,
    SettingsError,
    get_settings,
)

# A literal used only by this test module. It is not a credential for any system.
TEST_PASSWORD = "test-only-value-not-a-real-credential"


@pytest.fixture(autouse=True)
def _required_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply the one required secret so unrelated cases can construct Settings."""
    monkeypatch.setenv("FINSIGHT_POSTGRES_PASSWORD", TEST_PASSWORD)


class TestEnvironment:
    def test_defaults_to_development(self) -> None:
        assert Settings().environment is Environment.DEVELOPMENT

    def test_is_read_from_finsight_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FINSIGHT_ENV", "production")

        assert Settings().environment is Environment.PRODUCTION

    def test_unknown_value_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FINSIGHT_ENV", "staging")

        with pytest.raises(ValidationError):
            Settings()

    def test_is_read_from_env_file(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("FINSIGHT_ENV=test\n", encoding="utf-8")

        assert Settings().environment is Environment.TEST

    def test_environment_variable_overrides_env_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".env").write_text("FINSIGHT_ENV=test\n", encoding="utf-8")
        monkeypatch.setenv("FINSIGHT_ENV", "production")

        assert Settings().environment is Environment.PRODUCTION


class TestUnknownKeys:
    def test_unknown_env_file_key_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("FINSIGHT_TYPO_VARIABLE=1\n", encoding="utf-8")

        with pytest.raises(ValidationError):
            Settings()


class TestPassword:
    def test_missing_password_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FINSIGHT_POSTGRES_PASSWORD", raising=False)

        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_password_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        blank: str,
    ) -> None:
        monkeypatch.setenv("FINSIGHT_POSTGRES_PASSWORD", blank)

        with pytest.raises(SettingsError):
            Settings()

    @pytest.mark.parametrize("known_default", sorted(REJECTED_PASSWORD_VALUES))
    def test_known_default_password_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        known_default: str,
    ) -> None:
        monkeypatch.setenv("FINSIGHT_POSTGRES_PASSWORD", known_default)

        with pytest.raises(SettingsError):
            Settings()

    def test_known_default_rejection_is_case_insensitive(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FINSIGHT_POSTGRES_PASSWORD", "ChangeMe")

        with pytest.raises(SettingsError):
            Settings()

    def test_rejection_message_does_not_reveal_the_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FINSIGHT_POSTGRES_PASSWORD", "ChangeMe")

        with pytest.raises(SettingsError) as error:
            Settings()

        assert "changeme" not in str(error.value).lower()
        assert "FINSIGHT_POSTGRES_PASSWORD" in str(error.value)

    def test_unrelated_validation_failure_does_not_reveal_the_password(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failure on another field must not echo the password from the input."""
        monkeypatch.setenv("FINSIGHT_ENV", "staging")

        with pytest.raises(ValidationError) as error:
            Settings()

        assert TEST_PASSWORD not in str(error.value)

    def test_accepted_password_is_not_exposed_by_repr(self) -> None:
        settings = Settings()

        assert TEST_PASSWORD not in repr(settings)
        assert TEST_PASSWORD not in str(settings.postgres_password)


class TestDatabaseUrl:
    def test_carries_connection_details(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FINSIGHT_POSTGRES_HOST", "db.internal")
        monkeypatch.setenv("FINSIGHT_POSTGRES_PORT", "6543")
        monkeypatch.setenv("FINSIGHT_POSTGRES_DB", "finsight_test")
        monkeypatch.setenv("FINSIGHT_POSTGRES_USER", "finsight_app")

        url = Settings().database_url

        assert url.drivername == "postgresql+psycopg"
        assert url.host == "db.internal"
        assert url.port == 6543
        assert url.database == "finsight_test"
        assert url.username == "finsight_app"
        assert url.password == TEST_PASSWORD

    def test_rendered_string_hides_the_password(self) -> None:
        rendered = Settings().database_url.render_as_string()

        assert TEST_PASSWORD not in rendered
        assert "finsight" in rendered


class TestPoolDefaults:
    def test_initial_operational_defaults(self) -> None:
        settings = Settings()

        assert settings.db_pool_size == 5
        assert settings.db_max_overflow == 5
        assert settings.db_pool_recycle_seconds == 1800
        assert settings.db_connect_timeout_seconds == 5

    def test_values_are_configurable_without_a_code_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FINSIGHT_DB_POOL_SIZE", "12")
        monkeypatch.setenv("FINSIGHT_DB_CONNECT_TIMEOUT_SECONDS", "9")

        settings = Settings()

        assert settings.db_pool_size == 12
        assert settings.db_connect_timeout_seconds == 9


def test_get_settings_returns_one_cached_instance() -> None:
    assert get_settings() is get_settings()
