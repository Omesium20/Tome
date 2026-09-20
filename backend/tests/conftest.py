"""Shared test fixtures.

Both database fixtures run on in-memory SQLite so tests never touch a
configured database and never need one running. They are separate fixtures
because the two planes are separate databases: a test that needs both is
almost certainly testing something that shouldn't exist.
"""

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database.knowledge.models import Base as KnowledgeBase
from database.local.models import Base as LocalBase


def memory_engine(base: type[DeclarativeBase]) -> Engine:
    """An in-memory SQLite engine with ``base``'s tables created."""
    engine = create_engine(
        "sqlite://",
        # A plain in-memory SQLite database is per-connection; StaticPool keeps
        # every session on the same one so the schema persists across calls.
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked. The keys that survived the
    # plane split are card_metadata -> cards and deck_cards -> decks; the
    # references from a user's rows to `cards` are deliberately not foreign
    # keys at all, and no PRAGMA can make them behave like one.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    base.metadata.create_all(engine)
    return engine


def _session_fixture(base: type[DeclarativeBase]):
    engine = memory_engine(base)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def knowledge_engine() -> Engine:
    """A knowledge database with no session attached.

    For code that opens its own sessions — the importer CLI does, because it
    has to build the engine before it can tell you which database it is about
    to empty.
    """
    engine = memory_engine(KnowledgeBase)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def knowledge_session() -> Session:
    """A session against the shared card corpus: cards, metadata, import runs."""
    yield from _session_fixture(KnowledgeBase)


@pytest.fixture
def local_session() -> Session:
    """A session against one user's own database: collection, decks."""
    yield from _session_fixture(LocalBase)
