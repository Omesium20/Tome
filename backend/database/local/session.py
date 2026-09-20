"""Engine and sessions for the user's own database.

Lazy for the same reason as the knowledge engine: constructing a connection is
a side effect, and importing a module shouldn't have one.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_local_settings


@lru_cache
def get_engine() -> Engine:
    """The process-wide local engine, created on first use."""
    return create_engine(get_local_settings().local_database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def new_session() -> Session:
    """A new session against the local database. Caller closes it."""
    return get_sessionmaker()()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session against the local database."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
