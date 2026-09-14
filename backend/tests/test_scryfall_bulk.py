"""Talking to Scryfall: required headers, backoff, and the download cache."""

import gzip
import json

import httpx
import pytest

from config import Settings
from knowledge_pipeline.scryfall_importer import bulk


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        scryfall_api_base="https://api.scryfall.test",
        scryfall_cache_dir=tmp_path / "cache",
        scryfall_user_agent="Tome/test (+https://example.test)",
        _env_file=None,
    )


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Backoff waits 30s for real; tests shouldn't."""
    monkeypatch.setattr(bulk.time, "sleep", lambda _seconds: None)


def catalog_payload(**overrides) -> dict:
    entry = {
        "object": "bulk_data",
        "type": "oracle_cards",
        "name": "Oracle Cards",
        "updated_at": "2026-08-17T09:01:54.476+00:00",
        "jsonl_download_uri": "https://data.scryfall.test/oracle.jsonl.gz",
        "compressed_size": 24_408_680,
    }
    entry.update(overrides)
    return {"object": "list", "data": [entry]}


def client_returning(*responses: httpx.Response, record: list | None = None) -> httpx.Client:
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return next(queue)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "Tome/test (+https://example.test)", "Accept": "application/json"},
    )


def test_required_headers_are_sent(settings):
    """Scryfall requires a descriptive User-Agent and an Accept header."""
    seen: list[httpx.Request] = []
    with client_returning(httpx.Response(200, json=catalog_payload()), record=seen) as client:
        bulk.fetch_catalog_entry(settings, client)

    assert seen[0].headers["user-agent"] == "Tome/test (+https://example.test)"
    assert seen[0].headers["accept"] == "application/json"


def test_open_client_carries_the_configured_user_agent(settings):
    with bulk.open_client(settings) as client:
        assert client.headers["user-agent"] == "Tome/test (+https://example.test)"
        assert client.headers["accept"] == "application/json"


def test_catalog_entry_is_parsed(settings):
    with client_returning(httpx.Response(200, json=catalog_payload())) as client:
        entry = bulk.fetch_catalog_entry(settings, client)

    assert entry.type == "oracle_cards"
    assert entry.download_uri == "https://data.scryfall.test/oracle.jsonl.gz"
    # Normalized to naive UTC to match the DateTime columns.
    assert entry.updated_at.tzinfo is None
    assert entry.updated_at.isoformat() == "2026-08-17T09:01:54.476000"
    assert entry.cache_filename == "oracle_cards-20260817090154.jsonl.gz"


def test_jsonl_uri_is_preferred_over_the_legacy_single_array(settings):
    payload = catalog_payload(download_uri="https://data.scryfall.test/legacy.json")
    with client_returning(httpx.Response(200, json=payload)) as client:
        assert bulk.fetch_catalog_entry(settings, client).download_uri.endswith(".jsonl.gz")


def test_rate_limit_is_retried(settings):
    with client_returning(
        httpx.Response(429),
        httpx.Response(200, json=catalog_payload()),
    ) as client:
        assert bulk.fetch_catalog_entry(settings, client).type == "oracle_cards"


def test_server_error_is_retried(settings):
    with client_returning(
        httpx.Response(503),
        httpx.Response(200, json=catalog_payload()),
    ) as client:
        assert bulk.fetch_catalog_entry(settings, client).type == "oracle_cards"


def test_persistent_rate_limiting_eventually_gives_up(settings):
    with client_returning(*[httpx.Response(429) for _ in range(4)]) as client:
        with pytest.raises(bulk.BulkDataError) as excinfo:
            bulk.fetch_catalog_entry(settings, client)

    assert "429" in str(excinfo.value)


def test_client_errors_are_not_retried(settings):
    # A 404 won't fix itself; retrying just wastes the rate-limit budget.
    with client_returning(httpx.Response(404)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            bulk.fetch_catalog_entry(settings, client)


def test_download_caches_and_reuses(settings):
    payload = gzip.compress(b'{"oracle_id": "x"}\n')
    calls: list[httpx.Request] = []

    with client_returning(
        httpx.Response(200, json=catalog_payload()),
        httpx.Response(200, content=payload),
        record=calls,
    ) as client:
        entry = bulk.fetch_catalog_entry(settings, client)
        first = bulk.download(entry, settings, client)
        # Only two responses were queued; a second download that hit the network
        # would raise StopIteration.
        second = bulk.download(entry, settings, client)

    assert first == second
    assert first.read_bytes() == payload
    assert len(calls) == 2
    # An interrupted download must not be left looking like a complete one.
    assert not list(settings.scryfall_cache_dir.glob("*.partial"))


def test_stream_cards_decodes_line_by_line(settings, tmp_path):
    cards = [{"oracle_id": "a", "name": "A"}, {"oracle_id": "b", "name": "B"}]
    path = tmp_path / "cards.jsonl.gz"
    path.write_bytes(gzip.compress("\n".join(json.dumps(c) for c in cards).encode()))

    assert list(bulk.stream_cards(path)) == cards


def test_stream_cards_tolerates_the_legacy_json_array_format(tmp_path):
    # download_uri (as opposed to jsonl_download_uri) serves one big array.
    body = '[\n{"oracle_id": "a"},\n{"oracle_id": "b"}\n]\n'
    path = tmp_path / "legacy.json.gz"
    path.write_bytes(gzip.compress(body.encode()))

    assert list(bulk.stream_cards(path)) == [{"oracle_id": "a"}, {"oracle_id": "b"}]


def test_unknown_bulk_type_reports_what_is_available(settings):
    settings = settings.model_copy(update={"scryfall_bulk_type": "rulings"})

    with client_returning(httpx.Response(200, json=catalog_payload())) as client:
        with pytest.raises(bulk.BulkDataError) as excinfo:
            bulk.fetch_catalog_entry(settings, client)

    assert "rulings" in str(excinfo.value)
    assert "oracle_cards" in str(excinfo.value)
