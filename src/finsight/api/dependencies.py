"""Process-wide wiring the routes share.

**One pipeline per process, and one for both routes.** Building it loads the cross-encoder from
the local cache, so building it per request would add that load to every query — and building a
*second* one for the answer route would load the same weights twice into one process. The answer
service therefore composes the pipeline cached here rather than wiring its own.

**The answer service is cached for a sharper reason: its generator owns an HTTP client.**
:class:`~finsight.generation.ollama_generator.OllamaGenerator` creates an ``httpx.Client`` when
none is supplied, and nothing closes it. Built per request, that is a new connection pool per
request that is never released — a socket and thread leak under sustained use. Built once, there
is one pool for the process, which is what a long-lived client is for.

**Cached behind plain functions on purpose.** ``lru_cache`` is the cache; each provider is a thin
wrapper so ``dependency_overrides`` has something to replace. Overriding the decorated function
directly would leave the cache populated for whatever ran next, which is one test leaking into
its neighbours.

**Nothing here runs at import time.** Settings are read when a provider is first called, so
:func:`finsight.api.app.create_app` stays constructible with no configuration present — the
property the health contract tests rely on. The consequence is that these providers read
:func:`~finsight.config.settings.get_settings` directly rather than through FastAPI: a test
substituting settings substitutes the *provider* instead, which is the seam the contract tests
already use.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from functools import lru_cache

from sqlalchemy.orm import Session

from finsight.config.settings import get_settings
from finsight.generation.ollama_generator import build_generator
from finsight.generation.service import AskService
from finsight.persistence.database import session_scope
from finsight.retrieval.pipeline import RetrievalPipeline, build_retrieval_pipeline

__all__ = ["SessionScope", "get_ask_service", "get_pipeline", "get_session_scope"]

SessionScope = Callable[[], AbstractContextManager[Session]]
"""A factory for one bounded database session (§29.7)."""


@lru_cache(maxsize=1)
def _cached_pipeline() -> RetrievalPipeline:
    """Build the pipeline once, paying the cross-encoder load a single time."""
    return build_retrieval_pipeline()


def get_pipeline() -> RetrievalPipeline:
    """Provide the process-wide retrieval pipeline."""
    return _cached_pipeline()


def get_session_scope() -> SessionScope:
    """Provide the session factory for routes that read the database directly.

    Not cached and not a session: a dependency that returned an open session would hold a
    transaction for the lifetime of a request, which §29.7 forbids. Routes open and close
    their own, bounded by the work they do.
    """
    return session_scope


@lru_cache(maxsize=1)
def _cached_ask_service() -> AskService:
    """Build the answer path once, over the shared pipeline and one HTTP client."""
    settings = get_settings()
    return AskService(
        pipeline=_cached_pipeline(),
        generator=build_generator(settings),
        evidence_budget_chars=settings.generation_evidence_budget_chars,
        expand_below_chars=settings.generation_expand_below_chars,
    )


def get_ask_service() -> AskService:
    """Provide the process-wide answer path.

    Safe to share: :class:`~finsight.generation.service.AskService` is a frozen dataclass whose
    only state is the pipeline, the generator's client and three configured numbers. Nothing a
    request does mutates it, so one instance serves every caller.
    """
    return _cached_ask_service()
