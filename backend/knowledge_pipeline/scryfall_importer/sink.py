"""Writing card rows to the database.

Three operations, in descending order of safety:

``upsert_batches``
    The default. Idempotent — re-running an import updates rows in place and
    never deletes, so a weekly refresh can't disturb a collection or a deck.

``prune``
    Removes cards the current import didn't produce, but only when nothing
    references them. Reference-safe by construction rather than by convention.

``reset``
    Empties the card table outright. Refuses to run while user data exists
    unless explicitly forced, because a collection is not re-downloadable the
    way card data is.
"""

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database.models import Card, CardMetadata, Collection, Deck, DeckCard

from .mapping import CardRow

logger = logging.getLogger(__name__)

# Every column except the primary key gets refreshed on conflict.
_UPDATABLE_COLUMNS = [
    column.name for column in Card.__table__.columns if column.name != "oracle_id"
]


class ResetRefused(RuntimeError):
    """Raised when a destructive reset would take user data with it."""


def _insert_for(session: Session):
    """Pick the dialect-specific INSERT that supports ON CONFLICT.

    Postgres is the deployment target; SQLite is the zero-setup default in
    ``config.Settings`` and what the tests run on. Both support upsert, but
    through separate constructs.
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
) -> tuple[int, set[str]]:
    """Insert or update ``rows`` in batches.

    Consumes ``rows`` lazily, so the caller can hand over a generator chained
    all the way back to the gzip stream and never hold the corpus in memory.

    Returns the number of rows written and the set of oracle ids seen, the
    latter being what :func:`prune` needs to know what's now stale.
    """
    insert = _insert_for(session)
    written = 0
    seen: set[str] = set()

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
        seen.update(deduped)
        logger.debug("Upserted %d cards (%d total)", len(deduped), written)

    return written, seen


def referenced_card_ids(session: Session) -> set[str]:
    """Every card id currently pointed at by user data or generated metadata."""
    queries = (
        select(Collection.card_id),
        select(DeckCard.card_id),
        select(Deck.commander_id),
        select(CardMetadata.card_id),
    )
    referenced: set[str] = set()
    for query in queries:
        referenced.update(row[0] for row in session.execute(query) if row[0])
    return referenced


def prune(session: Session, keep: set[str]) -> int:
    """Delete cards outside ``keep`` that nothing references.

    Used after a narrowed import (say, Standard only) to drop the cards that
    are no longer in scope. Anything in a collection, a deck, or with generated
    metadata attached survives regardless — those rows would otherwise become
    dangling foreign keys, and a user's collection is not ours to discard just
    because they switched formats.
    """
    protected = keep | referenced_card_ids(session)

    stale = [
        row[0]
        for row in session.execute(select(Card.oracle_id))
        if row[0] not in protected
    ]
    if not stale:
        logger.info("Prune: nothing to remove")
        return 0

    # Chunked to stay clear of parameter limits on large deletes.
    for start in range(0, len(stale), 500):
        session.execute(
            delete(Card).where(Card.oracle_id.in_(stale[start : start + 500]))
        )
    session.commit()

    logger.info("Prune: removed %d unreferenced cards", len(stale))
    return len(stale)


def user_data_counts(session: Session) -> dict[str, int]:
    """Row counts for the tables a reset would endanger."""
    return {
        "collection": session.scalar(select(func.count()).select_from(Collection)) or 0,
        "decks": session.scalar(select(func.count()).select_from(Deck)) or 0,
        "deck_cards": session.scalar(select(func.count()).select_from(DeckCard)) or 0,
    }


def reset(session: Session, *, force: bool = False) -> int:
    """Empty the card table.

    Card data is derived and can always be re-downloaded, so resetting it is
    cheap in itself. What isn't cheap is the collection and decks hanging off
    it, which is why this refuses to proceed while they exist unless the caller
    insists.
    """
    counts = user_data_counts(session)
    if any(counts.values()) and not force:
        detail = ", ".join(f"{count} {table}" for table, count in counts.items() if count)
        raise ResetRefused(
            f"Refusing to reset: the database holds user data ({detail}). "
            "Deleting cards would orphan it. Re-run with --force if you really "
            "mean to discard that."
        )

    if force and any(counts.values()):
        # Foreign keys point at cards.oracle_id, so the dependents have to go
        # first. The user was warned and asked for this explicitly.
        logger.warning("Reset --force: discarding %s", counts)
        session.execute(delete(DeckCard))
        session.execute(delete(Deck))
        session.execute(delete(Collection))

    session.execute(delete(CardMetadata))
    removed = session.scalar(select(func.count()).select_from(Card)) or 0
    session.execute(delete(Card))
    session.commit()

    logger.info("Reset: removed %d cards", removed)
    return removed
