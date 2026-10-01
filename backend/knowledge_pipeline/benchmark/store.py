"""The `benchmark_runs` store: one SQLite file, one table, no ORM.

Backs the 300-card metadata-generation benchmark specified in
`docs/benchmarking-and-testing.md#run-tracking`. Every generation call made
during that benchmark is logged here in full — prompt, raw response, tokens,
latency, cost — because the benchmark has to justify an escalation rule on
cost and latency, not just accuracy, and you cannot recover a per-call cost
from an aggregate score written down afterwards.

**Why a standalone SQLite file rather than either production database.**
This is a maintainer-only tool that runs once, or occasionally when a new
model release is worth re-checking. It is not data either plane serves: the
knowledge database holds the shared card corpus every client reads, the local
database holds one user's collection. Benchmark runs are neither, so they get
no Alembic migration, no SQLAlchemy model in `database/knowledge/models.py`,
and — deliberately — no reference to `KNOWLEDGE_DATABASE_URL` or
`LOCAL_DATABASE_URL` anywhere in this package. Wiring a throwaway maintainer
artifact into a migration lineage would mean every future client upgrade
carries a table nobody but a maintainer will ever write to.

**Why stdlib `sqlite3` rather than SQLAlchemy.** SQLAlchemy earns its place in
`database/` because two dialects (Postgres in the cloud, SQLite in tests) have
to share one set of models and one migration history. Neither applies here:
there is exactly one table, exactly one dialect, and no migrations at all. The
DDL below *is* the schema — readable in one screen, versioned by the fact that
the file is disposable, and re-derivable by deleting `benchmark.db` and
re-running the benchmark.

**Why no settings.** The store needs no configuration, so it reads none —
`DEFAULT_DB_PATH` is a module constant and every entry point takes a
`db_path` override for tests. Adding a `BENCHMARK_DB_PATH` env var would put
a maintainer-only path into a settings class the API also loads, for a file
that has exactly one sensible location: next to the code that writes it.

**Why one table and not two.** A row's technical facts (prompt, response,
timing, cost) and its eventual human verdict describe the same event. Split
across a run log and a scores table they would need a join on every query,
for ~900 rows.

The file lives at `backend/knowledge_pipeline/benchmark/benchmark.db` and is
covered by the repo-root `.gitignore`'s `*.db` rule, same as the Scryfall
cache: the artifact worth keeping is the summary this data produces, not the
database.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH: Path = Path(__file__).resolve().parent / "benchmark.db"
"""Where the benchmark store lives unless a caller overrides it.

Beside the code that writes it, not under `backend/data/`, because the two
have the same lifetime: this file is meaningful only while the benchmark it
records is being run and read.
"""

Bucket = Literal["easy", "medium", "hard"]
"""Difficulty stratum of the 300-card sample (`benchmarking-and-testing.md`)."""

ModelTier = Literal["local_low", "local_high", "frontier"]
"""Which of the three models under test produced a run."""

HumanVerdict = Literal["good", "acceptable", "wrong"]
"""A maintainer's read of one run, recorded after scoring.

Human review is the benchmark's actual scoring mechanism, not a footnote on
top of the metrics — see `benchmarking-and-testing.md#scoring`. A card can
score well on F1 and still be a judgment call the model got wrong.
"""

BUCKETS: tuple[Bucket, ...] = ("easy", "medium", "hard")
MODEL_TIERS: tuple[ModelTier, ...] = ("local_low", "local_high", "frontier")
HUMAN_VERDICTS: tuple[HumanVerdict, ...] = ("good", "acceptable", "wrong")

# Demo rows are tagged by a model_name prefix rather than a boolean column.
# A column would be a schema field that means nothing once real runs exist;
# a prefix is self-describing in every report the reporting layer prints, so
# a synthetic number can never be mistaken for a measured one.
DEMO_MODEL_PREFIX = "demo/"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_oracle_id  TEXT    NOT NULL,
    card_name       TEXT    NOT NULL,
    bucket          TEXT    NOT NULL
        CHECK (bucket IN ('easy', 'medium', 'hard')),
    model_tier      TEXT    NOT NULL
        CHECK (model_tier IN ('local_low', 'local_high', 'frontier')),
    model_name      TEXT    NOT NULL,
    system_prompt   TEXT    NOT NULL,
    user_prompt     TEXT    NOT NULL,
    raw_response    TEXT    NOT NULL,
    schema_valid    INTEGER NOT NULL
        CHECK (schema_valid IN (0, 1)),
    parsed_output   TEXT
        CHECK (schema_valid = 1 OR parsed_output IS NULL),
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    cost_usd        REAL    NOT NULL,
    run_at          TEXT    NOT NULL,
    human_verdict   TEXT
        CHECK (human_verdict IN ('good', 'acceptable', 'wrong')
               OR human_verdict IS NULL),
    human_notes     TEXT,
    reviewed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_bucket_tier
    ON benchmark_runs (bucket, model_tier);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_card
    ON benchmark_runs (card_oracle_id);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_unreviewed
    ON benchmark_runs (id) WHERE human_verdict IS NULL;
"""
"""The whole schema, applied by `initialize`.

The three closed vocabularies are enforced as `CHECK` constraints rather than
left to the `Literal` type hints above. Those hints vanish at runtime, and
this store is written to from a benchmark harness, a CLI, and eventually a
reporting script — a typo'd `"frontier_model"` in any one of them would
silently create a fourth tier and quietly skew every per-tier aggregate.
`Literal` catches that in a type checker; the database catches it always.

`parsed_output`'s check enforces the one cross-column invariant the doc
states: null whenever `schema_valid` is false. Nothing forbids a valid run
from having a null parse — but that pairing should never occur, and letting
the database reject the reverse is the half that matters, because a non-null
parse on an invalid run would mean the harness recorded a guess.

The unreviewed index is partial on purpose: `review` in the CLI only ever
asks for rows with no verdict, and that set shrinks to nothing as review
progresses, so a partial index stays small instead of indexing all ~900 rows
to find the handful still outstanding.
"""


class UnknownRunError(LookupError):
    """`set_verdict` was given a `run_id` that isn't in the table.

    A `LookupError` rather than a silent no-op: reviewing is a manual loop
    over ids a maintainer reads off a report and retypes, so a wrong id is a
    plausible mistake and losing the verdict to it would be invisible.
    """


class SeedRefusedError(RuntimeError):
    """`seed_demo` was called against a table holding real benchmark runs.

    Synthetic rows exist to exercise the reporting layer before the benchmark
    is run. Mixed into measured data they would corrupt every aggregate the
    benchmark's conclusions rest on, and the prefix that makes them obvious
    in a report does nothing for a mean computed over both.
    """


@dataclass(frozen=True)
class BenchmarkRun:
    """One (card, tier) generation call. `id`/`run_at` assigned on insert.

    Frozen because a run is a record of something that already happened: the
    call was made, it took that long, it cost that much. The only field that
    changes afterwards is the human verdict, and that goes through
    `set_verdict` against the stored row, not by mutating this object.

    `id` and `run_at` are deliberately absent rather than optional. The store
    assigns both — an autoincrement primary key and a UTC timestamp taken at
    insert — so a harness cannot accidentally backdate a run or collide two.
    """

    card_oracle_id: str
    card_name: str
    bucket: Bucket
    model_tier: ModelTier
    model_name: str
    system_prompt: str
    user_prompt: str
    raw_response: str
    schema_valid: bool
    parsed_output: dict | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float


def _utc_iso(moment: datetime | None = None) -> str:
    """ISO-8601 UTC, to the second, as TEXT.

    SQLite has no date type, so the storage format has to be one that sorts
    lexicographically in the same order it sorts chronologically — which
    ISO-8601 in a single fixed zone does and a locale-formatted string does
    not. Always UTC: a benchmark may be re-run months later from a different
    machine, and a mixed-offset `run_at` column cannot be ordered at all.
    """
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the benchmark store and return a connection.

    Rows come back as `sqlite3.Row`, so callers index by column name rather
    than by position — a positional read would silently shift the day a
    column is added between the DDL and a report.

    `initialize` runs on every connect. It is cheap and idempotent, and it
    means a fresh path just works: the benchmark harness, the CLI, and a test
    using `tmp_path` all get a usable database from one call, with no
    "did someone run init first?" ordering rule between them.

    Args:
        db_path: Override the store's location. Production callers pass
            nothing and get `DEFAULT_DB_PATH`; tests pass a `tmp_path` so
            they never touch the real `benchmark.db`.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize(connection)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create the table and its indexes if they aren't there yet. Idempotent.

    Every statement in `_SCHEMA` is `IF NOT EXISTS`, so this is safe to call
    on every connect and safe to call on a store already full of runs. There
    is no migration path and deliberately so — the file is disposable, and
    "delete benchmark.db and re-run" is a cheaper answer than a second
    Alembic lineage for a table nobody ships.
    """
    connection.executescript(_SCHEMA)
    connection.commit()


def record_run(connection: sqlite3.Connection, run: BenchmarkRun) -> int:
    """Insert one completed generation call. Returns its new row id; commits.

    Commits per row rather than batching. The benchmark is ~900 synchronous
    model calls, several of them against a local GGUF model at multi-second
    latency, so the commit is free relative to the call that produced the row
    — and an interrupted run (a rate limit, a killed process, a laptop lid)
    keeps everything it had already paid for.

    `parsed_output` is JSON-encoded here and stored as TEXT. `schema_valid`
    becomes INTEGER 0/1, which is what SQLite stores a bool as anyway; making
    it explicit keeps the `CHECK` honest and stops a truthy non-bool sneaking
    in from a harness.
    """
    parsed_json = (
        json.dumps(run.parsed_output, sort_keys=True)
        if run.parsed_output is not None
        else None
    )

    cursor = connection.execute(
        """
        INSERT INTO benchmark_runs (
            card_oracle_id, card_name, bucket, model_tier, model_name,
            system_prompt, user_prompt, raw_response, schema_valid,
            parsed_output, input_tokens, output_tokens, latency_ms,
            cost_usd, run_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.card_oracle_id,
            run.card_name,
            run.bucket,
            run.model_tier,
            run.model_name,
            run.system_prompt,
            run.user_prompt,
            run.raw_response,
            int(run.schema_valid),
            parsed_json,
            run.input_tokens,
            run.output_tokens,
            run.latency_ms,
            run.cost_usd,
            _utc_iso(),
        ),
    )
    connection.commit()

    run_id = int(cursor.lastrowid or 0)
    logger.debug(
        "Recorded run %d: %s / %s (%s)", run_id, run.card_name, run.model_tier, run.bucket
    )
    return run_id


def set_verdict(
    connection: sqlite3.Connection,
    run_id: int,
    verdict: HumanVerdict,
    notes: str | None = None,
) -> None:
    """Record a maintainer's read of one run, stamping `reviewed_at`.

    `reviewed_at` is set here rather than left to the caller so "has this been
    reviewed?" has exactly one answer in the data: a verdict and its timestamp
    are written in the same statement and can never disagree. That pairing is
    what `iter_runs(reviewed=...)` filters on.

    Re-reviewing an already-reviewed run overwrites both the verdict and the
    timestamp. A verdict is a current opinion, not an append-only log — if a
    maintainer revisits a card after seeing the other tiers' answers, the
    later read is the one the benchmark should score against.

    Raises:
        ValueError: `verdict` is outside the closed vocabulary. Checked here
            as well as by the database so the caller gets the list of valid
            values back, not an opaque `IntegrityError`.
        UnknownRunError: no run has that id.
    """
    if verdict not in HUMAN_VERDICTS:
        raise ValueError(
            f"Unknown verdict {verdict!r}. Choose one of: {', '.join(HUMAN_VERDICTS)}."
        )

    cursor = connection.execute(
        """
        UPDATE benchmark_runs
           SET human_verdict = ?, human_notes = ?, reviewed_at = ?
         WHERE id = ?
        """,
        (verdict, notes, _utc_iso(), run_id),
    )
    if cursor.rowcount == 0:
        connection.rollback()
        raise UnknownRunError(f"No benchmark run with id {run_id}.")

    connection.commit()
    logger.debug("Run %d reviewed: %s", run_id, verdict)


def iter_runs(
    connection: sqlite3.Connection,
    *,
    bucket: Bucket | None = None,
    model_tier: ModelTier | None = None,
    reviewed: bool | None = None,
    card_oracle_id: str | None = None,
) -> list[sqlite3.Row]:
    """Rows matching every filter given, oldest first. All filters optional.

    **`parsed_output` comes back as the raw JSON string, not a dict.** Rows
    are plain `sqlite3.Row` objects straight from the driver — the reporting
    layer must `json.loads(row["parsed_output"])` itself, and must handle
    `None` for runs where `schema_valid` is false. Decoding here would mean
    either returning a hand-built dict per row (losing `sqlite3.Row`'s
    by-name indexing and its cheapness over ~900 rows) or mutating a `Row`,
    which is immutable. Likewise `schema_valid` is the stored `0`/`1`.

    Returns a list despite the name — the whole point of this store is
    aggregating and sorting ~900 rows, which callers do with `len()`,
    slicing, and repeated passes. A generator would force every one of them
    to materialize it first. The name matches the agreed contract.

    Args:
        bucket: Restrict to one difficulty stratum.
        model_tier: Restrict to one model under test.
        reviewed: `True` for rows that have a `human_verdict`, `False` for
            rows still awaiting one, `None` (default) for both. The `False`
            case is what the `review` CLI walks.
        card_oracle_id: One card's runs across every tier — the shape a
            per-card tier comparison needs.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if bucket is not None:
        clauses.append("bucket = ?")
        params.append(bucket)
    if model_tier is not None:
        clauses.append("model_tier = ?")
        params.append(model_tier)
    if card_oracle_id is not None:
        clauses.append("card_oracle_id = ?")
        params.append(card_oracle_id)
    if reviewed is True:
        clauses.append("human_verdict IS NOT NULL")
    elif reviewed is False:
        clauses.append("human_verdict IS NULL")

    statement = "SELECT * FROM benchmark_runs"
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += " ORDER BY id"

    return list(connection.execute(statement, params).fetchall())


def run_count(connection: sqlite3.Connection) -> int:
    """How many runs the store holds. What `init` prints."""
    return int(connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0])


# --------------------------------------------------------------------------
# Demo data
# --------------------------------------------------------------------------

# The tiers' stand-in model identifiers. Prefixed `demo/` so they are wrong on
# sight in any report: the real ones are a Hugging Face repo path and a pinned
# Claude model id (`benchmarking-and-testing.md#models-under-test`).
_DEMO_MODELS: dict[ModelTier, str] = {
    "local_low": f"{DEMO_MODEL_PREFIX}qwen2.5-coder-7b",
    "local_high": f"{DEMO_MODEL_PREFIX}qwen2.5-coder-14b",
    "frontier": f"{DEMO_MODEL_PREFIX}claude-frontier",
}

# Per-tier latency band (ms) and per-1k-token cost. Rough, and only has to be
# rough: these exist so a report has *differently shaped* distributions to
# render, not so anyone reads a number off them. The one property worth
# keeping true to the doc is that the local tiers cost exactly zero and are
# slower, which is the whole premise the real benchmark tests.
_DEMO_PROFILE: dict[ModelTier, tuple[int, int, float]] = {
    "local_low": (1800, 6000, 0.0),
    "local_high": (3200, 11000, 0.0),
    "frontier": (900, 2600, 0.004),
}

# Vocabulary values are duplicated as literals rather than imported from
# `metadata_generator.schema`. Importing that package pulls in pydantic and
# the Anthropic SDK through its `__init__`, to decorate throwaway fixture
# data — and the coupling would then have to be maintained in the direction
# that matters least. Demo output is not scored against anything.
_DEMO_ROLES = ["Ramp", "Removal", "Card Draw", "Token Generator", "Protection"]
_DEMO_THEMES = ["Tokens", "Aristocrats", "Artifacts", "Landfall", "Control"]
_DEMO_TAGS = ["ETB Trigger", "Sacrifice Outlet", "Counters Matter", "Cost Reduction"]

# A fixed epoch so re-seeding produces byte-identical `run_at` values. Real
# runs use wall-clock UTC; only the demo path is pinned.
_DEMO_EPOCH = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

_DEMO_SEED = 20260101


def _demo_parsed_output(rng: random.Random, card_name: str) -> dict[str, Any]:
    """A `CardMetadataBlueprint`-shaped dict, plausible but obviously fake."""
    return {
        "summary": f"Synthetic demo metadata for {card_name}. Not a real analysis.",
        "roles": rng.sample(_DEMO_ROLES, k=rng.randint(1, 2)),
        "themes": rng.sample(_DEMO_THEMES, k=rng.randint(1, 2)),
        "game_stage": {
            "early": float(rng.randint(1, 10)),
            "mid": float(rng.randint(1, 10)),
            "late": float(rng.randint(1, 10)),
        },
        "power_rating": float(rng.randint(1, 10)),
        "strengths": ["Demo strength"],
        "weaknesses": ["Demo weakness"],
        "synergy_tags": rng.sample(_DEMO_TAGS, k=1),
    }


def seed_demo(connection: sqlite3.Connection, *, cards: int = 12) -> int:
    """Fill the store with deterministic synthetic runs. Returns rows inserted.

    Exists so the reporting and dashboard layer can be built and reviewed
    *before* the benchmark is run — that benchmark needs 300 hand-labeled
    cards and three model runtimes standing up, and no aggregation code
    should have to wait on any of it. Every row is `cards` × 3 tiers, spread
    round-robin across all three buckets, with roughly a third already
    carrying a human verdict so both sides of `iter_runs(reviewed=...)` have
    something in them.

    Everything is seeded from one fixed `random.Random`, and `run_at` is
    derived from a pinned epoch rather than the clock, so two seeds of the
    same `cards` count produce identical data. A report reviewed against
    yesterday's demo store looks the same today.

    Demo rows already present are deleted first, so re-seeding replaces
    rather than accumulates — otherwise "deterministic" would only hold on a
    fresh file.

    Args:
        cards: How many synthetic cards to generate runs for. Three rows per
            card, one per tier.

    Raises:
        ValueError: `cards` is below 1.
        SeedRefusedError: the table already holds runs that aren't demo rows.
            Synthetic data must never be mixed into measured data — see
            `SeedRefusedError`.
    """
    if cards < 1:
        raise ValueError("cards must be at least 1.")

    real_rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM benchmark_runs WHERE model_name NOT LIKE ?",
            (f"{DEMO_MODEL_PREFIX}%",),
        ).fetchone()[0]
    )
    if real_rows:
        raise SeedRefusedError(
            f"benchmark_runs already holds {real_rows:,} real run(s). Refusing "
            "to mix synthetic demo rows into measured benchmark data. Use a "
            "different --db path, or delete the store if those runs are "
            "disposable."
        )

    connection.execute(
        "DELETE FROM benchmark_runs WHERE model_name LIKE ?",
        (f"{DEMO_MODEL_PREFIX}%",),
    )

    rng = random.Random(_DEMO_SEED)
    inserted = 0

    for index in range(cards):
        card_name = f"Demo Card {index + 1:02d}"
        oracle_id = f"demo-oracle-{index + 1:04d}"
        bucket: Bucket = BUCKETS[index % len(BUCKETS)]

        for tier_index, tier in enumerate(MODEL_TIERS):
            low_ms, high_ms, cost_per_1k = _DEMO_PROFILE[tier]

            # One invalid-schema row in every seven, so the schema-validity
            # rate the benchmark actually cares about isn't a flat 100%.
            schema_valid = (index * len(MODEL_TIERS) + tier_index) % 7 != 3
            parsed = _demo_parsed_output(rng, card_name) if schema_valid else None
            raw_response = (
                json.dumps(parsed, sort_keys=True)
                if parsed is not None
                else "Sure! Here's the metadata for that card: {roles: Ramp,"
            )

            input_tokens = rng.randint(900, 1400)
            output_tokens = rng.randint(120, 320)
            run_at = _utc_iso(_DEMO_EPOCH + timedelta(minutes=inserted))

            cursor = connection.execute(
                """
                INSERT INTO benchmark_runs (
                    card_oracle_id, card_name, bucket, model_tier, model_name,
                    system_prompt, user_prompt, raw_response, schema_valid,
                    parsed_output, input_tokens, output_tokens, latency_ms,
                    cost_usd, run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    oracle_id,
                    card_name,
                    bucket,
                    tier,
                    _DEMO_MODELS[tier],
                    # ASCII only in stored text: these strings are printed
                    # back out by the review CLI, and a Windows console at
                    # cp1252 turns anything else into mojibake.
                    "DEMO SYSTEM PROMPT -- placeholder for the cached taxonomy, "
                    "anchors and rubric block.",
                    f"DEMO USER PROMPT -- rendered card facts for {card_name}.",
                    raw_response,
                    int(schema_valid),
                    json.dumps(parsed, sort_keys=True) if parsed is not None else None,
                    input_tokens,
                    output_tokens,
                    rng.randint(low_ms, high_ms),
                    round((input_tokens + output_tokens) / 1000 * cost_per_1k, 6),
                    run_at,
                ),
            )
            inserted += 1

            if inserted % 3 == 0:
                connection.execute(
                    """
                    UPDATE benchmark_runs
                       SET human_verdict = ?, human_notes = ?, reviewed_at = ?
                     WHERE id = ?
                    """,
                    (
                        HUMAN_VERDICTS[(inserted // 3) % len(HUMAN_VERDICTS)],
                        "Demo verdict -- not a real review.",
                        run_at,
                        cursor.lastrowid,
                    ),
                )

    connection.commit()
    logger.info("Seeded %d demo runs across %d cards", inserted, cards)
    return inserted
