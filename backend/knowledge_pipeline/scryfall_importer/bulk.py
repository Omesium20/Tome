"""Fetching Scryfall's bulk card data.

Scryfall publishes daily snapshots as gzipped JSON Lines and asks consumers to
stream them rather than load them whole, so this module downloads the archive
to a local cache and then yields one decoded card per line.

Caching to disk (rather than streaming straight from the socket into the
parser) buys three things worth the 24 MB: a failed import can be retried
without re-downloading, a developer can work offline, and the download stage is
independently inspectable.

Rate limits, from https://scryfall.com/docs/api/rate-limits:
  * ``data.scryfall.io`` file downloads are not rate limited.
  * ``api.scryfall.com`` allows ~10 requests/second; we make one call per run.
  * A 429 imposes a 30-second restriction, so backing off by less is pointless.
"""

import gzip
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import KnowledgeSettings

logger = logging.getLogger(__name__)

# Scryfall answers a 429 with a 30-second lockout; retrying sooner just burns
# another strike against a temporary ban.
_RATE_LIMIT_BACKOFF_SECONDS = 30.0
_MAX_ATTEMPTS = 4


class BulkDataError(RuntimeError):
    """Raised when Scryfall's bulk data can't be located or downloaded."""


@dataclass(frozen=True)
class BulkDataEntry:
    """One entry from Scryfall's ``/bulk-data`` catalog."""

    type: str
    name: str
    updated_at: datetime
    download_uri: str
    compressed_size: int | None

    @property
    def cache_filename(self) -> str:
        stamp = self.updated_at.strftime("%Y%m%d%H%M%S")
        return f"{self.type}-{stamp}.jsonl.gz"


def _headers(settings: KnowledgeSettings) -> dict[str, str]:
    # Scryfall requires both of these and explicitly asks that the User-Agent
    # identify the application rather than being a default library string.
    # https://scryfall.com/docs/api ("Required Headers")
    return {
        "User-Agent": settings.scryfall_user_agent,
        "Accept": "application/json",
    }


def _get_with_backoff(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET ``url``, retrying on rate limits and transient server errors."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response = client.get(url, **kwargs)
        if response.status_code == 429:
            wait = _RATE_LIMIT_BACKOFF_SECONDS
        elif response.status_code >= 500:
            wait = min(2.0 ** attempt, _RATE_LIMIT_BACKOFF_SECONDS)
        else:
            response.raise_for_status()
            return response

        if attempt == _MAX_ATTEMPTS:
            raise BulkDataError(
                f"Scryfall returned {response.status_code} for {url} "
                f"after {_MAX_ATTEMPTS} attempts"
            )
        logger.warning(
            "Scryfall returned %s for %s; retrying in %.0fs (attempt %d/%d)",
            response.status_code, url, wait, attempt, _MAX_ATTEMPTS,
        )
        time.sleep(wait)

    raise AssertionError("unreachable")  # pragma: no cover


def fetch_catalog_entry(settings: KnowledgeSettings, client: httpx.Client) -> BulkDataEntry:
    """Find the catalog entry for the configured bulk type.

    Defaults to ``oracle_cards``: one object per Oracle ID, which is exactly the
    grain our Card model stores. ``default_cards``/``all_cards`` carry every
    printing and every language, which we'd only collapse back down again.
    """
    url = f"{settings.scryfall_api_base.rstrip('/')}/bulk-data"
    logger.info("Fetching Scryfall bulk data catalog from %s", url)
    payload = _get_with_backoff(client, url).json()

    wanted = settings.scryfall_bulk_type
    for item in payload.get("data", []):
        if item.get("type") != wanted:
            continue
        # Scryfall serves .jsonl.gz via jsonl_download_uri; download_uri is the
        # older single-JSON-array form. Prefer JSONL — it streams line by line.
        uri = item.get("jsonl_download_uri") or item.get("download_uri")
        if not uri:
            raise BulkDataError(f"Bulk entry {wanted!r} has no download URI")
        return BulkDataEntry(
            type=item["type"],
            name=item.get("name", item["type"]),
            updated_at=_parse_timestamp(item["updated_at"]),
            download_uri=uri,
            compressed_size=item.get("compressed_size"),
        )

    available = ", ".join(sorted(i.get("type", "?") for i in payload.get("data", [])))
    raise BulkDataError(
        f"Scryfall has no bulk data of type {wanted!r}. Available: {available}"
    )


def _parse_timestamp(raw: str) -> datetime:
    """Parse Scryfall's ISO timestamp into naive UTC.

    Scryfall emits "+00:00" offsets. The timestamp columns are plain
    ``DateTime``, so the offset is normalized away here rather than leaving an
    aware datetime to blow up on comparison against a value read back out.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def download(
    entry: BulkDataEntry,
    settings: KnowledgeSettings,
    client: httpx.Client,
    *,
    force: bool = False,
) -> Path:
    """Download ``entry`` into the cache directory and return the local path.

    The filename embeds Scryfall's ``updated_at``, so a re-run against an
    unchanged snapshot is a no-op and a changed snapshot lands beside it rather
    than overwriting a file that may be mid-read.
    """
    cache_dir = Path(settings.scryfall_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / entry.cache_filename

    if destination.exists() and not force:
        logger.info("Using cached bulk file %s", destination)
        return destination

    size_note = (
        f" (~{entry.compressed_size / 1_048_576:.1f} MB)" if entry.compressed_size else ""
    )
    logger.info("Downloading %s%s -> %s", entry.download_uri, size_note, destination)

    # Write to a temp name first so an interrupted download can't be mistaken
    # for a complete cache entry on the next run.
    partial = destination.with_suffix(destination.suffix + ".partial")
    with client.stream("GET", entry.download_uri, timeout=None) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1 << 20):
                handle.write(chunk)
    partial.replace(destination)

    logger.info("Downloaded %.1f MB", destination.stat().st_size / 1_048_576)
    return destination


def stream_cards(path: Path) -> Iterator[dict]:
    """Yield one decoded Scryfall card object per line of a gzipped JSONL file.

    Never materializes the archive: the caller can pipe this straight into
    filtering and batched writes at constant memory.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().rstrip(",")
            # Tolerate the legacy single-array bulk format's brackets, so a
            # download_uri fallback doesn't blow up the parser.
            if not line or line in ("[", "]"):
                continue
            yield json.loads(line)


def open_client(settings: KnowledgeSettings) -> httpx.Client:
    """An httpx client carrying the headers Scryfall requires."""
    return httpx.Client(headers=_headers(settings), follow_redirects=True, timeout=60.0)
