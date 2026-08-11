import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tome.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
