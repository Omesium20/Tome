"""Shared test fixtures.

The database fixture runs on in-memory SQLite so tests never touch the
configured database and never need one running.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        # A plain in-memory SQLite database is per-connection; StaticPool keeps
        # every session on the same one so the schema persists across calls.
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked, and these tests care whether
    # references actually constrain deletes.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
