"""Command line entry point: ``python -m knowledge_pipeline.benchmark``.

Touches **neither production database**. Everything here reads and writes one
standalone SQLite file (`store.DEFAULT_DB_PATH`, overridable with `--db`) —
see `store.py` for why the benchmark's run log is deliberately not part of
the knowledge or local plane.

Five subcommands, in the order a maintainer meets them:

``init``
    Create the store and say where it is and what's in it.
``seed-demo``
    Fill it with obviously-synthetic rows so the reporting layer can be built
    before the benchmark is run.
``report``
    Aggregate the log — per bucket, tiers side by side — as text, and
    optionally as a self-contained HTML page.
``review``
    Walk the unreviewed runs interactively and record a verdict for each.
    This is the benchmark's actual scoring step — human review, not an
    automated metric (`docs/benchmarking-and-testing.md#scoring`).
``set-verdict``
    The non-interactive path into the same column, for a verdict decided
    outside the review loop or corrected after the fact.

``report`` is the one subcommand that doesn't run against the connection
opened below: it is read-only, `report.main` resolves and opens the store
itself, and `python -m knowledge_pipeline.benchmark.report` remains a
first-class entry point. This subparser is a convenience alias over the same
`main`, so the two can never drift in behaviour.
"""

import argparse
import logging
import sys
from pathlib import Path

from logging_config import configure_logging

from . import store
from .store import BUCKETS, HUMAN_VERDICTS, MODEL_TIERS

logger = logging.getLogger(__name__)

# Prompts and responses run to thousands of characters. Review needs enough to
# recognize a card and read its answer, not the cached taxonomy block printed
# in full ~900 times.
_PROMPT_PREVIEW_CHARS = 400
_RESPONSE_PREVIEW_CHARS = 2000


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.benchmark",
        description=(
            "Inspect and review the metadata-generation benchmark's run log. "
            "Reads and writes a standalone SQLite file; never touches "
            "KNOWLEDGE_DATABASE_URL or LOCAL_DATABASE_URL."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The store records the benchmark specified in\n"
            "docs/benchmarking-and-testing.md. That benchmark has not been run\n"
            "yet -- until it is, `seed-demo` is the only thing that puts rows in.\n"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        metavar="PATH",
        help=(
            "Use this SQLite file instead of the default "
            f"({store.DEFAULT_DB_PATH.name}, beside this package)."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "init",
        help="Create the store if it doesn't exist; print its path and row count.",
    )

    seed = subparsers.add_parser(
        "seed-demo",
        help="Populate deterministic synthetic rows for building reports against.",
        description=(
            "Insert obviously-fake runs -- 'Demo Card NN', model names prefixed "
            "'demo/' -- across all three buckets and tiers. Refuses if the "
            "store already holds real benchmark runs."
        ),
    )
    seed.add_argument(
        "--cards",
        type=int,
        default=12,
        metavar="N",
        help="How many synthetic cards to generate runs for (3 rows each). Default: 12.",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Summarize the log: per bucket, tiers side by side.",
        description=(
            "Alias for `python -m knowledge_pipeline.benchmark.report`. Prints "
            "the text summary and, with --html, writes a self-contained page "
            "that opens offline from a file:// path. Read-only."
        ),
    )
    report_parser.add_argument(
        "--html", type=Path, metavar="OUT.html", help="Also write an HTML report here."
    )
    report_parser.add_argument(
        "--bucket", choices=BUCKETS, help="Only runs in this difficulty bucket."
    )
    report_parser.add_argument(
        "--tier", choices=MODEL_TIERS, help="Only runs from this model tier."
    )
    report_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the text summary. Only useful alongside --html.",
    )

    review = subparsers.add_parser(
        "review",
        help="Walk unreviewed runs one at a time and record a verdict for each.",
        description=(
            "Prints each unreviewed run -- card, tier, truncated prompts, full "
            "response -- and asks for a verdict. Requires a terminal."
        ),
    )
    review.add_argument(
        "--bucket",
        choices=BUCKETS,
        help="Only review runs from this difficulty bucket.",
    )
    review.add_argument(
        "--tier",
        choices=MODEL_TIERS,
        help="Only review runs from this model tier.",
    )

    verdict = subparsers.add_parser(
        "set-verdict",
        help="Record one verdict non-interactively.",
    )
    verdict.add_argument("--id", type=int, required=True, metavar="N", help="The run id.")
    verdict.add_argument(
        "--verdict", choices=HUMAN_VERDICTS, required=True, help="The maintainer's read."
    )
    verdict.add_argument(
        "--notes",
        metavar="TEXT",
        help="Free text: what the automated metrics didn't catch.",
    )

    return parser


def _truncate(text: str, limit: int) -> str:
    """Clip `text` to `limit` characters, saying how much was hidden.

    Silently truncating would let a reviewer judge a response they could only
    see part of without knowing it — the exact failure this whole review step
    exists to avoid.
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n  ... [{len(text) - limit:,} more characters]"


def _print_run(row, position: int, total: int) -> None:
    """Show one run to a human deciding whether the model got it right."""
    # ASCII box-drawing only: this prints to whatever console the maintainer
    # has, and a Windows one at cp1252 mangles anything else.
    print("\n" + "=" * 72)
    print(f"[{position}/{total}]  run #{row['id']}  |  {row['card_name']}")
    print(
        f"  bucket={row['bucket']}  tier={row['model_tier']}  model={row['model_name']}"
    )
    print(
        f"  schema_valid={bool(row['schema_valid'])}  "
        f"tokens={row['input_tokens']}/{row['output_tokens']}  "
        f"latency={row['latency_ms']:,}ms  cost=${row['cost_usd']:.4f}"
    )
    print(f"  oracle_id={row['card_oracle_id']}  run_at={row['run_at']}")
    print("-" * 72)
    print("SYSTEM PROMPT:")
    print("  " + _truncate(row["system_prompt"], _PROMPT_PREVIEW_CHARS))
    print("\nUSER PROMPT:")
    print("  " + _truncate(row["user_prompt"], _PROMPT_PREVIEW_CHARS))
    print("\nRAW RESPONSE:")
    print("  " + _truncate(row["raw_response"], _RESPONSE_PREVIEW_CHARS))
    print("-" * 72)


def _cmd_init(connection, db_path: Path) -> int:
    print(f"Benchmark store: {db_path}")
    print(f"Runs recorded:   {store.run_count(connection):,}")
    return 0


def _cmd_seed_demo(connection, cards: int) -> int:
    if cards < 1:
        print("--cards must be at least 1.", file=sys.stderr)
        return 2

    try:
        inserted = store.seed_demo(connection, cards=cards)
    except store.SeedRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Seeded {inserted:,} demo runs across {cards:,} cards.")
    print("Every row is synthetic: card names 'Demo Card NN', models prefixed 'demo/'.")
    return 0


def _cmd_review(connection, *, bucket, tier) -> int:
    """Interactively record a verdict for each unreviewed run.

    Refuses without a terminal for the same reason the importer's `--reset`
    does (`scryfall_importer/__main__.py`): a prompt read at EOF is a prompt
    nobody answered. Here the consequence is the opposite of destructive —
    the loop would spin through every run recording nothing — but it is just
    as silent, so it gets the same guard and says what to run instead.
    """
    if not sys.stdin.isatty():
        print(
            "`review` needs a terminal: it asks for a verdict on each run and "
            "there is nobody here to ask.\n"
            "Use `set-verdict --id N --verdict good|acceptable|wrong` for the "
            "non-interactive path.",
            file=sys.stderr,
        )
        return 1

    rows = store.iter_runs(connection, bucket=bucket, model_tier=tier, reviewed=False)
    if not rows:
        print("Nothing to review: every matching run already has a verdict.")
        return 0

    print(f"{len(rows):,} unreviewed run(s). Enter to skip, 'q' to stop.")
    options = "/".join(HUMAN_VERDICTS)
    reviewed = 0

    for position, row in enumerate(rows, start=1):
        _print_run(row, position, len(rows))

        try:
            answer = input(f"Verdict [{options}] (Enter=skip, q=quit): ").strip().lower()
        except EOFError:
            # stdin closed mid-loop. Everything already answered is committed
            # row by row, so stopping here loses nothing.
            print()
            break

        if answer == "q":
            break
        if not answer:
            continue
        if answer not in HUMAN_VERDICTS:
            print(f"  Not a verdict: {answer!r}. Skipped.", file=sys.stderr)
            continue

        try:
            notes = input("Notes (optional): ").strip()
        except EOFError:
            print()
            notes = ""

        store.set_verdict(connection, int(row["id"]), answer, notes or None)
        reviewed += 1
        print(f"  Recorded: run #{row['id']} -> {answer}")

    remaining = len(rows) - reviewed
    print(f"\nReviewed {reviewed:,} run(s); {remaining:,} still unreviewed.")
    return 0


def _cmd_report(
    *,
    db_path: Path,
    html: Path | None,
    bucket: str | None,
    tier: str | None,
    quiet: bool,
) -> int:
    """Delegate to `report.main` rather than reimplement its rendering.

    Imported inside the function, not at module scope, so `init`, `review`
    and `set-verdict` don't pay for loading ~2,000 lines of aggregation and
    HTML templating they never touch. Arguments are rebuilt as an argv list
    because `report.main` owns its own parser — routing through it means the
    alias and the direct entry point cannot diverge in defaults or in how
    they handle a missing database.
    """
    from . import report

    argv = ["--db", str(db_path)]
    if html is not None:
        argv += ["--html", str(html)]
    if bucket is not None:
        argv += ["--bucket", bucket]
    if tier is not None:
        argv += ["--tier", tier]
    if quiet:
        argv.append("--quiet")

    return report.main(argv)


def _cmd_set_verdict(connection, *, run_id: int, verdict: str, notes: str | None) -> int:
    try:
        store.set_verdict(connection, run_id, verdict, notes)
    except store.UnknownRunError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Run #{run_id} -> {verdict}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()

    db_path = args.db or store.DEFAULT_DB_PATH

    # Handled before the connection is opened: `report` needs no writable
    # handle, and `store.connect` would create an empty store as a side effect
    # of asking for a summary of one that doesn't exist.
    if args.command == "report":
        return _cmd_report(
            db_path=db_path,
            html=args.html,
            bucket=args.bucket,
            tier=args.tier,
            quiet=args.quiet,
        )

    try:
        connection = store.connect(db_path)
    except Exception:
        logger.exception("Could not open the benchmark store at %s", db_path)
        return 1

    try:
        if args.command == "init":
            return _cmd_init(connection, db_path)
        if args.command == "seed-demo":
            return _cmd_seed_demo(connection, args.cards)
        if args.command == "review":
            return _cmd_review(connection, bucket=args.bucket, tier=args.tier)
        if args.command == "set-verdict":
            return _cmd_set_verdict(
                connection, run_id=args.id, verdict=args.verdict, notes=args.notes
            )
    except Exception:
        logger.exception("Command %r failed", args.command)
        return 1
    finally:
        connection.close()

    # argparse's required=True makes this unreachable; kept so the function
    # has one obvious exit type rather than an implicit None.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
