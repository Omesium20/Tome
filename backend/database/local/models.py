"""Entities on the user's own machine (`docs/data-model.md#local-database-client`).

Private to one user. Never transmitted anywhere — the client asks the
Knowledge API about *cards*, never about the user.

**No foreign key here reaches `cards`.** `Collection.card_id`,
`Deck.commander_id` and `DeckCard.card_id` are *logical* references to
`cards.oracle_id` in a different database that this engine cannot see, so
nothing but application code will ever enforce them. Two rules follow:

- Never key a user row on anything but ``oracle_id``. It is reprint-stable;
  a printing id would rotate upstream and orphan user data across a boundary
  where nothing cascades.
- Treat a card that does not resolve as a normal case, not an error. A
  restored backup, or a card printed since the last refresh, will reference
  something the client has never seen. Resolve through the Knowledge API in
  batch and render a placeholder — never crash.
"""

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the local database only.

    Separate from the knowledge plane's base so the two schemas migrate
    independently and neither Alembic environment can see the other's tables.
    """


class Collection(Base):
    """Tracks the cards a user owns.

    Populated by CSV import, which arrives as card *names* and resolves them
    to oracle ids through the Knowledge API. Names that are ambiguous or
    unmatched are surfaced to the user rather than guessed at.
    """

    __tablename__ = "collection"

    # Vestigial in the local-first model — a local database has exactly one
    # user. Kept as the seam a future sync or multi-user deployment needs.
    user_id: Mapped[str] = mapped_column(primary_key=True)
    # Logical reference to cards.oracle_id in the knowledge database.
    card_id: Mapped[str] = mapped_column(primary_key=True)
    quantity: Mapped[int]


class Deck(Base):
    """A user's saved deck -- AI-generated or built by hand."""

    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str]
    # User-chosen on first save.
    name: Mapped[str]
    # Logical reference to cards.oracle_id in the knowledge database.
    # Nullable: a work-in-progress deck may not have picked a commander yet.
    commander_id: Mapped[str | None]
    created_at: Mapped[datetime]
    # Saving an existing deck updates in place rather than creating a duplicate.
    updated_at: Mapped[datetime]


class DeckCard(Base):
    """Cards inside a saved deck."""

    __tablename__ = "deck_cards"

    # A real foreign key: both tables are in this database.
    deck_id: Mapped[str] = mapped_column(ForeignKey("decks.id"), primary_key=True)
    # Logical reference to cards.oracle_id in the knowledge database.
    card_id: Mapped[str] = mapped_column(primary_key=True)
    # 1 for everything except basic lands (singleton format).
    quantity: Mapped[int]
    owned: Mapped[bool]
    proxy: Mapped[bool]
