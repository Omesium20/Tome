"""Command line entry point: ``python -m knowledge_pipeline.benchmark``.

Touches **neither production database**. Everything here reads and writes one
standalone SQLite file (`store.DEFAULT_DB_PATH`, overridable with `--db`) —
see `store.py` for why the benchmark's run log is deliberately not part of
the knowledge or local plane.

Nine subcommands, in the order a maintainer meets them:

``init``
    Create the store and say where it is and what's in it.
``seed-demo``
    Fill it with obviously-synthetic sessions so the reporting layer can be
    built before the benchmark is run.
``sample``
    Build (or check) the 300-card sample sessions draw from. Reads the
    knowledge corpus; writes a JSON file, not the store. Tracked in git, unlike
    the store -- `sample.DEFAULT_SAMPLE_PATH` is the default for `--out`,
    `--validate`, and the sample `run` reads.
``run``
    Draw a card set from one or more difficulty buckets and put it through
    every tier in serial succession, as one **session**. This is the benchmark
    proper -- the only subcommand that spends time on inference, and the only
    one that puts measured rows in the store.
``sessions``
    List the sessions: what each measured, whether it is paired, how it landed.
``report``
    Aggregate -- per session as the primary view, pooled across sessions as
    context -- as text, and optionally as a self-contained HTML page.
``review``
    Walk the unreviewed runs interactively and record a verdict for each.
``set-verdict``
    The non-interactive path into the same column, for a verdict decided
    outside the review loop or corrected after the fact.
``pair-verdict``
    Record which tier won a card. Takes the JSON the HTML report's "Collect
    picks" button produces (`--from`), or one card at a time.

``report`` is the one subcommand that doesn't run against the connection
opened below: it is read-only, `report.main` resolves and opens the store
itself, and `python -m knowledge_pipeline.benchmark.report` remains a
first-class entry point. This subparser is a convenience alias over the same
`main`, so the two can never drift in behaviour.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from logging_config import configure_logging

from . import store
from .store import (
    BUCKETS,
    HUMAN_VERDICTS,
    MODEL_TIERS,
    NO_WINNER,
    PAIR_OUTCOMES,
)

logger = logging.getLogger(__name__)

# Prompts and responses run to thousands of characters. Review needs enough to
# recognize a card and read its answer, not the cached taxonomy block printed
# in full for every row.
_PROMPT_PREVIEW_CHARS = 400
_RESPONSE_PREVIEW_CHARS = 2000

# Which tiers `run` will accept, derived from the store's closed vocabulary
# rather than imported from `runner` -- building the parser must not pull in
# sqlalchemy, ollama and the Anthropic SDK for the sake of `init` or `review`.
# `runner.build_local_backends` re-checks this, so a tier that slipped through
# still fails before any call is made.
_LOCAL_TIER_CHOICES: tuple[str, ...] = tuple(
    tier for tier in MODEL_TIERS if tier.startswith("local_")
)

# Mirrors `sample.DEFAULT_SAMPLE_PATH` -- computed directly rather than
# imported, for the same reason `_LOCAL_TIER_CHOICES` reads `store.MODEL_TIERS`
# instead of `runner`'s: `_build_parser` runs on every invocation, and must
# not pull in sqlalchemy for the sake of `init` or `review`. The two constants
# are asserted equal in `test_benchmark_runner.py`.
DEFAULT_SAMPLE_PATH: Path = Path(__file__).resolve().parent / "sample.json"

# Duplicated from `runner` for the same import-weight reason. Asserted equal
# in the runner tests.
DEFAULT_CARDS_PER_BUCKET = 30
DEFAULT_DRAW_SEED = 20261012


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.benchmark",
        description=(
            "Run, inspect and review the metadata-generation benchmark. The run "
            "log is a standalone SQLite file; only `sample` and `run` read "
            "KNOWLEDGE_DATABASE_URL, and nothing here ever writes to it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Runs the benchmark specified in docs/benchmarking-and-testing.md.\n"
            "Usual order: `sample --out`, then `run --bucket B`, then `report`,\n"
            "then pick winners on the HTML page and `pair-verdict --from`.\n"
            "`run` wires the two local tiers only -- the frontier tier is not\n"
            "wired, so the hard-bucket gap against it stays uncomputable, and\n"
            "accuracy stays unavailable until ground truth is hand-labeled.\n"
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
        help="Create the store if it doesn't exist; print its path and counts.",
    )

    seed = subparsers.add_parser(
        "seed-demo",
        help="Populate deterministic synthetic sessions to build reports against.",
        description=(
            "Insert obviously-fake sessions -- 'Demo Card NN', model names "
            "prefixed 'demo/', session ids prefixed 'S-D' -- one per bucket, "
            "each internally paired across all three tiers. Refuses if the "
            "store already holds real benchmark runs."
        ),
    )
    seed.add_argument(
        "--cards",
        type=int,
        default=12,
        metavar="N",
        help="How many synthetic cards in total, split across the three bucket "
        "sessions (3 rows each). Default: 12.",
    )

    sample_parser = subparsers.add_parser(
        "sample",
        help="Build or check the 300-card benchmark sample.",
        description=(
            "Selects 300 Commander-legal cards, stratified into easy/medium/hard "
            "by a difficulty heuristic, and writes them as JSON. Reads the "
            "knowledge corpus; never writes to it. Buckets are UNREVIEWED: the "
            "file records that, because the hard-bucket gap between tiers is the "
            "number this benchmark exists to produce."
        ),
    )
    sample_parser.add_argument(
        "--out",
        type=Path,
        nargs="?",
        const=DEFAULT_SAMPLE_PATH,
        default=None,
        metavar="SAMPLE.json",
        help=(
            "Write a freshly selected sample here. Bare --out (no path) writes "
            f"to {DEFAULT_SAMPLE_PATH.name} beside this package, the tracked "
            "default `run` also reads from."
        ),
    )
    sample_parser.add_argument(
        "--validate",
        type=Path,
        nargs="?",
        const=DEFAULT_SAMPLE_PATH,
        default=None,
        metavar="SAMPLE.json",
        help=(
            "Check an existing sample: structure, then every card against the "
            f"corpus. Bare --validate checks {DEFAULT_SAMPLE_PATH.name}."
        ),
    )
    sample_parser.add_argument(
        "--seed",
        type=int,
        default=20261001,
        metavar="N",
        help="Selection seed, so a sample is reproducible. Default: 20261001.",
    )
    sample_parser.add_argument(
        "--offline",
        action="store_true",
        help="With --validate, skip the corpus checks (structure only).",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run one paired session: the same cards through every tier.",
        description=(
            "The benchmark itself. Draws --cards cards from each --bucket, then "
            "runs every tier over that one frozen card set -- one tier at a "
            "time, never two at once, since the 7B and 14B models do not share "
            "a 12GB card. Writes one session plus one row per (card, tier). "
            "Resumable with --resume. Only local tiers are wired; see runner.py."
        ),
    )
    run_parser.add_argument(
        "sample",
        type=Path,
        nargs="?",
        default=DEFAULT_SAMPLE_PATH,
        metavar="SAMPLE.json",
        help=(
            "The sample to draw from. Defaults to "
            f"{DEFAULT_SAMPLE_PATH.name} beside this package."
        ),
    )
    run_parser.add_argument(
        "--bucket",
        action="append",
        choices=list(BUCKETS),
        metavar="BUCKET",
        help=(
            "Difficulty bucket to draw from; repeatable, and REQUIRED -- there "
            "is no default. The bucket is what a session measures, and a run "
            "that did not choose one measured whichever cards happened to come "
            "first in the sample file (which is how every row of the first "
            "store ended up easy). Choices: " + ", ".join(BUCKETS) + ". The "
            "full 300 is --cards 100 with all three."
        ),
    )
    run_parser.add_argument(
        "--cards",
        type=int,
        default=DEFAULT_CARDS_PER_BUCKET,
        metavar="N",
        help=(
            f"Cards per bucket. Default: {DEFAULT_CARDS_PER_BUCKET}, which is "
            "about half an hour of 14B inference per tier. A win rate over 30 "
            "cards carries ~+/-18 points of uncertainty, so raise this before "
            "concluding anything from a near-even split."
        ),
    )
    run_parser.add_argument(
        "--draw-seed",
        type=int,
        default=DEFAULT_DRAW_SEED,
        metavar="N",
        help=(
            f"Which cards get drawn. Default: {DEFAULT_DRAW_SEED}, so repeating "
            "a command compares the same cards -- what you want after a prompt "
            "change. Pass a different seed for a fresh draw, which is what you "
            "want before trusting a result."
        ),
    )
    run_parser.add_argument(
        "--label",
        metavar="TEXT",
        help="Short name for this session, shown on its tab and in `sessions`.",
    )
    run_parser.add_argument(
        "--resume",
        metavar="S-NNN",
        help=(
            "Finish an existing session instead of starting one: same cards, "
            "same prompt, only the (card, tier) cells that have no row. Refuses "
            "if the system prompt has changed since that session started."
        ),
    )
    run_parser.add_argument(
        "--tier",
        action="append",
        choices=list(_LOCAL_TIER_CHOICES),
        metavar="TIER",
        help=(
            "Run only this tier; repeatable. Default: every local tier, in "
            "ascending size order."
        ),
    )
    run_parser.add_argument(
        "--tier-order",
        choices=("ascending", "descending"),
        default="ascending",
        help=(
            "Which tier goes first. The second tier always runs on a machine "
            "the first one just warmed, so flipping this on a second session is "
            "how that confound gets tested rather than assumed away. Default: "
            "ascending (smallest model first)."
        ),
    )
    run_parser.add_argument(
        "--host", default=None, metavar="URL", help="Ollama server. Default: localhost:11434."
    )
    run_parser.add_argument(
        "--model-low", default=None, metavar="TAG", help="Ollama tag for the local_low tier."
    )
    run_parser.add_argument(
        "--model-high", default=None, metavar="TAG", help="Ollama tag for the local_high tier."
    )
    run_parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Context window per call. The default is sized to fit the ~10,100-token "
            "system prompt; Ollama's own default of 4096 would silently truncate "
            "the calibration ladders out of it."
        ),
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-call timeout. Default: 300.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the resolved config, the cards that would be drawn and the "
            "prompts that would be sent, and make no model calls at all -- and "
            "open no session. (Unlike `metadata_generator --dry-run`, which "
            "still spends real API calls; local inference has no billing to "
            "warn about.)"
        ),
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        help="List sessions: what each measured, and whether it is paired.",
        description=(
            "One line per session. `paired` is the column that matters: a "
            "session missing any (card, tier) cell is not a like-for-like "
            "comparison, and no win rate over it should be quoted."
        ),
    )
    sessions_parser.add_argument(
        "--status",
        choices=list(store.SESSION_STATUSES),
        help="Only sessions in this state.",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Summarize: per session first, pooled across sessions after.",
        description=(
            "Alias for `python -m knowledge_pipeline.benchmark.report`. Prints "
            "the text summary and, with --html, writes a self-contained page "
            "whose tabs are the sessions. Read-only."
        ),
    )
    report_parser.add_argument(
        "--html", type=Path, metavar="OUT.html", help="Also write an HTML report here."
    )
    report_parser.add_argument(
        "--session", metavar="S-NNN", help="Only this session."
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
            "response -- and asks for a verdict. Requires a terminal.\n"
            "This is the per-run judgment ('was this answer any good?'). The "
            "per-card comparison ('which tier was better?') is `pair-verdict`, "
            "picked on the HTML report's session tab."
        ),
    )
    review.add_argument(
        "--session",
        metavar="S-NNN",
        help="Only review runs from this session.",
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
        help="Record one per-run verdict non-interactively.",
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

    pair = subparsers.add_parser(
        "pair-verdict",
        help="Record which tier won a card (the paired comparison).",
        description=(
            "The scoring step the benchmark's conclusion rests on, since "
            "without ground truth a relative judgment is the sound one. Give "
            "it the JSON the HTML report's 'Collect picks' button produces, or "
            "one card at a time. Re-deciding a card replaces the earlier pick."
        ),
    )
    pair.add_argument(
        "--from",
        dest="from_file",
        type=Path,
        metavar="PICKS.json",
        help=(
            "A file of picks: {\"session_id\": \"S-003\", \"picks\": [{...}]}. "
            "This is what the report page's button puts on your clipboard."
        ),
    )
    pair.add_argument("--session", metavar="S-NNN", help="Session id, for one pick.")
    pair.add_argument("--card", metavar="ORACLE_ID", help="Card oracle_id, for one pick.")
    pair.add_argument(
        "--winner",
        choices=list(PAIR_OUTCOMES),
        help=(
            "Which tier won, or why neither did. 'tie' and 'both_bad' are "
            "distinguished on purpose: equally good and equally useless drive "
            "opposite decisions about local generation."
        ),
    )
    pair.add_argument("--notes", metavar="TEXT", help="Why. Free text.")

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
        f"  session={row['session_id']}  bucket={row['bucket']}  "
        f"tier={row['model_tier']}  model={row['model_name']}"
    )
    print(
        f"  schema_valid={bool(row['schema_valid'])}  "
        f"tokens={row['input_tokens']}/{row['output_tokens']}  "
        f"latency={row['latency_ms']:,}ms  cost=${row['cost_usd']:.4f}"
    )
    print(f"  oracle_id={row['card_oracle_id']}  run_at={row['run_at']}")
    print("-" * 72)
    print("SYSTEM PROMPT:")
    print(f"  pass {row['pass_name']} of {len(store.PASSES)}")
    print("  " + _truncate(row["system_prompt"], _PROMPT_PREVIEW_CHARS))
    print("\nUSER PROMPT:")
    print("  " + _truncate(row["user_prompt"], _PROMPT_PREVIEW_CHARS))
    print("\nRAW RESPONSE:")
    print("  " + _truncate(row["raw_response"], _RESPONSE_PREVIEW_CHARS))
    print("-" * 72)


def _cmd_init(connection, db_path: Path) -> int:
    print(f"Benchmark store: {db_path}")
    print(f"Schema version:  {store.SCHEMA_VERSION}")
    print(f"Sessions:        {store.session_count(connection):,}")
    print(f"Runs recorded:   {store.run_count(connection):,}")
    print(f"Pair verdicts:   {store.pair_count(connection):,}")
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

    print(f"Seeded {inserted:,} demo runs across {len(BUCKETS)} demo session(s).")
    print(
        "Every row is synthetic: card names 'Demo Card NN', models prefixed "
        "'demo/', sessions prefixed 'S-D'."
    )
    return 0


def _cmd_sessions(connection, *, db_path: Path, status: str | None) -> int:
    """One line per session, with the pairing column that gates conclusions."""
    sessions = store.iter_sessions(connection, status=status)
    if not sessions:
        print(
            "No sessions recorded. `run --bucket hard --cards 30` creates one."
            if status is None
            else f"No sessions with status {status!r}."
        )
        return 0

    print(f"{len(sessions):,} session(s) in {db_path}:")
    print(
        f"  {'id':<8} {'status':<9} {'paired':<7} {'buckets':<18} "
        f"{'cards':>6} {'runs':>6} {'picks':>6}  tiers / label"
    )
    # Newest last, matching the store's ordering: the id column already sorts
    # and a reader scanning for "the one I just ran" looks at the bottom line
    # of a terminal, not the top.
    for row in sessions:
        session_id = str(row["id"])
        card_ids = store.session_card_ids(row)
        tiers = store.session_tiers(row)
        missing = store.missing_cells(
            connection, session_id, card_ids=card_ids, tiers=tiers
        )
        runs = len(card_ids) * len(tiers) * len(store.PASSES) - len(missing)
        paired = "yes" if not missing and len(tiers) > 1 else f"NO({len(missing)})"
        label = " then ".join(tiers) + (f"  {row['label']}" if row["label"] else "")
        print(
            f"  {session_id:<8} {str(row['status']):<9} {paired:<7} "
            f"{'+'.join(store.session_buckets(row)):<18} "
            f"{len(card_ids):>6} {runs:>6} "
            f"{store.pair_count(connection, session_id):>6}  {label}"
        )
    print(
        "\n`paired` is the column that gates a conclusion: a session missing "
        "any\n(card, tier) cell compared different card sets. Finish one with "
        "`run --resume ID`."
    )
    return 0


def _cmd_review(connection, *, session_id, bucket, tier) -> int:
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

    rows = store.iter_runs(
        connection,
        session_id=session_id,
        bucket=bucket,
        model_tier=tier,
        reviewed=False,
    )
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


def _cmd_sample(
    *,
    out: Path | None,
    validate: Path | None,
    seed: int,
    offline: bool,
) -> int:
    """Build or check the sample. Reads the corpus; never writes to it.

    Imported inside the function for the same reason `report` is: selecting a
    sample needs sqlalchemy and a configured KNOWLEDGE_DATABASE_URL, and
    `init`/`review`/`set-verdict` must not require either.
    """
    from . import sample as sample_mod

    if out is None and validate is None:
        print(
            "Nothing to do: pass --out SAMPLE.json to build one, or "
            "--validate SAMPLE.json to check one.",
            file=sys.stderr,
        )
        return 2

    if out is not None:
        built = sample_mod.select_sample(seed=seed)
        problems = sample_mod.validate_sample(built)
        if problems:
            # Selection built something its own rules reject -- a bug here, not
            # a bad input. Say so rather than writing the file.
            print("Refusing to write: the selected sample fails validation.", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  {problem}", file=sys.stderr)
            return 1

        sample_mod.write_sample(built, out)
        print(sample_mod.describe(built))
        print()
        print(f"Wrote {out}")
        for bucket in BUCKETS:
            print()
            print(f"{bucket} spread:")
            for key in ("color_bucket", "mana_value_band", "base_type"):
                spread = sample_mod.spread(built, bucket, key)
                print(f"  {key}: {spread}")

    if validate is not None:
        loaded = sample_mod.load_sample(validate)
        print(sample_mod.describe(loaded))

        problems = sample_mod.validate_sample(loaded)
        if not offline:
            problems += sample_mod.validate_against_corpus(loaded)

        if problems:
            print(f"{len(problems):,} problem(s):", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1

        checked = "structure" if offline else "structure and corpus"
        print(f"OK: {checked} checks pass for {len(loaded.cards):,} cards.")

    return 0


def _cmd_run(connection, *, db_path: Path, args) -> int:
    """The benchmark proper. The only subcommand that calls a model."""
    from . import runner
    from . import sample as sample_mod

    if not args.resume and not args.bucket:
        print(
            "run needs at least one --bucket (easy, medium, hard), repeatable.\n"
            "There is no default on purpose: the bucket is what the session "
            "measures, and the one flag that used to stand in for it silently "
            "made every recorded row easy. The full 300 is\n"
            "  run --cards 100 --bucket easy --bucket medium --bucket hard",
            file=sys.stderr,
        )
        return 2

    if args.resume and (args.bucket or args.cards != DEFAULT_CARDS_PER_BUCKET):
        print(
            f"--resume takes its buckets and card count from session "
            f"{args.resume}; --bucket/--cards would describe a different "
            "comparison. Drop them, or start a new session.",
            file=sys.stderr,
        )
        return 2

    tiers = tuple(args.tier) if args.tier else runner.LOCAL_TIERS
    if args.tier_order == "descending":
        tiers = tuple(reversed(tiers))

    try:
        loaded = sample_mod.load_sample(args.sample)
    except (OSError, ValueError) as exc:
        print(f"Could not read {args.sample}: {exc}", file=sys.stderr)
        return 2

    # Structural problems block the run; an undersized sample only warns.
    # The 100-per-bucket rule is about whether *per-bucket conclusions* hold,
    # not whether a run can execute -- and refusing on it would make a trimmed
    # sample unrunnable, which is a reasonable thing to want when smoke-testing
    # a prompt change. A duplicate oracle_id or an anchor card is different:
    # those corrupt the rows themselves, so they still refuse.
    problems = sample_mod.validate_sample(loaded, require_full_size=False)
    if problems:
        print(f"{args.sample} is not a usable sample:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 2

    num_ctx = args.num_ctx if args.num_ctx is not None else runner.DEFAULT_NUM_CTX
    host = args.host or runner.DEFAULT_HOST
    models = dict(runner.DEFAULT_MODELS)
    if args.model_low:
        models["local_low"] = args.model_low
    if args.model_high:
        models["local_high"] = args.model_high

    try:
        if args.resume:
            session_row = store.get_session(connection, args.resume)
            if session_row is None:
                print(
                    f"No session {args.resume!r} in {db_path}. "
                    "`sessions` lists what is there.",
                    file=sys.stderr,
                )
                return 2
            plan = runner.plan_from_session(loaded, session_row)
        else:
            plan = runner.plan_session(
                loaded,
                buckets=tuple(args.bucket),
                tiers=tiers,
                cards_per_bucket=args.cards,
                models=models,
                num_ctx=num_ctx,
                draw_seed=args.draw_seed,
                label=args.label,
                sample_path=str(args.sample),
            )
    except runner.BenchmarkRunError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Host:     {host}")
        print(f"Store:    {db_path}  (no rows would be written by --dry-run)")
        print(sample_mod.describe(loaded))
        print()
        print(runner.render_prompts(plan, limit=2))
        return 0

    try:
        backends = runner.build_local_backends(
            plan.tiers,
            host=host,
            models=plan.models,
            num_ctx=plan.num_ctx,
            timeout_seconds=(
                args.timeout if args.timeout is not None else runner.DEFAULT_TIMEOUT_SECONDS
            ),
            temperature=plan.temperature,
            seed=plan.model_seed,
        )
        report = runner.run_benchmark(
            plan=plan,
            backends=backends,
            connection=connection,
            db_path=db_path,
        )
    except runner.BenchmarkRunError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(report.summary())
    print()
    print(
        "Next: `report --html out.html` to render the side-by-side page, pick "
        "a winner per card, then `pair-verdict --from picks.json`."
    )
    return 0 if not report.interrupted else 130


def _cmd_report(
    *,
    db_path: Path,
    html: Path | None,
    session_id: str | None,
    bucket: str | None,
    tier: str | None,
    quiet: bool,
) -> int:
    """Delegate to `report.main` rather than reimplement its rendering.

    Imported inside the function, not at module scope, so `init`, `review`
    and `set-verdict` don't pay for loading the aggregation and HTML
    templating they never touch. Arguments are rebuilt as an argv list
    because `report.main` owns its own parser — routing through it means the
    alias and the direct entry point cannot diverge in defaults or in how
    they handle a missing database.
    """
    from . import report

    argv = ["--db", str(db_path)]
    if html is not None:
        argv += ["--html", str(html)]
    if session_id is not None:
        argv += ["--session", session_id]
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


def _cmd_pair_verdict(connection, *, args) -> int:
    """Record per-card picks, in bulk from the report page or one at a time."""
    if args.from_file is not None:
        if args.session or args.card or args.winner:
            print(
                "--from reads the session and every pick from the file; "
                "--session/--card/--winner describe a single pick instead. "
                "Use one form or the other.",
                file=sys.stderr,
            )
            return 2
        return _record_picks_file(connection, args.from_file)

    missing = [
        name
        for name, value in (
            ("--session", args.session),
            ("--card", args.card),
            ("--winner", args.winner),
        )
        if not value
    ]
    if missing:
        print(
            f"A single pick needs {', '.join(missing)}. Or pass --from "
            "PICKS.json, which is what the HTML report's 'Collect picks' "
            "button produces.",
            file=sys.stderr,
        )
        return 2

    try:
        run_rows = store.iter_runs(
            connection, session_id=args.session, card_oracle_id=args.card
        )
        store.record_pair(
            connection,
            store.PairVerdict(
                session_id=args.session,
                card_oracle_id=args.card,
                card_name=(
                    str(run_rows[0]["card_name"]) if run_rows else args.card
                ),
                runs={
                    str(row["model_tier"]): int(row["id"]) for row in run_rows
                },
                winner=args.winner,
                notes=args.notes,
            ),
        )
    except (store.UnknownSessionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not run_rows:
        print(
            f"Warning: session {args.session} has no runs recorded for card "
            f"{args.card}, so this pick points at no outputs.",
            file=sys.stderr,
        )
    print(f"{args.session} / {args.card} -> {args.winner}.")
    return 0


def _record_picks_file(connection, path: Path) -> int:
    """Apply the JSON the report page's button produces.

    Validated field by field with named errors rather than trusted: this file
    arrives by copy-paste, and a truncated clipboard is a likelier failure than
    a malformed page.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(
            f"{path} is not valid JSON ({exc}). It should be the whole blob "
            "from the report page's 'Collect picks' box, braces included.",
            file=sys.stderr,
        )
        return 2

    if not isinstance(payload, dict):
        print(f"{path} should hold a JSON object, not a {type(payload).__name__}.", file=sys.stderr)
        return 2

    session_id = payload.get("session_id")
    picks = payload.get("picks")
    if not session_id or not isinstance(picks, list):
        print(
            f"{path} needs a 'session_id' string and a 'picks' list. The "
            "report page's button writes both.",
            file=sys.stderr,
        )
        return 2

    if store.get_session(connection, str(session_id)) is None:
        print(
            f"No session {session_id!r} in this store. `sessions` lists what "
            "is there; --db may be pointing at a different file than the "
            "report was rendered from.",
            file=sys.stderr,
        )
        return 1

    recorded = 0
    for index, pick in enumerate(picks):
        where = f"picks[{index}]"
        if not isinstance(pick, dict):
            print(f"{where} is not an object. Skipped.", file=sys.stderr)
            continue
        card_id = pick.get("card_oracle_id")
        winner = pick.get("winner")
        if not card_id or not winner:
            print(f"{where} needs card_oracle_id and winner. Skipped.", file=sys.stderr)
            continue
        if winner not in PAIR_OUTCOMES:
            print(
                f"{where}: {winner!r} is not a tier or "
                f"{'/'.join(NO_WINNER)}. Skipped.",
                file=sys.stderr,
            )
            continue

        runs = pick.get("runs") or {}
        try:
            store.record_pair(
                connection,
                store.PairVerdict(
                    session_id=str(session_id),
                    card_oracle_id=str(card_id),
                    card_name=str(pick.get("card_name") or card_id),
                    runs={str(k): int(v) for k, v in dict(runs).items()},
                    winner=str(winner),
                    notes=pick.get("notes") or None,
                ),
            )
        except (ValueError, TypeError) as exc:
            print(f"{where}: {exc}. Skipped.", file=sys.stderr)
            continue
        recorded += 1

    print(f"Recorded {recorded:,} of {len(picks):,} pick(s) for {session_id}.")
    if recorded < len(picks):
        print(
            "Some picks were skipped -- see the messages above. Nothing "
            "already recorded was removed.",
            file=sys.stderr,
        )
    return 0 if recorded else 1


def _survive_the_console() -> None:
    """Stop an unencodable character from killing a command mid-print.

    Everything this CLI prints is evidence, and three sources of it carry
    characters a Windows console at cp1252 cannot encode:

    * **card text** — planeswalker loyalty abilities are printed with U+2212
      MINUS SIGN, not a hyphen, so `run --dry-run` crashed on `Ugin, the
      Ineffable` at character 39,646 of its output;
    * **the system prompt** — the rubric contains an em dash;
    * **model output** — `review` prints `raw_response` verbatim, which is
      whatever a model emitted and is bounded by nothing.

    The failure mode is the worst available: a `UnicodeEncodeError` raised
    *during* printing, which abandons a half-written dump and (for `review`)
    loses the loop. Replacing the character costs one glyph in a preview;
    raising costs the command.

    Deliberately does not change the *encoding* — forcing UTF-8 onto a cp1252
    console produces mojibake, which is how this project ended up writing
    ASCII-only demo fixtures in the first place. It only changes the error
    handler, so an unrepresentable character prints as `?` and everything
    around it stays readable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                # A redirected or wrapped stream may refuse; printing is then
                # no more fragile than it was before this call.
                pass


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()
    _survive_the_console()

    db_path = args.db or store.DEFAULT_DB_PATH

    # Handled before the connection is opened: `report` needs no writable
    # handle, and `store.connect` would create an empty store as a side effect
    # of asking for a summary of one that doesn't exist.
    if args.command == "report":
        return _cmd_report(
            db_path=db_path,
            html=args.html,
            session_id=args.session,
            bucket=args.bucket,
            tier=args.tier,
            quiet=args.quiet,
        )

    # `sample` reads the knowledge corpus and writes a JSON file; it has no
    # business creating a run store as a side effect of being asked for a
    # sample, so it too is handled before `connect`.
    if args.command == "sample":
        return _cmd_sample(
            out=args.out,
            validate=args.validate,
            seed=args.seed,
            offline=args.offline,
        )

    try:
        connection = store.connect(db_path)
    except store.StaleStoreError as exc:
        # Not logged as an exception: this is a maintainer-facing instruction,
        # and a traceback would bury the one sentence that says what to delete.
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        logger.exception("Could not open the benchmark store at %s", db_path)
        return 1

    try:
        if args.command == "init":
            return _cmd_init(connection, db_path)
        if args.command == "seed-demo":
            return _cmd_seed_demo(connection, args.cards)
        if args.command == "run":
            return _cmd_run(connection, db_path=db_path, args=args)
        if args.command == "sessions":
            return _cmd_sessions(connection, db_path=db_path, status=args.status)
        if args.command == "review":
            return _cmd_review(
                connection,
                session_id=args.session,
                bucket=args.bucket,
                tier=args.tier,
            )
        if args.command == "set-verdict":
            return _cmd_set_verdict(
                connection, run_id=args.id, verdict=args.verdict, notes=args.notes
            )
        if args.command == "pair-verdict":
            return _cmd_pair_verdict(connection, args=args)
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
