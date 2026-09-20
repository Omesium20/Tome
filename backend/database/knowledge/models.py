"""Entities in the shared cloud corpus (`docs/data-model.md#knowledge-database-cloud`).

Written once, centrally, by the Knowledge Pipeline; identical for every user.
Foreign keys between these tables are real — they all live in the same
Postgres. References *into* this schema from a user's machine are not, and
cannot be; see `database/local/models.py`.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the knowledge database only.

    Separate from the local plane's base so the two schemas migrate
    independently and neither Alembic environment can see the other's tables.
    """


class Card(Base):
    """Source of truth, imported directly from Scryfall. Never AI-generated.

    Keyed by ``oracle_id``, not by Scryfall's per-printing ``id``. The
    oracle_cards bulk file returns whichever printing Scryfall currently
    considers most recognizable, and that choice changes when a card is
    reprinted — so keying on the printing id would rotate the primary key on
    an ordinary refresh and orphan every collection and deck row pointing at
    it. ``oracle_id`` is stable across reprints.

    That stability is now load-bearing rather than merely convenient: user
    rows reference these ids from a different database on a machine we cannot
    see, where no foreign key can cascade a change.
    """

    __tablename__ = "cards"

    oracle_id: Mapped[str] = mapped_column(primary_key=True)
    # The printing Scryfall picked for this oracle id. Kept for image and
    # permalink lookups; not an identity, and expected to change over time.
    scryfall_id: Mapped[str]
    name: Mapped[str] = mapped_column(index=True)
    # Genuinely absent at the card root for multi-faced layouts, and for cards
    # with no mana cost at all — see the face-merge rules in the importer.
    mana_cost: Mapped[str | None]
    mana_value: Mapped[float]
    oracle_text: Mapped[str | None]
    colors: Mapped[list[str]] = mapped_column(JSON)
    color_identity: Mapped[list[str]] = mapped_column(JSON)
    type_line: Mapped[str]
    # Nullable: power/toughness are creature (and vehicle) only, loyalty is
    # planeswalker only, defense is battle only. Stored as Scryfall's raw
    # string ("*", "1+*" are real values) rather than parsed to a number.
    power: Mapped[str | None]
    toughness: Mapped[str | None]
    loyalty: Mapped[str | None]
    defense: Mapped[str | None]
    keywords: Mapped[list[str]] = mapped_column(JSON)
    image_url: Mapped[str | None]
    # Scryfall's layout discriminator ("normal", "transform", "modal_dfc", ...).
    # Retained because the face-merge rules depend on it and downstream stages
    # need to know a card has a back side.
    layout: Mapped[str]
    # Scryfall's full legalities map, stored whole: {"commander": "legal", ...}.
    # One column rather than a boolean per format, so a new format appearing
    # upstream needs no migration.
    legalities: Mapped[dict[str, str]] = mapped_column(JSON)
    # When this row was last written by the importer.
    updated_at: Mapped[datetime]


class CardMetadata(Base):
    """AI-generated strategic information, produced by the Knowledge Pipeline."""

    __tablename__ = "card_metadata"

    # A real foreign key: both tables are in this database.
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.oracle_id"), primary_key=True
    )
    summary: Mapped[str]
    roles: Mapped[list[str]] = mapped_column(JSON)
    themes: Mapped[list[str]] = mapped_column(JSON)
    game_stage: Mapped[str]
    power_rating: Mapped[float]
    strengths: Mapped[list[str]] = mapped_column(JSON)
    weaknesses: Mapped[list[str]] = mapped_column(JSON)
    synergy_tags: Mapped[list[str]] = mapped_column(JSON)
    # When the metadata generator last wrote this row. Lets a refresh detect
    # metadata that predates the Card.updated_at it describes (e.g. after an
    # oracle text errata) and needs regenerating.
    updated_at: Mapped[datetime]


class ImportRun(Base):
    """One execution of the Scryfall importer.

    Exists so a scheduled refresh can compare Scryfall's ``updated_at`` against
    the last successful run and exit early when the bulk file hasn't changed
    (``--if-newer``), and so an operator can see what a past import actually did.

    Two columns were dropped when format scoping was removed: ``format_profile``
    could only ever hold one value once every import took the whole pool, and
    ``cards_pruned`` outlived ``--prune`` itself. An import only ever adds and
    updates now, so there is no deletion count to record.
    """

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bulk_type: Mapped[str]
    # Scryfall's own timestamp for the bulk file consumed by this run.
    source_updated_at: Mapped[datetime]
    cards_seen: Mapped[int]
    cards_written: Mapped[int]
    cards_skipped: Mapped[int]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
