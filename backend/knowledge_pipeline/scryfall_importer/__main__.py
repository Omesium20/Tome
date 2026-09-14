"""Command line entry point: ``python -m knowledge_pipeline.scryfall_importer``.

Only Commander is importable today (see ``formats.py``), so ``--format`` is
optional and the interactive picker stays out of the way: with one enabled
profile there is nothing to ask. The picker is still here, and comes back
automatically as soon as a second profile is enabled.
"""

import argparse
import logging
import sys

from logging_config import configure_logging

from . import sink
from .formats import DEFAULT_PROFILE, FormatError, enabled_profiles, resolve, selectable_names
from .pipeline import import_cards

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.scryfall_importer",
        description=(
            "Import Magic card data from Scryfall's bulk data API. "
            "Safe to re-run: cards are upserted, never blindly replaced."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # RawDescriptionHelpFormatter prints this verbatim; argparse only
        # %-interpolates when the text contains %(prog), so a literal % is fine.
        epilog=(
            "Tome is a Commander deck builder, so the import is Commander-only: the\n"
            "database holds the ~31,800 Commander-legal cards and nothing else. Other\n"
            "formats are scaffolded in formats.py but not selectable yet.\n"
        ),
    )
    parser.add_argument(
        "--format",
        dest="format_name",
        metavar="{" + ",".join(selectable_names()) + "}",
        help=f"Card pool to import (default: {DEFAULT_PROFILE}).",
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
        help="Stop after N matching cards. For development.",
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

    destructive = parser.add_argument_group(
        "destructive options",
        "Both leave anything referenced by a collection or deck alone unless forced.",
    )
    destructive.add_argument(
        "--prune",
        action="store_true",
        help="After importing, delete cards outside this import that nothing references.",
    )
    destructive.add_argument(
        "--reset",
        action="store_true",
        help="Empty the cards table before importing. Refuses if user data exists.",
    )
    destructive.add_argument(
        "--force",
        action="store_true",
        help="Allow --reset to proceed even though it will discard collections and decks.",
    )
    return parser


def _select_format() -> str:
    """Which pool to import when ``--format`` wasn't given.

    With a single enabled profile — today's situation — there is nothing worth
    asking, so this is silent and the import just runs.
    """
    if len(enabled_profiles()) < 2 or not sys.stdin.isatty():
        return DEFAULT_PROFILE
    return _choose_format_interactively() or DEFAULT_PROFILE


def _choose_format_interactively() -> str | None:
    """Prompt for a card pool, or return None if there's nobody to ask.

    ``isatty()`` alone isn't a reliable test — some CI runners and container
    shells report a terminal while stdin is already at EOF — so the read itself
    has to handle that rather than dying with an EOFError traceback.
    """
    profiles = list(enabled_profiles().values())

    print("\nWhich card pool do you want to import?\n")
    for index, profile in enumerate(profiles, start=1):
        approx = f"~{profile.approx_cards:,} cards" if profile.approx_cards else ""
        default_marker = "  (recommended)" if profile.name == DEFAULT_PROFILE else ""
        print(f"  {index:2}) {profile.label:<22} {approx:>16}{default_marker}")

    default_index = [profile.name for profile in profiles].index(DEFAULT_PROFILE) + 1
    print()

    while True:
        try:
            raw = input(f"Choice [{default_index}]: ").strip()
        except EOFError:
            print()
            return None
        if not raw:
            return DEFAULT_PROFILE
        if raw.isdigit() and 1 <= int(raw) <= len(profiles):
            return profiles[int(raw) - 1].name
        if raw.lower() in selectable_names():
            return raw.lower()
        print(f"Please enter a number between 1 and {len(profiles)}.")


def _typed_confirmation(prompt: str, expected: str) -> bool:
    """Require the user to type ``expected`` exactly.

    Refuses when there's no terminal or stdin is at EOF: an unattended process
    must never be able to fall through a destructive confirmation.
    """
    if not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip().lower() == expected
    except EOFError:
        print()
        return False


def _run_reset(*, force: bool) -> int | None:
    """Empty the card table. Returns rows removed, or None if it was refused."""
    from database.session import SessionLocal

    with SessionLocal() as session:
        counts = sink.user_data_counts(session)

        if force and any(counts.values()):
            detail = ", ".join(f"{n} {table}" for table, n in counts.items() if n)
            print(f"\nThis database holds user data: {detail}.")
            print("A forced reset deletes all of it. This cannot be undone.")
            if not _typed_confirmation("Type 'delete' to confirm: ", "delete"):
                print("Aborted; nothing was deleted.", file=sys.stderr)
                return None

        try:
            return sink.reset(session, force=force)
        except sink.ResetRefused as error:
            print(str(error), file=sys.stderr)
            return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()

    try:
        profile = resolve(args.format_name or _select_format())
    except FormatError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1.", file=sys.stderr)
        return 2

    if args.force and not args.reset:
        # Silently ignoring it would let someone believe they'd authorized
        # something destructive that never ran.
        print("--force only applies to --reset; ignoring it.", file=sys.stderr)

    if args.reset:
        if args.dry_run:
            print("--reset and --dry-run are contradictory.", file=sys.stderr)
            return 2

        removed = _run_reset(force=args.force)
        if removed is None:
            return 1
        print(f"Reset: removed {removed:,} cards.")

    try:
        report = import_cards(
            format_name=profile.name,
            dry_run=args.dry_run,
            limit=args.limit,
            prune=args.prune,
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
