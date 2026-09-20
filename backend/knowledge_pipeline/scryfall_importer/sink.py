"""Writing card rows to the knowledge database.

Two operations:

``upsert_batches``
    What an import does, and the only thing it does. Idempotent — re-running
    updates rows in place and never deletes, so a weekly refresh is safe to
    schedule and cannot orphan anything.

``reset``
    Empties the card table outright. Not part of an import; the CLI owns the
    confirmation.

**There is no prune.** It existed to clean up after a narrowed import — drop
the cards that fell outside the new format scope — and the importer no longer
narrows anything (`card_filter.py`). What remained afterwards was a way to
delete rows from a corpus every client reads, guarded only by a
`card_metadata` check that couldn't see the collections and decks it was
really protecting, since those live on users' machines
(`docs/data-model.md#two-databases-one-join-key`). A card Scryfall drops
upstream now lingers as an unreferenced row, which costs bytes; deleting it
would break whoever owns that card, which costs an install.
"""

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database.knowledge.models import Card, CardMetadata

from .mapping import CardRow

logger = logging.getLogger(__name__)

# Every column except the primary key gets refreshed on conflict.
_UPDATABLE_COLUMNS = [
    column.name for column in Card.__table__.columns if column.name != "oracle_id"
]


def _insert_for(session: Session):
    """Pick the dialect-specific INSERT that supports ON CONFLICT.

    Postgres is the deployment target; SQLite is what the tests run on and
    what a small development corpus can use. Both support upsert, but through
    separate constructs.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert
    if dialect == "sqlite":
        return sqlite_insert
    raise NotImplementedError(
        f"Card upsert is not implemented for the {dialect!r} dialect. "
        "Use PostgreSQL or SQLite."
    )


def _chunked(rows: Iterable[CardRow], size: int) -> Iterator[list[CardRow]]:
    batch: list[CardRow] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_batches(
    session: Session,
    rows: Iterable[CardRow],
    *,
    batch_size: int,
) -> int:
    """Insert or update ``rows`` in batches, returning how many were written.

    Consumes ``rows`` lazily, so the caller can hand over a generator chained
    all the way back to the gzip stream and never hold the corpus in memory.
    """
    insert = _insert_for(session)
    written = 0

    for batch in _chunked(rows, batch_size):
        # A single bulk file can legitimately contain two entries sharing an
        # oracle_id (reversible cards, some promos). Postgres rejects an
        # ON CONFLICT statement that touches the same key twice in one
        # command, so collapse duplicates before the write.
        deduped: dict[str, dict[str, Any]] = {}
        for row in batch:
            deduped[row.oracle_id] = row.as_dict()

        statement = insert(Card).values(list(deduped.values()))
        statement = statement.on_conflict_do_update(
            index_elements=[Card.oracle_id],
            set_={name: getattr(statement.excluded, name) for name in _UPDATABLE_COLUMNS},
        )
        session.execute(statement)
        session.commit()

        written += len(deduped)
        logger.debug("Upserted %d cards (%d total)", len(deduped), written)

    return written


def card_count(session: Session) -> int:
    """How many cards this database currently holds."""
    return session.scalar(select(func.count()).select_from(Card)) or 0


def reset(session: Session) -> int:
    """Empty the card table and the metadata derived from it.

    Returns the number of cards removed. There is no refusal condition for
    this function to apply — the user data it used to protect is not in this
    database. The caller owns the confirmation; see the CLI.
    """
    session.execute(delete(CardMetadata))
    removed = card_count(session)
    session.execute(delete(Card))
    session.commit()

    logger.info("Reset: removed %d cards", removed)
    return removed
