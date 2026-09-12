"""Foundation application settings.

Settings are supplied by the process environment, optionally seeded from a local
``.env`` file that is never committed. Only settings with a current consumer are
declared here; later phases extend this model as their capabilities are introduced.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment the application is running in."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Environment-supplied application settings.

    Unknown keys are rejected rather than ignored so that a misspelled variable
    fails explicitly instead of silently falling back to a default.
    """

    model_config = SettingsConfigDict(
        env_prefix="FINSIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        validation_alias=AliasChoices("FINSIGHT_ENV"),
        description="Deployment environment: development, test, or production.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that every caller observes one consistent configuration. Tests clear
    the cache to isolate cases.
    """
    return Settings()
