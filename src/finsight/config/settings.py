"""Foundation application settings.

Settings are supplied by the process environment, optionally seeded from a local
``.env`` file that is never committed. Only settings with a current consumer are
declared here; later phases extend this model as their capabilities are introduced.

Secret values are held as :class:`~pydantic.SecretStr` and are never written to a
log, an error message, or a rendered connection string.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Any, Final

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class SettingsError(RuntimeError):
    """Raised for invalid configuration that must never echo the supplied value.

    Pydantic attaches the raw input to its own validation errors, so credential
    checks raise this instead of ``ValueError``: it carries the variable name and
    the reason, never the value.
    """


class Environment(StrEnum):
    """Deployment environment the application is running in."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


REJECTED_PASSWORD_VALUES: Final[frozenset[str]] = frozenset(
    {
        "postgres",
        "password",
        "changeme",
        "secret",
        "admin",
        "example",
        "finsight",
    }
)
"""Known default and example credentials that must never reach a deployment.

This guards against well-known defaults only. It is not a password-strength
check and must not be described as one.
"""


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

    postgres_host: str = Field(default="localhost", description="PostgreSQL host.")
    postgres_port: int = Field(default=5432, ge=1, le=65535, description="PostgreSQL port.")
    postgres_db: str = Field(default="finsight", description="PostgreSQL database name.")
    postgres_user: str = Field(default="finsight", description="PostgreSQL role name.")
    postgres_password: SecretStr = Field(description="PostgreSQL password. Required.")

    # The four values below are initial operational defaults chosen from safe
    # constraints. No measurement supports them. Pool sizing, recycling, and the
    # connect timeout must be measured under the complete running stack before
    # any of them is treated as a validated setting. They are configurable so
    # that measurement does not require a code change.
    db_pool_size: int = Field(default=5, ge=1, description="Unmeasured initial default.")
    db_max_overflow: int = Field(default=5, ge=0, description="Unmeasured initial default.")
    db_pool_recycle_seconds: int = Field(
        default=1800, ge=1, description="Unmeasured initial default."
    )
    db_connect_timeout_seconds: int = Field(
        default=5, ge=1, description="Unmeasured initial default."
    )

    def model_post_init(self, context: Any, /) -> None:
        """Reject empty and known default credentials without revealing the value.

        This runs after validation rather than inside it. Both field and model
        validators surface the raw input in the resulting ``ValidationError`` —
        the field's value in one case, the whole input mapping in the other — so
        a rejected password would be rendered into the message and from there
        into any log or traceback. Raising :class:`SettingsError` here keeps the
        value out of the error entirely.
        """
        secret = self.postgres_password.get_secret_value().strip()
        if not secret:
            raise SettingsError("FINSIGHT_POSTGRES_PASSWORD must not be empty")
        if secret.lower() in REJECTED_PASSWORD_VALUES:
            raise SettingsError(
                "FINSIGHT_POSTGRES_PASSWORD matches a known default value and was rejected"
            )

    @property
    def database_url(self) -> URL:
        """Assemble the PostgreSQL DSN.

        ``URL.create`` escapes credentials correctly, so the password never has to
        be embedded in a hand-built string. Render with
        ``render_as_string(hide_password=True)`` for logging.
        """
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that every caller observes one consistent configuration. Tests clear
    the cache to isolate cases.
    """
    return Settings()
