"""Command line entry point: ``python -m knowledge_pipeline.scryfall_importer``.

Writes to the **knowledge** database (``KNOWLEDGE_DATABASE_URL``), which for
maintainers is the shared cloud corpus every client reads. Nothing here writes
to a user's own database, and nothing here can see one.

There is no format selection. The corpus holds every card, and format is a
filter applied downstream — at the AI stages, or on the client. See
``card_filter.py``.
"""

import argparse
import logging
import sys

from logging_config import configure_logging

from . import sink
from .pipeline import import_cards

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.scryfall_importer",
        description=(
            "Import Magic card data from Scryfall's bulk data API into the "
            "knowledge database. Safe to re-run: cards are upserted, never "
            "blindly replaced, and an import never deletes a row."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # RawDescriptionHelpFormatter prints this verbatim; argparse only
        # %-interpolates when the text contains %(prog), so a literal % is fine.
        epilog=(
            "Every card object in the bulk file is imported (~34,800), with its\n"
            "complete legality map. There is no --format: the corpus is shared and\n"
            "hosted, so it holds the whole pool and clients filter by format.\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download, filter and map everything, but write nothing to the database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Stop after N cards. For development.",
    )
    parser.add_argument(
        "--if-newer",
        action="store_true",
        help="Exit early when Scryfall's snapshot has already been imported.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the bulk file even if a matching cached copy exists.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Empty the cards table before importing. Destructive and "
            "unguarded — requires the target database's name typed back."
        ),
    )
    return parser


def _typed_confirmation(prompt: str, expected: str) -> bool:
    """Require the user to type ``expected`` exactly.

    Refuses when there's no terminal or stdin is at EOF: an unattended process
    must never be able to fall through a destructive confirmation. There is
    deliberately no flag to skip this. The guard it replaced could be
    overridden with ``--force``, and what that guard protected is no longer in
    this database to protect.

    Comparison is exact rather than case-folded — the expected value is a
    database identifier, and those are not always lowercase.
    """
    if not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip() == expected
    except EOFError:
        print()
        return False


def _run_reset() -> int | None:
    """Empty the card table. Returns rows removed, or None if it was refused.

    The confirmation asks for the *database name* rather than a fixed word,
    because the hazard changed. It used to be "you are about to delete cards
    that a collection points at" — data that is no longer here to check for.
    What replaced it is "you are about to wipe a corpus every client reads,
    and you may not be pointed where you think you are".
    """
    from database.knowledge.session import get_engine, new_session

    url = get_engine().url
    # An in-memory sqlite:// URL has no database name to ask for; fall back to
    # the whole URL so there is always something specific to type.
    expected = url.database or url.render_as_string(hide_password=True)

    with new_session() as session:
        count = sink.card_count(session)

        print(f"\nReset will delete {count:,} cards from:")
        print(f"  {url.render_as_string(hide_password=True)}")
        print(
            "Anything referencing them from a collection or a deck is in a "
            "different database and cannot be checked from here."
        )
        prompt = f"Type the database name ({expected}) to confirm: "
        if not _typed_confirmation(prompt, expected):
            print("Aborted; nothing was deleted.", file=sys.stderr)
            return None

        return sink.reset(session)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()

    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1.", file=sys.stderr)
        return 2

    if args.reset:
        if args.dry_run:
            print("--reset and --dry-run are contradictory.", file=sys.stderr)
            return 2

        removed = _run_reset()
        if removed is None:
            return 1
        print(f"Reset: removed {removed:,} cards.")

    try:
        report = import_cards(
            dry_run=args.dry_run,
            limit=args.limit,
            if_newer=args.if_newer,
            force_download=args.force_download,
        )
    except Exception:
        logger.exception("Import failed")
        return 1

    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
