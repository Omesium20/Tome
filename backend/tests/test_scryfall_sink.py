"""Writing and resetting the card table.

The safety property that matters is idempotence: a refresh must run weekly
without disturbing anything. An import is upsert-only now — `prune` is gone
along with the format scoping that justified it, so nothing an import does
can remove a row.

Reset used to refuse while collections and decks held rows. Those tables are
in a different database on the user's machine, so the check couldn't see what
it protected and was removed rather than left returning zero. The
confirmation that replaced it guards the wrong-database case instead, and
lives in the CLI.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select

from database.knowledge.models import Card, CardMetadata
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


def make_metadata(card_id: str) -> CardMetadata:
    return CardMetadata(
        card_id=card_id,
        summary="A card.",
        roles=["ramp"],
        themes=["counters"],
        game_stage="early",
        power_rating=5.0,
        strengths=[],
        weaknesses=[],
        synergy_tags=[],
        updated_at=datetime(2026, 1, 1),
    )


def count_cards(session) -> int:
    return session.scalar(select(func.count()).select_from(Card))


def test_upsert_inserts_rows(knowledge_session):
    written = sink.upsert_batches(
        knowledge_session, [make_row("a"), make_row("b")], batch_size=10
    )

    assert written == 2
    assert count_cards(knowledge_session) == 2


def test_upsert_is_idempotent(knowledge_session):
    rows = [make_row("a"), make_row("b")]
    sink.upsert_batches(knowledge_session, rows, batch_size=10)
    sink.upsert_batches(knowledge_session, rows, batch_size=10)

    # Re-running an import must not duplicate anything — this is what makes a
    # weekly refresh safe.
    assert count_cards(knowledge_session) == 2


def test_upsert_updates_changed_fields(knowledge_session):
    sink.upsert_batches(knowledge_session, [make_row("a", name="Old Name")], batch_size=10)
    sink.upsert_batches(knowledge_session, [make_row("a", name="New Name")], batch_size=10)

    card = knowledge_session.get(Card, "a")
    assert card.name == "New Name"
    assert count_cards(knowledge_session) == 1


def test_upsert_collapses_duplicate_oracle_ids_within_a_batch(knowledge_session):
    # Reversible cards and some promos appear twice under one oracle_id.
    # Postgres rejects an ON CONFLICT statement that hits the same key twice.
    written = sink.upsert_batches(
        knowledge_session, [make_row("a", name="First"), make_row("a", name="Second")],
        batch_size=10,
    )

    assert written == 1
    assert knowledge_session.get(Card, "a").name == "Second"


def test_upsert_respects_batch_size(knowledge_session):
    rows = [make_row(str(i)) for i in range(25)]
    written = sink.upsert_batches(knowledge_session, rows, batch_size=10)

    assert written == 25
    assert count_cards(knowledge_session) == 25


def test_reset_empties_the_corpus(knowledge_session):
    sink.upsert_batches(knowledge_session, [make_row("a"), make_row("b")], batch_size=10)

    removed = sink.reset(knowledge_session)

    assert removed == 2
    assert count_cards(knowledge_session) == 0


def test_reset_takes_metadata_with_it(knowledge_session):
    """card_metadata has a real FK to cards, so it has to go first or the
    delete fails. There is nothing else in this database to protect."""
    sink.upsert_batches(knowledge_session, [make_row("a")], batch_size=10)
    knowledge_session.add(make_metadata("a"))
    knowledge_session.commit()

    removed = sink.reset(knowledge_session)

    assert removed == 1
    assert count_cards(knowledge_session) == 0
    assert knowledge_session.scalar(select(func.count()).select_from(CardMetadata)) == 0


def test_card_count_reports_what_a_reset_would_delete(knowledge_session):
    assert sink.card_count(knowledge_session) == 0
    sink.upsert_batches(knowledge_session, [make_row("a"), make_row("b")], batch_size=10)
    assert sink.card_count(knowledge_session) == 2
