"""Command line entry point: ``python -m knowledge_pipeline.metadata_generator``.

Writes to the **knowledge** database (``KNOWLEDGE_DATABASE_URL``), which for
maintainers is the shared cloud corpus every client reads.
"""

import argparse
import logging
import sys

from logging_config import configure_logging

from .pipeline import generate_metadata

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.metadata_generator",
        description=(
            "Generate AI card_metadata for every Card lacking it, or whose "
            "metadata predates the Card row it describes. Writes to the "
            "knowledge database (KNOWLEDGE_DATABASE_URL)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Call the model and validate its output, but write nothing. "
            "Still spends real API calls -- pair with --limit."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Stop after N candidate cards. For development and smoke-testing.",
    )
    parser.add_argument(
        "--format",
        default="commander",
        metavar="FORMAT",
        help=(
            "Only generate for cards legal in this format, or 'all' for "
            "every card regardless of legality. Default: commander."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()

    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1.", file=sys.stderr)
        return 2

    format_name = None if args.format.lower() == "all" else args.format

    try:
        report = generate_metadata(
            dry_run=args.dry_run, limit=args.limit, format_name=format_name
        )
    except Exception:
        logger.exception("Metadata generation failed")
        return 1

    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
