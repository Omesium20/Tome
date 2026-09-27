"""Uses a model to generate CardMetadata (roles, themes, synergy tags, ...) for each Card.

See docs/architecture.md#knowledge-pipeline (step 2), docs/data-model.md#cardmetadata,
and docs/knowledge-pipeline.md#metadata-generation for how this stage fits the
rest of the pipeline.

**Single-tier today.** `docs/benchmarking-and-testing.md` specifies a 300-card
benchmark that decides *whether* a local model tier is viable and, if so,
where the local/frontier escalation line falls — and that benchmark hasn't
run. Until it does, every card routes through one backend: the frontier
Anthropic model, via `AnthropicMetadataBackend` (`model_backend.py`).
`MetadataModelBackend` is the seam a local-model tier plugs into once the
benchmark picks an escalation rule — nothing else in this module should need
to change to add one.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import KnowledgeSettings, get_knowledge_settings
from database.knowledge.models import Card, CardMetadata
from database.knowledge.session import new_session

from . import sink
from .anchors import ANCHORS
from .model_backend import AnthropicMetadataBackend, MetadataGenerationError, MetadataModelBackend
from .prompt import build_system_prompt, render_card_facts
from .schema import utcnow

logger = logging.getLogger(__name__)


@dataclass
class MetadataReport:
    """What a run actually did."""

    cards_seen: int = 0
    cards_generated: int = 0
    cards_written: int = 0
    cards_failed: int = 0
    dry_run: bool = False
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()

    def summary(self) -> str:
        prefix = "Dry run" if self.dry_run else "Run"
        parts = [
            f"{prefix} complete: {self.cards_seen:,} candidates",
            f"{self.cards_generated:,} generated",
            f"{self.cards_written:,} written",
        ]
        if self.cards_failed:
            parts.append(f"{self.cards_failed:,} failed")
        parts.append(f"{self.elapsed_seconds:.1f}s")
        return ", ".join(parts)


def _is_legal(card: Card, format_name: str) -> bool:
    return card.legalities.get(format_name) in ("legal", "restricted")


def _fetch_page(
    session: Session,
    *,
    after_oracle_id: str | None,
    page_size: int,
) -> list[tuple[Card, CardMetadata | None]]:
    statement = (
        select(Card, CardMetadata)
        .outerjoin(CardMetadata, CardMetadata.card_id == Card.oracle_id)
        .order_by(Card.oracle_id)
        .limit(page_size)
    )
    if after_oracle_id is not None:
        statement = statement.where(Card.oracle_id > after_oracle_id)
    return list(session.execute(statement))


def _cards_needing_metadata(
    session: Session,
    *,
    format_name: str | None,
    page_size: int,
):
    """Cards with no metadata yet, or metadata older than the `Card` it describes.

    A left outer join rather than a `NOT IN` subquery so a stale row (one
    whose `updated_at` predates `Card.updated_at` — e.g. after an oracle text
    errata) is picked up for regeneration too, not just a missing one.

    Paged with discrete ``oracle_id > cursor LIMIT page_size`` queries rather
    than one long-lived streaming cursor (``yield_per``) on principle: the
    caller commits writes on this same session between pages, and on Postgres
    a commit closes the server-side cursor `yield_per` opens out from under
    it. Small repeated queries against an indexed primary key have no such
    hazard, and page_size is already bounded by the write-batch size, so
    there's no memory-use downside to not streaming.
    """
    after_oracle_id: str | None = None
    while True:
        page = _fetch_page(session, after_oracle_id=after_oracle_id, page_size=page_size)
        if not page:
            return

        for card, metadata in page:
            if metadata is None or metadata.updated_at < card.updated_at:
                if format_name is None or _is_legal(card, format_name):
                    yield card

        after_oracle_id = page[-1][0].oracle_id


def generate_metadata(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    format_name: str | None = "commander",
    settings: KnowledgeSettings | None = None,
    backend: MetadataModelBackend | None = None,
    session: Session | None = None,
) -> MetadataReport:
    """Generate and write `CardMetadata` for every card that needs it.

    Args:
        dry_run: Call the model and validate its output, but write nothing.
            Still spends real API calls — use `limit` alongside it to bound cost.
        limit: Stop after this many candidate cards. For development and
            smoke-testing prompts before committing to a full run.
        format_name: Only generate for cards legal in this format, or `None`
            for every card in the corpus regardless of legality. Defaults to
            `"commander"` — Tome's only supported format (`PRD.md`) — since a
            card legal nowhere Tome plays is a model call spent on nothing
            (`docs/knowledge-pipeline.md#pool-sizes`).

    The ``settings``/``backend``/``session`` parameters exist so tests can
    inject fakes; production callers pass none of them.
    """
    settings = settings or get_knowledge_settings()
    report = MetadataReport(dry_run=dry_run)

    if not ANCHORS:
        logger.warning(
            "No anchor cards configured in knowledge_pipeline/metadata_generator/anchors.py — "
            "power_rating and role/theme boundaries are uncalibrated. Fine "
            "for a smoke test; see docs/data-model.md#anchor-cards before a "
            "full-corpus run."
        )

    owns_session = session is None
    session = session or new_session()
    backend = backend or AnthropicMetadataBackend(
        api_key=settings.metadata_model_api_key,
        model=settings.metadata_model_name,
        max_tokens=settings.metadata_model_max_tokens,
    )
    system_prompt = build_system_prompt()

    try:
        pending_rows: list[dict] = []

        for card in _cards_needing_metadata(
            session, format_name=format_name, page_size=settings.metadata_batch_size
        ):
            report.cards_seen += 1

            try:
                blueprint = backend.generate(
                    system=system_prompt, prompt=render_card_facts(card)
                )
            except MetadataGenerationError:
                logger.exception(
                    "Metadata generation failed for %s (%s)", card.name, card.oracle_id
                )
                report.cards_failed += 1
            else:
                report.cards_generated += 1
                if not dry_run:
                    pending_rows.append(blueprint.to_card_metadata_row(card.oracle_id))
                    if len(pending_rows) >= settings.metadata_batch_size:
                        report.cards_written += sink.upsert_batches(
                            session, pending_rows
                        )
                        pending_rows = []

            if limit is not None and report.cards_seen >= limit:
                logger.info("Stopping early at --limit %d", limit)
                break

        if not dry_run:
            report.cards_written += sink.upsert_batches(session, pending_rows)

        report.finished_at = utcnow()
        return report
    finally:
        if owns_session:
            session.close()
