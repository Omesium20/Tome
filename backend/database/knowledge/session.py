"""Engine and sessions for the shared cloud corpus.

The engine is built lazily rather than at import time. ``KNOWLEDGE_DATABASE_URL``
is required with no default, so a module-level ``create_engine`` would mean any
process that so much as imports this module dies on a setting a normal client
install has no business having.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_knowledge_settings


@lru_cache
def get_engine() -> Engine:
    """The process-wide knowledge engine, created on first use."""
    return create_engine(get_knowledge_settings().knowledge_database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def new_session() -> Session:
    """A new session against the knowledge database. Caller closes it."""
    return get_sessionmaker()()
