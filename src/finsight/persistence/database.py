"""Database engine, session factory, and bounded transaction scope.

PostgreSQL is authoritative for application state. Everything here is created
lazily: importing this module opens no connection and reads no settings, so code
paths that never touch the database — such as liveness checks and contract tests
— need no database configuration at all.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Final

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from finsight.config.settings import get_settings

MIGRATIONS_PATH: Final = Path(__file__).resolve().parents[3] / "migrations"
"""Where the migration scripts live, relative to this module.

Readiness compares the database's revision against the head of this directory, so
any image that serves the readiness endpoint must ship it.
"""


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use.

    ``pool_pre_ping`` is behavioral rather than a tuning choice: a restarted
    container otherwise hands out connections that are already dead. The pool
    size, overflow, recycle interval, and connect timeout come from settings and
    are unmeasured initial defaults — see ``finsight.config.settings``.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
        echo=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Run a unit of work in one bounded transaction.

    Commits on clean exit, rolls back on any exception, re-raises the original
    exception unchanged, and always closes the session.

    Transactions must stay bounded (PROJECT_BLUEPRINT.md §29.7): no parser,
    model, object-store, broker, or vector-store call belongs inside this scope.
    Long-running work is performed outside it and its result persisted within.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def is_database_reachable() -> bool:
    """Report whether the database answers a trivial query.

    The failure reason is deliberately not returned. A connection error can carry
    the host, port, and role name, and this result is consumed by an unauthenticated
    readiness endpoint. Diagnostics belong in structured logs once that capability
    exists; until then a failure is reported as unreachable and nothing more.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


def is_schema_current() -> bool:
    """Report whether the database is migrated to the head revision.

    PROJECT_BLUEPRINT.md §31.2 requires readiness to check schema compatibility as
    well as dependency reachability: a reachable database running last week's
    schema will fail in ways a connectivity probe cannot see.

    Returns False rather than raising when the revision cannot be determined at
    all — including when the migrations directory is absent from a deployment.
    That is the honest answer for readiness, since an unverifiable schema is not a
    verified one, though it cannot currently distinguish "behind" from
    "unverifiable"; structured logging will separate them.
    """
    try:
        head = ScriptDirectory(str(MIGRATIONS_PATH)).get_current_head()
        with get_engine().connect() as connection:
            applied = MigrationContext.configure(connection).get_current_heads()
    except (SQLAlchemyError, OSError):
        return False
    return head is not None and applied == (head,)


def dispose_engine() -> None:
    """Release pooled connections if an engine was ever created.

    Safe to call when no engine exists: it returns without constructing one, so
    shutdown paths never force database settings to be read.
    """
    if get_engine.cache_info().currsize == 0:
        get_session_factory.cache_clear()
        return
    get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
