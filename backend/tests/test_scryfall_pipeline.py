"""End-to-end import against a fake Scryfall.

httpx.MockTransport serves a real gzipped JSONL payload, so the catalog
lookup, download, streaming decompress, filtering, mapping and upsert all run
for real — only the network is fake.
"""

import gzip
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select

from config import KnowledgeSettings
from database.knowledge.models import Card, ImportRun
from knowledge_pipeline.scryfall_importer import bulk
from knowledge_pipeline.scryfall_importer.pipeline import import_cards

SNAPSHOT_UPDATED_AT = "2026-08-17T09:01:54.476+00:00"

CARDS = [
    {
        "id": "print-1", "oracle_id": "oracle-1", "name": "Cultivate", "layout": "normal",
        "mana_cost": "{2}{G}", "cmc": 3.0, "type_line": "Sorcery",
        "oracle_text": "Search your library...", "colors": ["G"], "color_identity": ["G"],
        "keywords": [], "image_uris": {"normal": "https://example.test/cultivate.jpg"},
        "legalities": {"commander": "legal", "standard": "not_legal"},
    },
    {
        "id": "print-2", "oracle_id": "oracle-2", "name": "Lightning Bolt", "layout": "normal",
        "mana_cost": "{R}", "cmc": 1.0, "type_line": "Instant",
        "oracle_text": "Deal 3 damage to any target.", "colors": ["R"],
        "color_identity": ["R"], "keywords": [],
        "legalities": {"commander": "legal", "standard": "not_legal"},
    },
    {
        # Banned in Commander and restricted in Vintage. Imported anyway:
        # legality is not an import filter, and somebody owns this card.
        "id": "print-3", "oracle_id": "oracle-3", "name": "Black Lotus", "layout": "normal",
        "mana_cost": "{0}", "cmc": 0.0, "type_line": "Artifact",
        "oracle_text": "{T}, Sacrifice: Add three mana of any one color.",
        "colors": [], "color_identity": [], "keywords": [],
        "legalities": {"commander": "banned", "vintage": "restricted"},
    },
    {
        # Not a card at all. The one thing the filter still rejects.
        "id": "print-4", "oracle_id": "oracle-4", "name": "Beast", "layout": "token",
        "cmc": 0.0, "type_line": "Token Creature — Beast", "colors": ["G"],
        "color_identity": ["G"], "keywords": [], "legalities": {"commander": "not_legal"},
    },
]


@pytest.fixture
def settings(tmp_path) -> KnowledgeSettings:
    return KnowledgeSettings(
        knowledge_database_url="sqlite://",
        scryfall_api_base="https://api.scryfall.test",
        scryfall_bulk_type="oracle_cards",
        scryfall_cache_dir=tmp_path / "scryfall",
        import_batch_size=2,
        _env_file=None,
    )


@pytest.fixture
def fake_scryfall() -> httpx.Client:
    payload = gzip.compress(
        "\n".join(json.dumps(card) for card in CARDS).encode("utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bulk-data":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "object": "bulk_data",
                            "id": "catalog-id",
                            "type": "oracle_cards",
                            "name": "Oracle Cards",
                            "updated_at": SNAPSHOT_UPDATED_AT,
                            "jsonl_download_uri": "https://data.scryfall.test/oracle.jsonl.gz",
                            "compressed_size": len(payload),
                        }
                    ],
                },
            )
        if request.url.host == "data.scryfall.test":
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def count_cards(session) -> int:
    return session.scalar(select(func.count()).select_from(Card))


def test_import_writes_every_card_object(settings, fake_scryfall, knowledge_session):
    report = import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    # Four objects read; only the token rejected, three written.
    assert report.cards_seen == 4
    assert report.cards_accepted == 3
    assert report.cards_written == 3
    assert count_cards(knowledge_session) == 3
    assert knowledge_session.get(Card, "oracle-4") is None  # token
    assert knowledge_session.get(Card, "oracle-1") is not None


def test_legality_does_not_filter_the_import(settings, fake_scryfall, knowledge_session):
    """The whole point of the change: a card banned in Commander is still a
    card somebody owns, and the client decides what's playable."""
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    black_lotus = knowledge_session.get(Card, "oracle-3")
    assert black_lotus is not None
    assert black_lotus.legalities == {"commander": "banned", "vintage": "restricted"}


def test_imported_card_keeps_the_full_legalities_map(settings, fake_scryfall, knowledge_session):
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    card = knowledge_session.get(Card, "oracle-1")
    assert card.legalities == {"commander": "legal", "standard": "not_legal"}
    assert card.scryfall_id == "print-1"
    assert card.layout == "normal"


def test_import_is_idempotent(settings, fake_scryfall, knowledge_session):
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    assert count_cards(knowledge_session) == 3
    assert knowledge_session.scalar(select(func.count()).select_from(ImportRun)) == 2


def test_import_records_a_run(settings, fake_scryfall, knowledge_session):
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    run = knowledge_session.scalar(select(ImportRun))
    assert run.bulk_type == "oracle_cards"
    assert run.cards_written == 3
    assert run.finished_at is not None


def test_if_newer_skips_an_already_imported_snapshot(settings, fake_scryfall, knowledge_session):
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    report = import_cards(
        if_newer=True, settings=settings, client=fake_scryfall, session=knowledge_session
    )

    assert report.up_to_date
    assert report.cards_written == 0
    assert "Already up to date" in report.summary()


def test_dry_run_writes_nothing(settings, fake_scryfall, knowledge_session):
    report = import_cards(
        dry_run=True, settings=settings, client=fake_scryfall, session=knowledge_session
    )

    assert report.cards_accepted == 3
    assert report.cards_written == 0
    assert count_cards(knowledge_session) == 0
    assert report.summary().startswith("Dry run")
    # A dry run must not claim a snapshot as imported, or --if-newer would
    # skip the real import afterwards.
    assert knowledge_session.scalar(select(func.count()).select_from(ImportRun)) == 0


def test_limit_stops_early(settings, fake_scryfall, knowledge_session):
    report = import_cards(
        limit=1, settings=settings, client=fake_scryfall, session=knowledge_session
    )

    assert report.cards_written == 1


def test_bulk_file_is_cached_between_runs(settings, fake_scryfall, knowledge_session):
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    cached = list(settings.scryfall_cache_dir.glob("*.jsonl.gz"))
    assert len(cached) == 1
    # The filename carries Scryfall's snapshot timestamp so a new snapshot
    # lands beside the old one rather than overwriting a file mid-read.
    assert cached[0].name.startswith("oracle_cards-20260817")
    assert not list(settings.scryfall_cache_dir.glob("*.partial"))


def test_missing_bulk_type_reports_what_is_available(settings, fake_scryfall, knowledge_session):
    settings = settings.model_copy(update={"scryfall_bulk_type": "nonexistent"})

    with pytest.raises(bulk.BulkDataError) as excinfo:
        import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    assert "oracle_cards" in str(excinfo.value)


def test_import_report_summary_reads_cleanly(settings, fake_scryfall, knowledge_session):
    report = import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    summary = report.summary()
    assert "4 read" in summary
    assert "3 cards" in summary
    assert "3 written" in summary


def test_updated_at_is_stamped_on_import(settings, fake_scryfall, knowledge_session):
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    import_cards(settings=settings, client=fake_scryfall, session=knowledge_session)

    card = knowledge_session.get(Card, "oracle-1")
    assert card.updated_at >= before
