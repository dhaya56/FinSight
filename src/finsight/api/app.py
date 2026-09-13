"""FastAPI application factory.

Construction reads no settings and opens no connection: the database is reached
only when a request actually needs it. That keeps liveness answerable while the
database is down, and lets contract tests run with no database configuration.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from finsight import __version__
from finsight.api.routes import health
from finsight.persistence.database import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Release pooled database connections on shutdown.

    ``dispose_engine`` is safe when no engine was ever created, so shutdown never
    forces database settings to be read.
    """
    yield
    dispose_engine()


def create_app() -> FastAPI:
    """Build the FinSight API application."""
    app = FastAPI(
        title="FinSight API",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app
