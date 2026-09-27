"""Writing `CardMetadata` rows to the knowledge database.

Mirrors `scryfall_importer/sink.py`'s upsert shape: batched
``INSERT ... ON CONFLICT DO UPDATE``, idempotent so a re-run (after an oracle
text errata, a taxonomy change, or just picking up cards the last run missed)
overwrites in place rather than erroring or duplicating.
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database.knowledge.models import CardMetadata

logger = logging.getLogger(__name__)

# Every column except the primary key gets refreshed on conflict.
_UPDATABLE_COLUMNS = [
    column.name for column in CardMetadata.__table__.columns if column.name != "card_id"
]


def _insert_for(session: Session):
    """Pick the dialect-specific INSERT that supports ON CONFLICT.

    Same rationale as `sink.py`'s `_insert_for`: Postgres is the deployment
    target, SQLite is what tests and a small local corpus use.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert
    if dialect == "sqlite":
        return sqlite_insert
    raise NotImplementedError(
        f"CardMetadata upsert is not implemented for the {dialect!r} dialect. "
        "Use PostgreSQL or SQLite."
    )


def upsert_batches(session: Session, rows: list[dict[str, Any]]) -> int:
    """Insert or update ``rows`` (each from `CardMetadataBlueprint.to_card_metadata_row`).

    Returns how many rows were written. A no-op, not an error, on an empty list
    — the caller's last partial batch is often empty.
    """
    if not rows:
        return 0

    insert = _insert_for(session)
    statement = insert(CardMetadata).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[CardMetadata.card_id],
        set_={name: getattr(statement.excluded, name) for name in _UPDATABLE_COLUMNS},
    )
    session.execute(statement)
    session.commit()

    logger.debug("Upserted %d card_metadata rows", len(rows))
    return len(rows)


def metadata_count(session: Session) -> int:
    """How many cards currently have metadata."""
    return session.scalar(select(func.count()).select_from(CardMetadata)) or 0
