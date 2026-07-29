from datetime import datetime

from sqlalchemy import ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Card(Base):
    """Source of truth, imported directly from Scryfall. Never AI-generated."""

    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(primary_key=True)
    oracle_id: Mapped[str]
    name: Mapped[str]
    mana_cost: Mapped[str]
    mana_value: Mapped[float]
    oracle_text: Mapped[str]
    colors: Mapped[list[str]] = mapped_column(JSON)
    color_identity: Mapped[list[str]] = mapped_column(JSON)
    type_line: Mapped[str]
    keywords: Mapped[list[str]] = mapped_column(JSON)
    image_url: Mapped[str]


class CardMetadata(Base):
    """AI-generated strategic information, produced by the Knowledge Pipeline."""

    __tablename__ = "card_metadata"

    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), primary_key=True)
    summary: Mapped[str]
    roles: Mapped[list[str]] = mapped_column(JSON)
    themes: Mapped[list[str]] = mapped_column(JSON)
    game_stage: Mapped[str]
    power_rating: Mapped[float]
    strengths: Mapped[list[str]] = mapped_column(JSON)
    weaknesses: Mapped[list[str]] = mapped_column(JSON)
    synergy_tags: Mapped[list[str]] = mapped_column(JSON)


class Collection(Base):
    """Tracks the cards a user owns."""

    __tablename__ = "collection"

    user_id: Mapped[str] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), primary_key=True)
    quantity: Mapped[int]


class Deck(Base):
    """A generated deck."""

    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str]
    commander_id: Mapped[str] = mapped_column(ForeignKey("cards.id"))
    created_at: Mapped[datetime]


class DeckCard(Base):
    """Cards inside a generated deck."""

    __tablename__ = "deck_cards"

    deck_id: Mapped[str] = mapped_column(ForeignKey("decks.id"), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), primary_key=True)
    owned: Mapped[bool]
    proxy: Mapped[bool]
