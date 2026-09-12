"""Tests for foundation application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from finsight.config.settings import Environment, Settings, get_settings


def test_environment_defaults_to_development() -> None:
    assert Settings().environment is Environment.DEVELOPMENT


def test_environment_is_read_from_finsight_env_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "production")

    assert Settings().environment is Environment.PRODUCTION


def test_unknown_environment_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings()


def test_environment_is_read_from_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FINSIGHT_ENV=test\n", encoding="utf-8")

    assert Settings().environment is Environment.TEST


def test_environment_variable_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("FINSIGHT_ENV=test\n", encoding="utf-8")
    monkeypatch.setenv("FINSIGHT_ENV", "production")

    assert Settings().environment is Environment.PRODUCTION


def test_get_settings_returns_one_cached_instance() -> None:
    assert get_settings() is get_settings()
