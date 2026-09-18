"""Declarative base and the metadata conventions every table inherits.

The naming convention matters from the first migration onward. Without it,
SQLAlchemy and Alembic emit unnamed constraints, and a later ``ALTER`` cannot
address a constraint whose name the database chose. Naming them up front is
cheap; renaming them across existing data is not.
"""

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every FinSight table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
