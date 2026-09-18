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

from config import Settings
from database.models import Card, ImportRun
from knowledge_pipeline.scryfall_importer import bulk, sink
from knowledge_pipeline.scryfall_importer.formats import FormatNotEnabledError
from knowledge_pipeline.scryfall_importer.mapping import CardRow
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
        # Banned in Commander, so a commander-scoped import must skip it.
        "id": "print-3", "oracle_id": "oracle-3", "name": "Black Lotus", "layout": "normal",
        "mana_cost": "{0}", "cmc": 0.0, "type_line": "Artifact",
        "oracle_text": "{T}, Sacrifice: Add three mana of any one color.",
        "colors": [], "color_identity": [], "keywords": [],
        "legalities": {"commander": "banned", "vintage": "restricted"},
    },
    {
        # Not a real card; excluded regardless of profile.
        "id": "print-4", "oracle_id": "oracle-4", "name": "Beast", "layout": "token",
        "cmc": 0.0, "type_line": "Token Creature — Beast", "colors": ["G"],
        "color_identity": ["G"], "keywords": [], "legalities": {"commander": "not_legal"},
    },
]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite://",
        scryfall_api_base="https://api.scryfall.test",
        scryfall_bulk_type="oracle_cards",
        scryfall_cache_dir=tmp_path / "scryfall",
        import_batch_size=2,
        model_api_key="unused",
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


def stale_row() -> CardRow:
    """A card already in the database that this import won't see again."""
    return CardRow(
        oracle_id="oracle-stale", scryfall_id="print-stale", name="Fallen From Grace",
        mana_cost="{1}{B}", mana_value=2.0, oracle_text="Was legal once.",
        colors=["B"], color_identity=["B"], type_line="Enchantment",
        power=None, toughness=None, loyalty=None, defense=None, keywords=[],
        image_url=None, layout="normal", legalities={"commander": "banned"},
        updated_at=datetime(2026, 1, 1),
    )


def test_import_writes_the_commander_pool_only(settings, fake_scryfall, db_session):
    report = import_cards(settings=settings, client=fake_scryfall, session=db_session)

    # Four objects read; the token and the banned card rejected, two written.
    assert report.cards_seen == 4
    assert report.cards_accepted == 2
    assert report.cards_written == 2
    assert count_cards(db_session) == 2
    assert db_session.get(Card, "oracle-3") is None  # Black Lotus, banned
    assert db_session.get(Card, "oracle-4") is None  # token
    assert db_session.get(Card, "oracle-1") is not None


def test_commander_is_the_default_pool(settings, fake_scryfall, db_session):
    """Callers get the Commander pool without naming it, so nothing has to
    repeat the format string."""
    report = import_cards(settings=settings, client=fake_scryfall, session=db_session)

    assert report.profile == "commander"


def test_a_scaffolded_format_cannot_be_imported(settings, fake_scryfall, db_session):
    with pytest.raises(FormatNotEnabledError):
        import_cards(
            format_name="standard", settings=settings, client=fake_scryfall,
            session=db_session,
        )

    assert count_cards(db_session) == 0


def test_imported_card_keeps_the_full_legalities_map(settings, fake_scryfall, db_session):
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    card = db_session.get(Card, "oracle-1")
    assert card.legalities == {"commander": "legal", "standard": "not_legal"}
    assert card.scryfall_id == "print-1"
    assert card.layout == "normal"


def test_import_is_idempotent(settings, fake_scryfall, db_session):
    import_cards(settings=settings, client=fake_scryfall, session=db_session)
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    assert count_cards(db_session) == 2
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 2


def test_import_records_a_run(settings, fake_scryfall, db_session):
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    run = db_session.scalar(select(ImportRun))
    assert run.bulk_type == "oracle_cards"
    assert run.format_profile == "commander"
    assert run.cards_written == 2
    assert run.finished_at is not None


def test_if_newer_skips_an_already_imported_snapshot(settings, fake_scryfall, db_session):
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    report = import_cards(
        if_newer=True, settings=settings, client=fake_scryfall, session=db_session
    )

    assert report.up_to_date
    assert report.cards_written == 0
    assert "Already up to date" in report.summary()


def test_dry_run_writes_nothing(settings, fake_scryfall, db_session):
    report = import_cards(
        dry_run=True, settings=settings, client=fake_scryfall, session=db_session
    )

    assert report.cards_accepted == 2
    assert report.cards_written == 0
    assert count_cards(db_session) == 0
    assert report.summary().startswith("Dry run")
    # A dry run must not claim a snapshot as imported, or --if-newer would
    # skip the real import afterwards.
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 0


def test_limit_stops_early(settings, fake_scryfall, db_session):
    report = import_cards(
        limit=1, settings=settings, client=fake_scryfall, session=db_session
    )

    assert report.cards_written == 1


def test_bulk_file_is_cached_between_runs(settings, fake_scryfall, db_session):
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    cached = list(settings.scryfall_cache_dir.glob("*.jsonl.gz"))
    assert len(cached) == 1
    # The filename carries Scryfall's snapshot timestamp so a new snapshot
    # lands beside the old one rather than overwriting a file mid-read.
    assert cached[0].name.startswith("oracle_cards-20260817")
    assert not list(settings.scryfall_cache_dir.glob("*.partial"))


def test_missing_bulk_type_reports_what_is_available(settings, fake_scryfall, db_session):
    settings = settings.model_copy(update={"scryfall_bulk_type": "nonexistent"})

    with pytest.raises(bulk.BulkDataError) as excinfo:
        import_cards(settings=settings, client=fake_scryfall, session=db_session)

    assert "oracle_cards" in str(excinfo.value)


def test_import_report_summary_reads_cleanly(settings, fake_scryfall, db_session):
    report = import_cards(settings=settings, client=fake_scryfall, session=db_session)

    summary = report.summary()
    assert "commander" in summary
    assert "4 read" in summary
    assert "2 written" in summary


def test_prune_removes_cards_that_left_the_pool(settings, fake_scryfall, db_session):
    """A card banned since the last import is no longer in the pool; --prune is
    what clears it out."""
    sink.upsert_batches(db_session, [stale_row()], batch_size=10)

    report = import_cards(
        prune=True, settings=settings, client=fake_scryfall, session=db_session
    )

    assert report.cards_pruned == 1
    assert db_session.get(Card, "oracle-stale") is None
    assert count_cards(db_session) == 2


def test_updated_at_is_stamped_on_import(settings, fake_scryfall, db_session):
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    import_cards(settings=settings, client=fake_scryfall, session=db_session)

    card = db_session.get(Card, "oracle-1")
    assert card.updated_at >= before
