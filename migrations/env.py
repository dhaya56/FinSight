"""Alembic environment.

The connection is built from application settings through the same engine the
application uses, so there is one source of truth for how FinSight reaches
PostgreSQL and no database URL is ever written into a config file.

Offline mode is deliberately unsupported: rendering a URL into generated SQL
would embed the password in a file that is easy to share by accident.
"""

from logging.config import fileConfig

from alembic import context

from finsight.persistence.database import get_engine
from finsight.persistence.tables import documents as _documents  # noqa: F401
from finsight.persistence.tables.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Refuse offline mode rather than render credentials into SQL output."""
    message = (
        "Offline migrations are not supported. The database URL is assembled from "
        "settings and contains a secret; use 'alembic upgrade head' against a "
        "reachable database instead."
    )
    raise RuntimeError(message)


def run_migrations_online() -> None:
    """Run migrations against a live connection from the application engine."""
    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
