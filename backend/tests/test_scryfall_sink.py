"""Writing, pruning and resetting the card table.

The safety properties here are the point: a refresh must never disturb a
collection, and a prune must never orphan one.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from database.models import Card, Collection, Deck, DeckCard
from knowledge_pipeline.scryfall_importer import sink
from knowledge_pipeline.scryfall_importer.mapping import CardRow


def make_row(oracle_id: str, name: str = "Test Card", **overrides) -> CardRow:
    values = {
        "oracle_id": oracle_id,
        "scryfall_id": f"print-{oracle_id}",
        "name": name,
        "mana_cost": "{1}{G}",
        "mana_value": 2.0,
        "oracle_text": "Do a thing.",
        "colors": ["G"],
        "color_identity": ["G"],
        "type_line": "Creature — Test",
        "power": "2",
        "toughness": "2",
        "loyalty": None,
        "defense": None,
        "keywords": [],
        "image_url": None,
        "layout": "normal",
        "legalities": {"commander": "legal"},
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    }
    values.update(overrides)
    return CardRow(**values)


def count_cards(session) -> int:
    return session.scalar(select(func.count()).select_from(Card))


def test_upsert_inserts_rows(db_session):
    written, seen = sink.upsert_batches(
        db_session, [make_row("a"), make_row("b")], batch_size=10
    )

    assert written == 2
    assert seen == {"a", "b"}
    assert count_cards(db_session) == 2


def test_upsert_is_idempotent(db_session):
    rows = [make_row("a"), make_row("b")]
    sink.upsert_batches(db_session, rows, batch_size=10)
    sink.upsert_batches(db_session, rows, batch_size=10)

    # Re-running an import must not duplicate anything — this is what makes a
    # weekly refresh safe.
    assert count_cards(db_session) == 2


def test_upsert_updates_changed_fields(db_session):
    sink.upsert_batches(db_session, [make_row("a", name="Old Name")], batch_size=10)
    sink.upsert_batches(db_session, [make_row("a", name="New Name")], batch_size=10)

    card = db_session.get(Card, "a")
    assert card.name == "New Name"
    assert count_cards(db_session) == 1


def test_upsert_collapses_duplicate_oracle_ids_within_a_batch(db_session):
    # Reversible cards and some promos appear twice under one oracle_id.
    # Postgres rejects an ON CONFLICT statement that hits the same key twice.
    written, _ = sink.upsert_batches(
        db_session, [make_row("a", name="First"), make_row("a", name="Second")],
        batch_size=10,
    )

    assert written == 1
    assert db_session.get(Card, "a").name == "Second"


def test_upsert_respects_batch_size(db_session):
    rows = [make_row(str(i)) for i in range(25)]
    written, seen = sink.upsert_batches(db_session, rows, batch_size=10)

    assert written == 25
    assert len(seen) == 25


def test_prune_removes_cards_outside_the_import(db_session):
    sink.upsert_batches(db_session, [make_row("a"), make_row("b")], batch_size=10)

    removed = sink.prune(db_session, keep={"a"})

    assert removed == 1
    assert count_cards(db_session) == 1
    assert db_session.get(Card, "a") is not None


def test_prune_keeps_cards_the_user_owns(db_session):
    """A collection is the user's physical cards and outlives any format switch."""
    sink.upsert_batches(db_session, [make_row("a"), make_row("b")], batch_size=10)
    db_session.add(Collection(user_id="u1", card_id="b", quantity=2))
    db_session.commit()

    removed = sink.prune(db_session, keep={"a"})

    assert removed == 0
    assert db_session.get(Card, "b") is not None


def test_prune_keeps_cards_used_in_decks(db_session):
    sink.upsert_batches(
        db_session, [make_row("a"), make_row("cmdr"), make_row("in-deck")], batch_size=10
    )
    db_session.add(
        Deck(id="d1", user_id="u1", commander_id="cmdr", created_at=datetime(2026, 1, 1))
    )
    db_session.add(DeckCard(deck_id="d1", card_id="in-deck", owned=True, proxy=False))
    db_session.commit()

    removed = sink.prune(db_session, keep=set())

    assert removed == 1  # only "a"
    assert db_session.get(Card, "cmdr") is not None
    assert db_session.get(Card, "in-deck") is not None


def test_reset_refuses_when_user_data_exists(db_session):
    sink.upsert_batches(db_session, [make_row("a")], batch_size=10)
    db_session.add(Collection(user_id="u1", card_id="a", quantity=1))
    db_session.commit()

    with pytest.raises(sink.ResetRefused) as excinfo:
        sink.reset(db_session, force=False)

    assert "collection" in str(excinfo.value)
    assert count_cards(db_session) == 1


def test_reset_proceeds_on_an_empty_database(db_session):
    sink.upsert_batches(db_session, [make_row("a"), make_row("b")], batch_size=10)

    removed = sink.reset(db_session, force=False)

    assert removed == 2
    assert count_cards(db_session) == 0


def test_reset_with_force_discards_user_data(db_session):
    sink.upsert_batches(db_session, [make_row("a")], batch_size=10)
    db_session.add(Collection(user_id="u1", card_id="a", quantity=1))
    db_session.commit()

    removed = sink.reset(db_session, force=True)

    assert removed == 1
    assert count_cards(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(Collection)) == 0
