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

    # ------------------------------------------------------------------------ API

    api_token: SecretStr | None = Field(
        default=None,
        description=(
            "Shared bearer token required on every non-health route (§28.2). "
            "Optional *here* and mandatory *at the route*, which is deliberate: the "
            "CLI, the chunker and the whole test suite construct settings without "
            "serving HTTP, and making it a required field would add a required "
            "variable to every one of them. An unset token does not leave the API "
            "open — the authenticated router refuses to serve at all (503), so the "
            "failure mode is a closed door rather than an unguarded one. "
            "One shared secret, not a user system: §28 does not require accounts at "
            "this stage, and a single-node self-hosted deployment has one operator."
        ),
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

    # ---------------------------------------------------------------- Extraction

    docling_artifacts_path: str | None = Field(
        default=None,
        description=(
            "Directory holding pre-staged Docling model artifacts, as "
            "scripts/stage_docling_models.py populates. When set, a missing model "
            "raises instead of being downloaded mid-parse, which is what §20.6 "
            "requires and what the restricted parser worker (§11.7) depends on. "
            "None keeps Docling's own default, which downloads on first use."
        ),
    )

    text_search_config: str = Field(
        default="english",
        description=(
            "PostgreSQL text-search configuration used to analyse chunk text and "
            "queries. NOT A SELECTED VALUE. §9.7 requires the configuration, how "
            "per-document language is determined, the index type and any field "
            "weighting to be chosen with recorded evidence when the lexical index "
            "is built. The corpus is India-first with non-Indian supplements, so a "
            "single hardcoded language is explicitly not a safe default. This is "
            "the setting that makes the choice visible and changeable; changing it "
            "requires re-chunking under a new generation, because the stored "
            "lexemes were analysed with the old one."
        ),
    )

    # ------------------------------------------------------------------ Embedding

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        description=(
            "Host-native Ollama endpoint (§9.8). Loopback by default because "
            "Ollama is not containerised and is not exposed to the network."
        ),
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description=(
            "PROVISIONAL, not selected. ADR-004 adopts it as §22.2's named "
            "lightweight initial candidate; §22.10 reserves selection for the "
            "smallest model with acceptable measured quality, and no comparison "
            "against BGE-M3 has been run."
        ),
    )
    embedding_dimensions: int = Field(
        default=768,
        ge=1,
        description=(
            "Vector width, measured from the configured model rather than "
            "assumed. Changing the model almost certainly changes this, and a "
            "collection is created with a fixed width, so the two must move "
            "together."
        ),
    )
    embedding_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "Unmeasured. Generous because a cold model load is slow and a "
            "timeout mid-batch leaves outbox events pending for no reason."
        ),
    )
    embedding_batch_size: int = Field(
        default=32,
        ge=1,
        description=(
            "Unmeasured initial default. Ollama serialises embedding work, so "
            "batching reduces round trips rather than adding parallelism and a "
            "client-side thread pool would buy nothing — measured at 12.3 texts/s "
            "with one client thread and 12.3 with eight. Note that rate came from "
            "34-character probes; real enriched chunks measure 1.68 texts/s, and "
            "ADR-004 carries the correction. The threading conclusion holds; the "
            "rate does not."
        ),
    )
    embedding_max_input_chars: int = Field(
        default=6000,
        ge=1,
        description=(
            "Longest text sent to the model, in characters. MEASURED BOUND: "
            "Ollama anchors num_ctx to 2,048 tokens for nomic-embed-text and "
            "silently discards the remainder — appending a sentence to a "
            "2,048-token passage returned a bit-identical vector. English prose "
            "runs about 4.4 characters per token, so 2,048 tokens is roughly "
            "9,000 characters; this sits below that. Exceeding it raises rather "
            "than truncating, because truncated text is indexed and unfindable."
        ),
    )

    # --------------------------------------------------------------- Vector index

    qdrant_url: str = Field(
        default="http://127.0.0.1:6333",
        description=(
            "Qdrant REST endpoint. Loopback, matching how compose publishes it. "
            "The index is derived and rebuildable from PostgreSQL (§29.2), so "
            "losing it costs time rather than evidence."
        ),
    )
    qdrant_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Unmeasured initial default.",
    )
    # ------------------------------------------------------------------ Reranking

    rerank_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description=(
            "PROVISIONAL, not selected. §23.2 names MiniLM the lightweight baseline "
            "and §23.9 reserves the choice; ADR-006 adopts it on that basis. Loaded "
            "offline from the local cache, which is also why CI uses the "
            "deterministic fake."
        ),
    )
    rerank_depth: int = Field(
        default=25,
        ge=1,
        description=(
            "Fused candidates passed to the reranker. §23.5 leaves input depth "
            "baseline-driven, and quality is unmeasured — but the latency is not. "
            "MEASURED on this host's CPU at roughly 87 ms per candidate, batch 8: "
            "10 candidates 607 ms, 25 candidates 1.70 s, 50 candidates 4.01 s, 100 "
            "candidates 9.14 s, against 337 ms for the whole hybrid retrieval that "
            "precedes it. 25 keeps a query near two seconds while giving the "
            "reranker enough to reorder; 50 would make reranking 92% of the query. "
            "This is the number to change if interactivity matters more than depth. "
            "END TO END, steady state, depth 25: a reranked query is 2,544 ms "
            "against 175 ms with the reranker off, so reranking is 93% of the "
            "latency at about 95 ms per candidate. §23.4's FlashRank trigger is "
            "therefore measurably live, and ENV-010 carries the figures."
        ),
    )
    rerank_batch_size: int = Field(
        default=8,
        ge=1,
        description=(
            "MEASURED, not assumed: 8 beats 16 and 32 at every depth tested, "
            "because a larger batch pads every sequence to the longest in it and "
            "the wasted compute outweighs the fewer forward passes."
        ),
    )
    rerank_enabled: bool = Field(
        default=True,
        description=(
            "§23.1 requires the reranker to remain optional under degradation. This "
            "makes that switchable deliberately rather than only by failure, which "
            "is what lets the fused and reranked orders be compared on the same "
            "candidates — the comparison §23.9's selection needs."
        ),
    )

    # ----------------------------------------------------------------- Generation

    generation_model: str = Field(
        default="llama3.1:8b",
        description=(
            "PROVISIONAL, not selected. §22 reserves the choice and ADR-008 adopts this "
            "one on the same basis ADR-004 adopted the embedding model: it is already "
            "pulled, it runs on this host, and no comparison has been made. Served by "
            "host-native Ollama with no tools and no network of its own (§10.4)."
        ),
    )
    generation_context_window: int = Field(
        default=8192,
        ge=512,
        description=(
            "Tokens the model may read. Unmeasured, and deliberately generous: Ollama "
            "discards prompt tokens past this without erroring, so a window that is too "
            "small drops evidence while the answer still cites it. The adapter refuses a "
            "response whose prompt reached the ceiling rather than trusting one."
        ),
    )
    generation_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description=(
            "Zero, because §26.7's substitution is deterministic and an answer that "
            "varies between identical runs cannot be compared or reproduced. Not a "
            "guarantee of reproducibility, only the least variance available."
        ),
    )
    generation_evidence_budget_chars: int = Field(
        default=22000,
        ge=500,
        description=(
            "Characters of evidence a prompt may carry. Unmeasured, and bounded rather "
            "than left to the context window because published guidance is explicit that "
            "feeding everything retrieved to a model is not good practice. "
            "Characters because the generation model's tokenizer is not available here; at "
            "a conservative 3.5 characters per token this is roughly 6,300 tokens, leaving "
            "room in an 8,192 window for instructions and the completion. The adapter "
            "refuses a prompt that reached the window, so a wrong estimate fails loudly."
        ),
    )
    generation_expand_below_chars: int = Field(
        default=170,
        ge=0,
        description=(
            "Expand a retrieved passage to its parent only when it is shorter than this. "
            "MEASURED over six real queries and three policies: retrieved passages averaged "
            "1,574 characters with 1 of 48 below this floor, because reranking already "
            "filters out the corpus's 1,211 sub-floor children. Expanding everything "
            "discarded 20 of 48 reranked passages to the budget. Expanding fragments "
            "produced output identical to not expanding, because the one fragment that "
            "surfaced has no parent. So this is a no-op on this corpus, kept because its "
            "measured cost is zero and a retrieved fragment with a parent would benefit. "
            "170 characters is the chunker's own 48-token floor in this module's unit; 0 "
            "disables it, a large value restores expand-everything for comparison."
        ),
    )

    generation_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "MEASURED on this host: a two-claim answer took 16 s warm at roughly 3.9 "
            "tokens per second, and the first call also loads a 4.9 GB model. A longer "
            "answer scales with its own length, so this bound is wide — a timeout here "
            "degrades the answer (§27.9) rather than failing the question."
        ),
    )

    embedding_config_version: str = Field(
        default="1",
        description=(
            "Versions the embedding configuration as a whole (§14.10). It enters "
            "the deterministic point identifier (§29.9), so bumping it re-indexes "
            "to new points rather than overwriting ones an active generation may "
            "still be serving."
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
        if self.api_token is not None:
            _reject_known_default(self.api_token, "FINSIGHT_API_TOKEN")
            _require_minimum_length(self.api_token, "FINSIGHT_API_TOKEN")
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


MINIMUM_TOKEN_LENGTH: Final = 32
"""Characters required of a shared bearer token.

Not a measurement and not a cryptographic claim — a floor. A shared token is the
only thing standing in front of the retrieval route, it never rotates on its own,
and it is guessable in a way a password behind a login form is not, because there
is no account to lock and no rate limit yet. 32 characters is what
``secrets.token_urlsafe(24)`` produces, so the obvious way to generate one already
clears it. Length is checked rather than entropy because entropy cannot be measured
from the value, and pretending otherwise would be the kind of invented control
CLAUDE.md §3 forbids.
"""


def _require_minimum_length(secret: SecretStr, variable_name: str) -> None:
    """Refuse a token short enough to be worth guessing, revealing only its length."""
    length = len(secret.get_secret_value().strip())
    if length < MINIMUM_TOKEN_LENGTH:
        raise SettingsError(
            f"{variable_name} must be at least {MINIMUM_TOKEN_LENGTH} characters, "
            f"got {length}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that every caller observes one consistent configuration. Tests clear
    the cache to isolate cases.
    """
    return Settings()
