"""Wiring the import stages together.

    catalog -> download -> stream -> filter -> map -> upsert

Each stage is a generator, so the corpus is never held in memory: a card is
decoded, tested, mapped, and handed to the batched writer, one at a time.

**An import is upsert-only — it can add and update, never delete.** Worth
stating as a property rather than leaving as an accident: this writes to a
corpus every client reads, and deleting a row here silently breaks a logical
reference on a machine we can't see. `--reset` is the one destructive path,
and it is not part of an import (see `__main__.py`).
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from config import KnowledgeSettings, get_knowledge_settings
from database.knowledge.models import ImportRun
from database.knowledge.session import new_session

from . import bulk, sink
from .card_filter import is_card
from .mapping import CardRow, to_card_row, utcnow

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    """What a run actually did."""

    bulk_type: str
    source_updated_at: datetime | None = None
    cards_seen: int = 0
    cards_accepted: int = 0
    cards_written: int = 0
    cards_skipped: int = 0
    dry_run: bool = False
    up_to_date: bool = False
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()

    def summary(self) -> str:
        if self.up_to_date:
            return (
                f"Already up to date — Scryfall's {self.bulk_type} snapshot "
                f"({self.source_updated_at:%Y-%m-%d %H:%M UTC}) has already been imported."
            )
        prefix = "Dry run" if self.dry_run else "Import"
        parts = [
            f"{prefix} complete: {self.cards_seen:,} read",
            f"{self.cards_accepted:,} cards",
            f"{self.cards_written:,} written",
        ]
        if self.cards_skipped:
            parts.append(f"{self.cards_skipped:,} unmappable")
        parts.append(f"{self.elapsed_seconds:.1f}s")
        return ", ".join(parts)


def _last_run(session: Session, bulk_type: str) -> ImportRun | None:
    return session.scalar(
        select(ImportRun)
        .where(ImportRun.bulk_type == bulk_type, ImportRun.finished_at.is_not(None))
        .order_by(desc(ImportRun.source_updated_at))
        .limit(1)
    )


def _card_rows(
    cards: Iterator[dict],
    report: ImportReport,
    *,
    limit: int | None,
    imported_at: datetime,
) -> Iterator[CardRow]:
    """Filter and map the raw stream, counting as it goes."""
    for raw in cards:
        report.cards_seen += 1

        if not is_card(raw):
            continue
        report.cards_accepted += 1

        row = to_card_row(raw, imported_at=imported_at)
        if row is None:
            report.cards_skipped += 1
            logger.debug("Skipping unmappable card %r", raw.get("name"))
            continue

        yield row

        if limit is not None and report.cards_accepted >= limit:
            logger.info("Stopping early at --limit %d", limit)
            return


def import_cards(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    if_newer: bool = False,
    force_download: bool = False,
    settings: KnowledgeSettings | None = None,
    client: httpx.Client | None = None,
    session: Session | None = None,
) -> ImportReport:
    """Import Scryfall's bulk card data into the ``cards`` table.

    Every card object in the file is imported, with its full legality map.
    There is no format parameter — see `card_filter.py` for why. Filter by
    format downstream instead: at the AI stages, or on the client.

    Args:
        dry_run: Read, filter and map everything, but write nothing.
        limit: Stop after this many cards. For development.
        if_newer: Exit early if Scryfall's snapshot has already been imported.
        force_download: Re-download even when a matching cache file exists.

    The ``settings``/``client``/``session`` parameters exist so tests can inject
    fakes; production callers pass none of them.
    """
    settings = settings or get_knowledge_settings()
    report = ImportReport(bulk_type=settings.scryfall_bulk_type, dry_run=dry_run)

    owns_client = client is None
    owns_session = session is None
    client = client or bulk.open_client(settings)
    session = session or new_session()

    try:
        entry = bulk.fetch_catalog_entry(settings, client)
        report.source_updated_at = entry.updated_at
        logger.info(
            "Scryfall %s snapshot dated %s", entry.name, entry.updated_at.isoformat()
        )

        if if_newer:
            previous = _last_run(session, entry.type)
            if previous is not None and previous.source_updated_at >= entry.updated_at:
                report.up_to_date = True
                report.finished_at = utcnow()
                return report

        path = bulk.download(entry, settings, client, force=force_download)

        run: ImportRun | None = None
        if not dry_run:
            run = ImportRun(
                bulk_type=entry.type,
                source_updated_at=entry.updated_at,
                cards_seen=0,
                cards_written=0,
                cards_skipped=0,
                started_at=report.started_at,
            )
            session.add(run)
            session.commit()

        imported_at = utcnow()
        rows = _card_rows(
            bulk.stream_cards(path), report, limit=limit, imported_at=imported_at
        )

        if dry_run:
            # Drain the generator so the counts are real, but write nothing.
            seen_ids = {row.oracle_id for row in rows}
            report.cards_written = 0
            logger.info("Dry run: would have written %d cards", len(seen_ids))
        else:
            report.cards_written = sink.upsert_batches(
                session, rows, batch_size=settings.import_batch_size
            )

        report.finished_at = utcnow()

        if run is not None:
            run.cards_seen = report.cards_seen
            run.cards_written = report.cards_written
            run.cards_skipped = report.cards_skipped
            run.finished_at = report.finished_at
            session.commit()

        return report
    finally:
        if owns_client:
            client.close()
        if owns_session:
            session.close()
