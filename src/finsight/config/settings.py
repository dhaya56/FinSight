"""Foundation application settings.

Settings are supplied by the process environment, optionally seeded from a local
``.env`` file that is never committed. Only settings with a current consumer are
declared here; later phases extend this model as their capabilities are introduced.

Secret values are held as :class:`~pydantic.SecretStr` and are never written to a
log, an error message, or a rendered connection string.

Object-storage settings are named for the protocol, not for any vendor. SeaweedFS
is one endpoint that answers the S3 API; the same values point at AWS S3,
Cloudflare R2, or another S3-compatible service without a code change.
"""

import ipaddress
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any, Final
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
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


class S3AddressingStyle(StrEnum):
    """How bucket names are placed in request URLs.

    Self-hosted backends generally require path style; AWS prefers virtual-host
    style. Making this explicit is what lets the endpoint move.
    """

    PATH = "path"
    VIRTUAL = "virtual"
    AUTO = "auto"


REJECTED_SECRET_VALUES: Final[frozenset[str]] = frozenset(
    {
        "postgres",
        "password",
        "changeme",
        "secret",
        "admin",
        "example",
        "finsight",
        "minioadmin",
    }
)
"""Known default and example credentials that must never reach a deployment.

Includes ``minioadmin``, the MinIO image's own default. This guards against
well-known defaults only. It is not a password-strength check and must not be
described as one.
"""

DEFAULT_ALLOWED_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/html",
    "application/xml",
    "text/xml",
)
"""The formats PROJECT_BLUEPRINT.md §5.2 admits. Parsers arrive in later phases."""


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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

    # ---------------------------------------------------------------- PostgreSQL

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

    # ------------------------------------------------------------- Object storage

    s3_endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint. Unset means the SDK default, which is how AWS is used.",
    )
    s3_bucket: str = Field(default="finsight", description="Bucket holding all object classes.")
    s3_region: str = Field(default="us-east-1", description="Region name sent to the backend.")
    s3_addressing_style: S3AddressingStyle = Field(
        default=S3AddressingStyle.PATH,
        description="Path style for self-hosted backends; virtual or auto for AWS.",
    )
    s3_access_key_id: str | None = Field(
        default=None,
        description="Unset falls through to boto3's default credential chain.",
    )
    s3_secret_access_key: SecretStr | None = Field(
        default=None,
        description="Unset falls through to boto3's default credential chain.",
    )

    # As with the database pool, these five are unmeasured initial defaults kept
    # configurable so measurement never requires a code change. botocore's own
    # defaults are sixty seconds for both timeouts, which is far too long for an
    # ingestion path, and worst-case latency is attempts x (connect + read).
    s3_connect_timeout_seconds: int = Field(default=5, ge=1, description="Unmeasured default.")
    s3_read_timeout_seconds: int = Field(default=30, ge=1, description="Unmeasured default.")
    s3_max_attempts: int = Field(default=3, ge=1, description="Unmeasured default.")
    s3_multipart_threshold_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1, description="Unmeasured default."
    )
    s3_multipart_concurrency: int = Field(default=2, ge=1, description="Unmeasured default.")

    s3_send_checksum: bool = Field(
        default=True,
        description=(
            "Ask the backend to record a SHA-256 checksum on write. Verified working "
            "on the pinned SeaweedFS image; configurable because S3 compatibility is "
            "a spectrum and another backend may reject the header."
        ),
    )

    # ----------------------------------------------------------------- Ingestion

    upload_max_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1,
        description="Safe operational bound, verified by bounded tests rather than measured.",
    )
    # NoDecode: without it pydantic-settings parses a complex field's raw value as
    # JSON before any validator runs, so a comma-separated list would fail before
    # reaching the splitter below.
    allowed_content_types: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_ALLOWED_CONTENT_TYPES,
        description="Detected content types accepted at upload.",
    )

    @field_validator("allowed_content_types", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: Any) -> Any:
        """Accept a comma-separated list, which is how environments express one."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    def model_post_init(self, context: Any, /) -> None:
        """Validate secrets and endpoint safety without revealing supplied values.

        These run after validation rather than inside it. Both field and model
        validators surface the raw input in the resulting ``ValidationError`` —
        the field's value in one case, the whole input mapping in the other — so
        a rejected credential would be rendered into the message and from there
        into any log or traceback. Raising :class:`SettingsError` here keeps the
        value out of the error entirely.
        """
        _reject_known_default(self.postgres_password, "FINSIGHT_POSTGRES_PASSWORD")
        if self.s3_secret_access_key is not None:
            _reject_known_default(self.s3_secret_access_key, "FINSIGHT_S3_SECRET_ACCESS_KEY")
        self._require_tls_beyond_loopback()

    def _require_tls_beyond_loopback(self) -> None:
        """Refuse a plaintext endpoint that is not on the loopback interface.

        Loopback HTTP is acceptable locally because there is no certificate to
        verify. Anything else carries filings over the network in clear text, so
        a misconfigured cloud endpoint fails at startup rather than silently.
        """
        if self.s3_endpoint_url is None:
            return
        parsed = urlparse(self.s3_endpoint_url)
        if parsed.scheme == "https":
            return
        if parsed.scheme != "http":
            raise SettingsError("FINSIGHT_S3_ENDPOINT_URL must use http or https")
        if not _is_loopback(parsed.hostname or ""):
            raise SettingsError(
                "FINSIGHT_S3_ENDPOINT_URL must use https for a non-loopback host"
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


def _reject_known_default(secret: SecretStr, variable_name: str) -> None:
    """Reject empty and known default credentials without revealing the value."""
    value = secret.get_secret_value().strip()
    if not value:
        raise SettingsError(f"{variable_name} must not be empty")
    if value.lower() in REJECTED_SECRET_VALUES:
        raise SettingsError(f"{variable_name} matches a known default value and was rejected")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that every caller observes one consistent configuration. Tests clear
    the cache to isolate cases.
    """
    return Settings()
