"""The benchmark store: one SQLite file, three tables, no ORM.

Backs the metadata-generation benchmark specified in
`docs/benchmarking-and-testing.md#run-tracking`. Every generation call made
during that benchmark is logged here in full — prompt, raw response, tokens,
latency, cost — because the benchmark has to justify an escalation rule on
cost and latency, not just accuracy, and you cannot recover a per-call cost
from an aggregate score written down afterwards.

**The unit of measurement is a session, not a call.** A session is one
invocation of `run`: a frozen set of cards, drawn from one or more difficulty
buckets, put through every tier in serial succession. That matters because the
only question this benchmark answers is a *comparison*, and a comparison needs
both models to have answered the same cards under the same prompt and the same
decoding knobs. The first version of this store had no such concept — rows
carried a `run_at` timestamp and nothing else tying a sitting together — and
the result was exactly the failure the structure now prevents: across three
sittings the 7B landed on 15 cards and the 14B on 30, all of them from the easy
bucket, and nothing in the harness could notice. `docs/lessons-learned.md`
records it.

So `benchmark_sessions` holds what has to be identical for a comparison to mean
anything (the card list, the tier order, the model tags, `num_ctx`, the
temperature and seed, and the system prompt with its SHA-256), and every row in
`benchmark_runs` belongs to one. There is no unsessioned run.

**Why a standalone SQLite file rather than either production database.**
This is a maintainer-only tool that runs occasionally, when a new model release
is worth re-checking. It is not data either plane serves: the knowledge
database holds the shared card corpus every client reads, the local database
holds one user's collection. Benchmark runs are neither, so they get no Alembic
migration, no SQLAlchemy model in `database/knowledge/models.py`, and —
deliberately — no reference to `KNOWLEDGE_DATABASE_URL` or
`LOCAL_DATABASE_URL` anywhere in this package. Wiring a throwaway maintainer
artifact into a migration lineage would mean every future client upgrade
carries a table nobody but a maintainer will ever write to.

**Why stdlib `sqlite3` rather than SQLAlchemy.** SQLAlchemy earns its place in
`database/` because two dialects (Postgres in the cloud, SQLite in tests) have
to share one set of models and one migration history. Neither applies here:
one dialect, no migrations at all. The DDL below *is* the schema — readable in
one screen, versioned by `PRAGMA user_version`, and re-derivable by deleting
`benchmark.db` and re-running the benchmark.

**Why no settings.** The store needs no configuration, so it reads none —
`DEFAULT_DB_PATH` is a module constant and every entry point takes a
`db_path` override for tests. Adding a `BENCHMARK_DB_PATH` env var would put
a maintainer-only path into a settings class the API also loads, for a file
that has exactly one sensible location: next to the code that writes it.

**Why three tables and not one.** The original docstring argued for one, and
the argument was right about what it covered: a row's technical facts (prompt,
response, timing, cost) and *its own* human verdict describe the same event,
so splitting those two would add a join for nothing. The two tables added since
are not that split.

* `benchmark_sessions` describes a *set* of events, and the fields on it are
  precisely the ones that must not vary between the runs inside it. Repeated
  onto every row they would have N places to disagree with each other, and
  "did these two tiers really see the same prompt?" would stop being a
  question the data can answer.
* `benchmark_pairs` records a judgment about *two* runs. Written onto the runs
  themselves it would land on both, with nothing keeping the two copies
  consistent — and "which of these two was better" is not a property either
  one of them has alone.

The per-run verdict stays on `benchmark_runs`, where the original reasoning
put it, and the two live side by side on purpose: `human_verdict` says whether
an answer was any good, and a pair says which of two answers was better. A
model can win every pair in a session and still be wrong on most cards.

The file lives at `backend/knowledge_pipeline/benchmark/benchmark.db` and is
**tracked in git**, by an explicit negation in the repo-root `.gitignore`
that carves it out of the `*.db` rule. That is the opposite of the Scryfall
cache, and deliberately: a rebuildable download is disposable, but the
measured evidence a benchmark conclusion rests on is not. Re-deriving it
means re-running hundreds of model calls on hardware whose VRAM contention has
since changed, which would not reproduce the same numbers — so losing it to a
gitignore rule would hide the evidence rather than discard a cache.

Note what keeps that file small enough to track: the ~48KB system prompt is
stored **once per session**, not once per run. It used to be denormalized into
every row, which cost 1.8MB of opaque binary for 45 rows and would have cost
tens of megabytes for a full benchmark.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH: Path = Path(__file__).resolve().parent / "benchmark.db"
"""Where the benchmark store lives unless a caller overrides it.

Beside the code that writes it, not under `backend/data/`, because the two
have the same lifetime: this file is meaningful only while the benchmark it
records is being run and read.
"""

SCHEMA_VERSION = 3
"""Stamped into `PRAGMA user_version`; checked on every `connect`.

Version 1 was the single-table store with no sessions, a `system_prompt`
column on every run and no phase timings. There is no migration from it and
there deliberately isn't one — the store is disposable by design, and the 45
rows that existed when sessions landed were easy-only and unpaired, so they
could support no comparison anyway.

Version 2 held one row per (card, tier) and one system prompt per session,
because the harness made one model call per card while production had moved to
two. Three sessions were recorded under it (S-001 to S-003), and what they
measured was a prompt production does not use: the single-call prompt still
carries all 66 anchor ladders, and carries none of `game_stage`'s turn ranges
or its 14 reference profiles, so `power_rating` reproduced the flat band around
7 that splitting the call exists to fix and `game_stage` was scored against a
field the prompt never defined (`docs/lessons-learned.md`). Version 3 is the
conversion: a `pass_name` on every run, two fingerprinted system blocks per
session, and the output schemas fingerprinted beside them.

Those three sessions are archived next to this file as
`benchmark-v2-sessions-001-003.db` rather than deleted, and reading it needs
the version-2 code from git history. That is the cost of having no migration,
accepted twice now with eyes open: what the sessions *established* is written
into `docs/benchmarking-and-testing.md` and `docs/lessons-learned.md`, which is
where a conclusion belongs anyway — the file is the evidence, not the finding.

What this constant buys is that "disposable" stays honest. Every statement in
`_SCHEMA` is `IF NOT EXISTS`, so running the new DDL against an old file would
quietly leave the old table in place and report every new column as missing.
The version check turns that silent half-upgrade into a refusal that names the
file to delete.
"""

Bucket = Literal["easy", "medium", "hard"]
"""Difficulty stratum of the card sample (`benchmarking-and-testing.md`)."""

ModelTier = Literal["local_low", "local_high", "frontier"]
"""Which of the three models under test produced a run."""

PassName = Literal["one", "two"]
"""Which of production's two generation calls a run records.

Production answers each card in two calls, and the harness now makes the same
two rather than one combined call (`docs/knowledge-pipeline.md#two-calls-per-card`).
They are different measurements, not halves of one: pass one decodes seven
fields against the taxonomy with no ladders in the prompt, pass two decodes two
fields against the handful of ladders pass one's tags selected. Summing their
timings would destroy the prefill/decode split that tells a slow model from a
long prompt, and their schema-validity outcomes are independent — a card can
pass one and fail the other.

So `pass_name` is part of a run's identity, not a flag on it. Readers that want
a card's finished answer merge the two (`report.merge_passes`); readers that
want throughput keep them apart.
"""

HumanVerdict = Literal["good", "acceptable", "wrong"]
"""A maintainer's read of one run, recorded after scoring.

Human review is the benchmark's actual scoring mechanism, not a footnote on
top of the metrics — see `benchmarking-and-testing.md#scoring`. A card can
score well on F1 and still be a judgment call the model got wrong.
"""

SessionStatus = Literal["running", "complete", "partial", "aborted"]
"""How a session ended, and therefore how much can be concluded from it.

`complete` means every (card, tier) pair in the session has a row — the only
state in which a win rate describes a like-for-like comparison. `partial` is
the honest label for a session that lost calls to an interrupt or a discarded
timing anomaly; the report warns on it rather than averaging over whatever
happens to be there. `aborted` is a session that stopped on an error.
"""

PairOutcome = Literal["local_low", "local_high", "frontier", "tie", "both_bad"]
"""Which tier won one card, or why neither did.

`tie` and `both_bad` are deliberately distinguished. Both mean "no winner",
but one says the models were equally good and the other says they were equally
useless, and those drive opposite decisions about whether local generation is
viable at all.
"""

BUCKETS: tuple[Bucket, ...] = ("easy", "medium", "hard")
MODEL_TIERS: tuple[ModelTier, ...] = ("local_low", "local_high", "frontier")
PASSES: tuple[PassName, ...] = ("one", "two")
HUMAN_VERDICTS: tuple[HumanVerdict, ...] = ("good", "acceptable", "wrong")
SESSION_STATUSES: tuple[SessionStatus, ...] = (
    "running",
    "complete",
    "partial",
    "aborted",
)
NO_WINNER: tuple[str, ...] = ("tie", "both_bad")
PAIR_OUTCOMES: tuple[str, ...] = MODEL_TIERS + NO_WINNER

# Demo rows are tagged by a model_name prefix rather than a boolean column.
# A column would be a schema field that means nothing once real runs exist;
# a prefix is self-describing in every report the reporting layer prints, so
# a synthetic number can never be mistaken for a measured one.
DEMO_MODEL_PREFIX = "demo/"

# Demo sessions carry a prefix too, so a tab in the HTML report is
# identifiable as fabricated from its id alone, without reading its rows.
DEMO_SESSION_PREFIX = "S-D"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_sessions (
    id                   TEXT    PRIMARY KEY,
    label                TEXT,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT,
    status               TEXT    NOT NULL
        CHECK (status IN ('running', 'complete', 'partial', 'aborted')),
    buckets              TEXT    NOT NULL,
    cards_per_bucket     INTEGER NOT NULL,
    card_ids             TEXT    NOT NULL,
    tiers                TEXT    NOT NULL,
    models               TEXT    NOT NULL,
    sample_path          TEXT,
    sample_seed          INTEGER,
    draw_seed            INTEGER NOT NULL,
    num_ctx              INTEGER NOT NULL,
    temperature          REAL    NOT NULL,
    model_seed           INTEGER,
    system_prompts       TEXT    NOT NULL,
    system_prompts_sha256 TEXT   NOT NULL,
    output_schemas_sha256 TEXT   NOT NULL,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL
        REFERENCES benchmark_sessions (id) ON DELETE CASCADE,
    card_oracle_id  TEXT    NOT NULL,
    card_name       TEXT    NOT NULL,
    bucket          TEXT    NOT NULL
        CHECK (bucket IN ('easy', 'medium', 'hard')),
    model_tier      TEXT    NOT NULL
        CHECK (model_tier IN ('local_low', 'local_high', 'frontier')),
    model_name      TEXT    NOT NULL,
    pass_name       TEXT    NOT NULL
        CHECK (pass_name IN ('one', 'two')),
    user_prompt     TEXT    NOT NULL,
    raw_response    TEXT    NOT NULL,
    schema_valid    INTEGER NOT NULL
        CHECK (schema_valid IN (0, 1)),
    parsed_output   TEXT
        CHECK (schema_valid = 1 OR parsed_output IS NULL),
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    load_ms         INTEGER NOT NULL DEFAULT 0,
    prompt_eval_ms  INTEGER NOT NULL DEFAULT 0,
    eval_ms         INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL,
    run_at          TEXT    NOT NULL,
    human_verdict   TEXT
        CHECK (human_verdict IN ('good', 'acceptable', 'wrong')
               OR human_verdict IS NULL),
    human_notes     TEXT,
    reviewed_at     TEXT,
    UNIQUE (session_id, card_oracle_id, model_tier, pass_name)
);

CREATE TABLE IF NOT EXISTS benchmark_pairs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL
        REFERENCES benchmark_sessions (id) ON DELETE CASCADE,
    card_oracle_id  TEXT    NOT NULL,
    card_name       TEXT    NOT NULL,
    runs            TEXT    NOT NULL,
    winner          TEXT    NOT NULL
        CHECK (winner IN ('local_low', 'local_high', 'frontier',
                          'tie', 'both_bad')),
    notes           TEXT,
    decided_at      TEXT    NOT NULL,
    UNIQUE (session_id, card_oracle_id)
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_session
    ON benchmark_runs (session_id, model_tier, pass_name);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_bucket_tier
    ON benchmark_runs (bucket, model_tier);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_card
    ON benchmark_runs (card_oracle_id);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_unreviewed
    ON benchmark_runs (id) WHERE human_verdict IS NULL;
"""
"""The whole schema, applied by `initialize`.

The closed vocabularies are enforced as `CHECK` constraints rather than left
to the `Literal` type hints above. Those hints vanish at runtime, and this
store is written to from a benchmark harness, a CLI, and a reporting script —
a typo'd `"frontier_model"` in any one of them would silently create a fourth
tier and quietly skew every per-tier aggregate. `Literal` catches that in a
type checker; the database catches it always.

`parsed_output`'s check enforces the one cross-column invariant the doc
states: null whenever `schema_valid` is false. Nothing forbids a valid run
from having a null parse — but that pairing should never occur, and letting
the database reject the reverse is the half that matters, because a non-null
parse on an invalid run would mean the harness recorded a guess.

`UNIQUE (session_id, card_oracle_id, model_tier, pass_name)` is what makes a
session's pairing countable. Resume already skips recorded cells, so a
duplicate would mean two rows for one cell of the comparison grid and a silent
double weight in that tier's mean. The database refuses instead. `pass_name`
joined that key when the harness went from one call per card to production's
two: without it the second pass would collide with the first and the store
would hold whichever call happened to be written last.

`system_prompts` is a JSON object keyed by pass name, with
`system_prompts_sha256` fingerprinting the canonical form of the whole map.
Both blocks are still byte-identical across every card in a session — the
per-card anchor ladders ride in pass two's *user* message precisely to keep
that true — so the comparability key survives the split; it just covers two
strings now.

`output_schemas_sha256` is the other half of that key, and it exists because a
run is determined by the prompt *and the output schema*: the schema becomes the
decoding grammar. Version 2 fingerprinted only the prompt, and it was caught
being wrong in exactly the way that predicts — S-002 and S-003 share a prompt
byte for byte and differ only in whether the tag fields were `required`, which
is the difference between a tier answering with no roles at all and answering
with roles on every card (`docs/lessons-learned.md`). A resume across them
would have been accepted.

The unreviewed index is partial on purpose: `review` in the CLI only ever
asks for rows with no verdict, and that set shrinks to nothing as review
progresses, so a partial index stays small instead of indexing every row to
find the handful still outstanding.
"""


class UnknownRunError(LookupError):
    """`set_verdict` was given a `run_id` that isn't in the table.

    A `LookupError` rather than a silent no-op: reviewing is a manual loop
    over ids a maintainer reads off a report and retypes, so a wrong id is a
    plausible mistake and losing the verdict to it would be invisible.
    """


class UnknownSessionError(LookupError):
    """A session id that isn't in `benchmark_sessions`.

    Raised in preference to letting the foreign key fire, so the caller gets
    the id it asked for back rather than an opaque `IntegrityError` naming a
    constraint.
    """


class SeedRefusedError(RuntimeError):
    """`seed_demo` was called against a store holding real benchmark runs.

    Synthetic rows exist to exercise the reporting layer before the benchmark
    is run. Mixed into measured data they would corrupt every aggregate the
    benchmark's conclusions rest on, and the prefix that makes them obvious
    in a report does nothing for a mean computed over both.
    """


class StaleStoreError(RuntimeError):
    """The file on disk was written by a different version of this schema.

    Carries the path, because the fix is to delete that specific file and the
    message is the only place a maintainer will look for it.
    """


@dataclass(frozen=True)
class BenchmarkSession:
    """One invocation of `run`: a frozen card set, put through every tier.

    Frozen for the same reason `BenchmarkRun` is, with one exception that goes
    through `finish_session` rather than mutation: `status` and `finished_at`
    are decided when the run ends, and the store owns both.

    Every field here is something that must be identical across the tiers for
    the session's comparison to mean anything. `card_ids` in particular is
    written **before the first call**, so the set is a decision the session
    recorded rather than a description of whatever happened to complete.
    """

    buckets: tuple[str, ...]
    cards_per_bucket: int
    card_ids: tuple[str, ...]
    tiers: tuple[str, ...]
    models: Mapping[str, str]
    draw_seed: int
    num_ctx: int
    temperature: float
    system_prompts: Mapping[str, str]
    output_schemas: Mapping[str, Any]
    label: str | None = None
    sample_path: str | None = None
    sample_seed: int | None = None
    model_seed: int | None = None
    notes: str | None = None

    @property
    def system_prompts_sha256(self) -> str:
        """Fingerprint of the exact prompt blocks this session ran.

        Half of the comparability key across sessions. Two sessions whose
        prompts differ cannot be pooled — anchors, taxonomy and rubric all live
        in those blocks, so editing one changes what every rating means — and
        without a fingerprint stored there is no way to find out after the
        fact, because the text is a blob nobody diffs by eye.

        Covers the map rather than a single string, so a change to *either*
        pass moves it. Note that pass two's block is the short one and carries
        no ladders at all: those are rendered per card into its user message,
        which is what keeps a single fingerprint meaningful for a whole session.
        """
        return fingerprint_payload(dict(self.system_prompts))

    @property
    def output_schemas_sha256(self) -> str:
        """Fingerprint of the JSON Schemas the two passes decoded against.

        The other half, and the half version 2 was missing. Under constrained
        decoding the schema *is* the grammar, so two sessions with an identical
        prompt and a changed schema are not comparable — and that is not a
        hypothetical: S-002 and S-003 differ only here, and the difference was
        whether either tier emitted `roles` at all.
        """
        return fingerprint_payload(dict(self.output_schemas))


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

    There is no `system_prompt` field: the prompt belongs to the session, is
    stored there once, and comes back to readers through the join in
    `iter_runs`. A per-run copy would be the same ~48KB repeated per card.

    `load_ms` / `prompt_eval_ms` / `eval_ms` split `latency_ms` into its three
    phases. They are stored rather than merely consulted at write time because
    `latency_ms` alone cannot be re-judged later: it is wall clock, a
    suspended host inflates it, and `runner.timing_anomaly` could only ever
    veto a call as it happened. With the split on the row, decode rate — the
    one throughput number that is not dominated by a ~10k-token prefill — is
    recoverable from the store months afterwards. Backends that cannot report
    the split leave all three at 0, which readers must treat as "unknown",
    never as "instant".
    """

    session_id: str
    card_oracle_id: str
    card_name: str
    bucket: Bucket
    model_tier: ModelTier
    model_name: str
    pass_name: PassName
    user_prompt: str
    raw_response: str
    schema_valid: bool
    parsed_output: dict | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    load_ms: int = 0
    prompt_eval_ms: int = 0
    eval_ms: int = 0


@dataclass(frozen=True)
class PairVerdict:
    """A maintainer's read of one card's answers, across two or more tiers.

    `runs` maps tier to the `benchmark_runs.id` that was actually read, so a
    verdict points at the exact outputs it was formed against. A re-run of the
    same card writes new rows with new ids, and the stale verdict is then
    visibly stale rather than silently reattached to answers nobody judged.
    """

    session_id: str
    card_oracle_id: str
    card_name: str
    runs: Mapping[str, int]
    winner: str
    notes: str | None = None


def prompt_fingerprint(system_prompt: str) -> str:
    """SHA-256 of a system prompt, as hex.

    A free function so the runner, the store and the reporting layer all
    derive the same value the same way, without the reporting layer having to
    import the store (see `report.py`'s layering note).
    """
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def fingerprint_payload(payload: Any) -> str:
    """SHA-256 of any JSON-serializable value, over its canonical encoding.

    Sorted keys and fixed separators, so a fingerprint depends on the content
    rather than on dict ordering or on how a caller happened to spell its
    JSON. Used for the two-prompt map and for the pair of output schemas —
    both of which are "did this change since the last session?" questions
    about a structure, not about a string.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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

    `PRAGMA foreign_keys` is enabled, which SQLite does not do by default.
    Without it `benchmark_runs.session_id` would be documentation rather than
    a constraint, and a harness bug could write runs belonging to a session
    that was never created — rows no tab in the report would ever show.

    `initialize` runs on every connect. It is cheap and idempotent, and it
    means a fresh path just works: the benchmark harness, the CLI, and a test
    using `tmp_path` all get a usable database from one call, with no
    "did someone run init first?" ordering rule between them.

    Args:
        db_path: Override the store's location. Production callers pass
            nothing and get `DEFAULT_DB_PATH`; tests pass a `tmp_path` so
            they never touch the real `benchmark.db`.

    Raises:
        StaleStoreError: the file was written by another schema version.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize(connection, path=path)
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def initialize(connection: sqlite3.Connection, *, path: Path | None = None) -> None:
    """Create the tables and indexes if they aren't there yet. Idempotent.

    Also stamps and enforces `PRAGMA user_version`. The enforcement is the
    point: every statement in `_SCHEMA` is `IF NOT EXISTS`, so applying it to
    a store written by schema version 1 would leave that version's tables
    exactly as they were and then fail much later, on a column that does not
    exist — or worse, succeed at reading a shape nothing writes any more.

    There is no migration path and deliberately so: the file is disposable,
    the tool is maintainer-only, and "delete `benchmark.db` and re-run" is a
    cheaper answer than a second Alembic lineage for a table nobody ships.
    What this function owes a maintainer is to say that out loud instead of
    half-applying a new schema over an old one.
    """
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables = _table_names(connection)
    where = f" at {path}" if path is not None else ""

    if version == SCHEMA_VERSION:
        connection.executescript(_SCHEMA)
        connection.commit()
        return

    if version == 0 and not (tables - {"sqlite_sequence"}):
        # A fresh (or empty) file: create everything and stamp it.
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        return

    if version < SCHEMA_VERSION:
        raise StaleStoreError(
            f"The benchmark store{where} was written by schema version "
            f"{version or 1}; this code expects {SCHEMA_VERSION}. There is no "
            "migration: the store is disposable by design, and a pre-session "
            "store cannot be upgraded into one -- its runs record no session, "
            "so there is nothing to say which tiers were compared against "
            "which cards.\n"
            "Fix: delete the file and re-run `benchmark init`, or pass "
            "--db PATH to start a new store elsewhere."
        )

    raise StaleStoreError(
        f"The benchmark store{where} was written by schema version {version}, "
        f"which is newer than this code's {SCHEMA_VERSION}. Update the code "
        "rather than the file."
    )


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

_SESSION_ID_PATTERN = re.compile(r"^S-(\d+)$")


def next_session_id(connection: sqlite3.Connection) -> str:
    """The next `S-NNN` id, one past the highest already used.

    Sequential and human-sized on purpose. These ids are typed by hand into
    `report --session`, `review --session` and `pair-verdict --session`, and
    they name the tabs in the HTML report — a UUID would be correct and
    unusable. Demo sessions use the `S-D` prefix (`DEMO_SESSION_PREFIX`) and
    are skipped by this counter, so seeding demo data never advances the real
    numbering.
    """
    highest = 0
    for row in connection.execute("SELECT id FROM benchmark_sessions"):
        match = _SESSION_ID_PATTERN.match(str(row[0]))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"S-{highest + 1:03d}"


def create_session(
    connection: sqlite3.Connection,
    session: BenchmarkSession,
    *,
    session_id: str | None = None,
) -> str:
    """Insert a session in `running` state. Returns its id; commits.

    Committed immediately, before any model is called, for the same reason
    `record_run` commits per row: the card set is the session's one
    irreplaceable decision, and a crash during the first call must not be able
    to lose it. A session row with no runs is a perfectly readable state —
    `partial`, nothing recorded — whereas runs with no session cannot exist at
    all.
    """
    resolved = session_id or next_session_id(connection)
    connection.execute(
        """
        INSERT INTO benchmark_sessions (
            id, label, started_at, finished_at, status, buckets,
            cards_per_bucket, card_ids, tiers, models, sample_path,
            sample_seed, draw_seed, num_ctx, temperature, model_seed,
            system_prompts, system_prompts_sha256, output_schemas_sha256, notes
        ) VALUES (?, ?, ?, NULL, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?)
        """,
        (
            resolved,
            session.label,
            _utc_iso(),
            json.dumps(list(session.buckets)),
            int(session.cards_per_bucket),
            json.dumps(list(session.card_ids)),
            json.dumps(list(session.tiers)),
            json.dumps(dict(session.models), sort_keys=True),
            session.sample_path,
            session.sample_seed,
            int(session.draw_seed),
            int(session.num_ctx),
            float(session.temperature),
            session.model_seed,
            json.dumps(dict(session.system_prompts), sort_keys=True),
            session.system_prompts_sha256,
            session.output_schemas_sha256,
            session.notes,
        ),
    )
    connection.commit()
    logger.info(
        "Session %s opened: %d card(s), tiers %s",
        resolved,
        len(session.card_ids),
        ", ".join(session.tiers),
    )
    return resolved


def finish_session(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    status: SessionStatus,
    notes: str | None = None,
) -> None:
    """Close a session with its final status, stamping `finished_at`.

    `status` is the report's gate on every paired number. A session that lost
    calls is `partial`, and a win rate over a partial pairing compares two
    different card sets — the exact defect sessions exist to prevent — so the
    renderers refuse to present one without saying so.

    Raises:
        ValueError: `status` is outside `SESSION_STATUSES`.
        UnknownSessionError: no session has that id.
    """
    if status not in SESSION_STATUSES:
        raise ValueError(
            f"Unknown session status {status!r}. Choose one of: "
            f"{', '.join(SESSION_STATUSES)}."
        )

    cursor = connection.execute(
        """
        UPDATE benchmark_sessions
           SET status = ?, finished_at = ?, notes = COALESCE(?, notes)
         WHERE id = ?
        """,
        (status, _utc_iso(), notes, session_id),
    )
    if cursor.rowcount == 0:
        connection.rollback()
        raise UnknownSessionError(f"No benchmark session with id {session_id!r}.")
    connection.commit()
    logger.info("Session %s closed: %s", session_id, status)


def get_session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    """One session row, or `None`. JSON columns come back as strings."""
    return connection.execute(
        "SELECT * FROM benchmark_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def require_session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    """`get_session`, raising `UnknownSessionError` instead of returning None."""
    row = get_session(connection, session_id)
    if row is None:
        raise UnknownSessionError(f"No benchmark session with id {session_id!r}.")
    return row


def iter_sessions(
    connection: sqlite3.Connection, *, status: SessionStatus | None = None
) -> list[sqlite3.Row]:
    """Every session, oldest first.

    Oldest-first to match `iter_runs`, not because that is the useful reading
    order — the report shows sessions newest first and reverses this itself.
    One ordering convention in the store is worth more than each query
    guessing at a presentation preference.
    """
    statement = "SELECT * FROM benchmark_sessions"
    params: list[Any] = []
    if status is not None:
        statement += " WHERE status = ?"
        params.append(status)
    statement += " ORDER BY started_at, id"
    return list(connection.execute(statement, params).fetchall())


def session_card_ids(session_row: Mapping[str, Any]) -> tuple[str, ...]:
    """The frozen card set, decoded. JSON columns are strings on the way out."""
    return tuple(json.loads(session_row["card_ids"]))


def session_tiers(session_row: Mapping[str, Any]) -> tuple[str, ...]:
    """The session's tiers, in execution order."""
    return tuple(json.loads(session_row["tiers"]))


def session_buckets(session_row: Mapping[str, Any]) -> tuple[str, ...]:
    """The buckets the session drew from, in the order they were requested."""
    return tuple(json.loads(session_row["buckets"]))


def session_models(session_row: Mapping[str, Any]) -> dict[str, str]:
    """Tier to the model identifier that actually answered for it."""
    return dict(json.loads(session_row["models"]))


def recorded_cells(
    connection: sqlite3.Connection, session_id: str
) -> set[tuple[str, str, str]]:
    """The (oracle_id, tier, pass) cells this session already has rows for.

    Three columns, not whole rows: resume asks a question about the grid, not
    about the answers in it, and `iter_runs` would read every response body to
    find out which cells are filled.

    `pass_name` is in the key because the grid gained a dimension when the
    harness started making production's two calls. Without it, a card whose
    pass one landed and whose pass two was interrupted would look finished, and
    resume would skip the call that is actually missing.
    """
    cursor = connection.execute(
        "SELECT card_oracle_id, model_tier, pass_name FROM benchmark_runs "
        " WHERE session_id = ?",
        (session_id,),
    )
    return {(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()}


def missing_cells(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    card_ids: Sequence[str],
    tiers: Sequence[str],
    passes: Sequence[str] = PASSES,
) -> list[tuple[str, str, str]]:
    """Cells of the session's comparison grid that have no row.

    What decides `complete` versus `partial`. Computed against the session's
    *frozen* card list rather than against whatever was recorded, because the
    question is "did every intended call land?" and a list derived from the
    rows themselves can only ever answer yes.

    A card whose pass one failed its schema has no pass two — the second call
    takes the first's output as input, so there is nothing to send. That leaves
    a genuinely missing cell and the session is `partial`, which is the honest
    label: a card with no `power_rating` cannot be compared on one.
    """
    have = recorded_cells(connection, session_id)
    return [
        (card_id, tier, pass_name)
        for card_id in card_ids
        for tier in tiers
        for pass_name in passes
        if (card_id, tier, pass_name) not in have
    ]


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def record_run(connection: sqlite3.Connection, run: BenchmarkRun) -> int:
    """Insert one completed generation call. Returns its new row id; commits.

    Commits per row rather than batching. The benchmark is hundreds of
    synchronous model calls, many of them against a local quantized model at
    multi-second latency, so the commit is free relative to the call that
    produced the row — and an interrupted run (a rate limit, a killed process,
    a laptop lid) keeps everything it had already paid for.

    `parsed_output` is JSON-encoded here and stored as TEXT. `schema_valid`
    becomes INTEGER 0/1, which is what SQLite stores a bool as anyway; making
    it explicit keeps the `CHECK` honest and stops a truthy non-bool sneaking
    in from a harness.

    Raises:
        UnknownSessionError: `run.session_id` names no session. Checked here
            rather than left to the foreign key so the message carries the id.
    """
    if get_session(connection, run.session_id) is None:
        raise UnknownSessionError(
            f"Cannot record a run against session {run.session_id!r}: no such "
            "session. Open one with `create_session` first -- every run "
            "belongs to a session, so that the tiers of a comparison can be "
            "shown to have answered the same cards."
        )

    parsed_json = (
        json.dumps(run.parsed_output, sort_keys=True)
        if run.parsed_output is not None
        else None
    )

    cursor = connection.execute(
        """
        INSERT INTO benchmark_runs (
            session_id, card_oracle_id, card_name, bucket, model_tier,
            model_name, pass_name, user_prompt, raw_response, schema_valid,
            parsed_output, input_tokens, output_tokens, latency_ms,
            load_ms, prompt_eval_ms, eval_ms, cost_usd, run_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.session_id,
            run.card_oracle_id,
            run.card_name,
            run.bucket,
            run.model_tier,
            run.model_name,
            run.pass_name,
            run.user_prompt,
            run.raw_response,
            int(run.schema_valid),
            parsed_json,
            run.input_tokens,
            run.output_tokens,
            run.latency_ms,
            run.load_ms,
            run.prompt_eval_ms,
            run.eval_ms,
            run.cost_usd,
            _utc_iso(),
        ),
    )
    connection.commit()

    run_id = int(cursor.lastrowid or 0)
    logger.debug(
        "Recorded run %d: %s / %s pass %s (%s) in %s",
        run_id,
        run.card_name,
        run.model_tier,
        run.pass_name,
        run.bucket,
        run.session_id,
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
    session_id: str | None = None,
    bucket: Bucket | None = None,
    model_tier: ModelTier | None = None,
    pass_name: PassName | None = None,
    reviewed: bool | None = None,
    card_oracle_id: str | None = None,
) -> list[sqlite3.Row]:
    """Rows matching every filter given, oldest first. All filters optional.

    **The system prompt is joined in from the session**, aliased as
    `system_prompt`, so readers that want the full prompt for one run keep
    asking for it by that name even though the column moved. The alternative
    was storing a copy per row.

    Since the session holds one block *per pass*, the join picks the block for
    the row's own `pass_name` — a pass-two row comes back with pass two's
    prompt, not with a map a reader would have to index itself. Readers that
    want the whole map read `benchmark_sessions.system_prompts` directly.

    **`parsed_output` comes back as the raw JSON string, not a dict.** Rows
    are plain `sqlite3.Row` objects straight from the driver — the reporting
    layer must `json.loads(row["parsed_output"])` itself, and must handle
    `None` for runs where `schema_valid` is false. Decoding here would mean
    either returning a hand-built dict per row (losing `sqlite3.Row`'s
    by-name indexing and its cheapness) or mutating a `Row`, which is
    immutable. Likewise `schema_valid` is the stored `0`/`1`.

    Returns a list despite the name — the whole point of this store is
    aggregating and sorting rows, which callers do with `len()`, slicing, and
    repeated passes. A generator would force every one of them to materialize
    it first. The name matches the agreed contract.

    Args:
        session_id: One session's runs — the shape every per-tab view needs.
        bucket: Restrict to one difficulty stratum.
        model_tier: Restrict to one model under test.
        pass_name: Restrict to one of production's two generation calls.
        reviewed: `True` for rows that have a `human_verdict`, `False` for
            rows still awaiting one, `None` (default) for both. The `False`
            case is what the `review` CLI walks.
        card_oracle_id: One card's runs across every tier — the shape a
            per-card tier comparison needs.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if session_id is not None:
        clauses.append("r.session_id = ?")
        params.append(session_id)
    if bucket is not None:
        clauses.append("r.bucket = ?")
        params.append(bucket)
    if model_tier is not None:
        clauses.append("r.model_tier = ?")
        params.append(model_tier)
    if pass_name is not None:
        clauses.append("r.pass_name = ?")
        params.append(pass_name)
    if card_oracle_id is not None:
        clauses.append("r.card_oracle_id = ?")
        params.append(card_oracle_id)
    if reviewed is True:
        clauses.append("r.human_verdict IS NOT NULL")
    elif reviewed is False:
        clauses.append("r.human_verdict IS NULL")

    statement = (
        "SELECT r.*, "
        "       json_extract(s.system_prompts, '$.' || r.pass_name) "
        "           AS system_prompt, "
        "       s.system_prompts_sha256 AS system_prompts_sha256, "
        "       s.output_schemas_sha256 AS output_schemas_sha256, "
        "       s.label AS session_label, s.status AS session_status "
        "  FROM benchmark_runs r "
        "  JOIN benchmark_sessions s ON s.id = r.session_id"
    )
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += " ORDER BY r.id"

    return list(connection.execute(statement, params).fetchall())


def run_count(connection: sqlite3.Connection) -> int:
    """How many runs the store holds. What `init` prints."""
    return int(connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0])


def session_count(connection: sqlite3.Connection) -> int:
    """How many sessions the store holds. What `init` prints alongside runs."""
    return int(
        connection.execute("SELECT COUNT(*) FROM benchmark_sessions").fetchone()[0]
    )


# --------------------------------------------------------------------------
# Pair verdicts
# --------------------------------------------------------------------------


def record_pair(connection: sqlite3.Connection, pair: PairVerdict) -> int:
    """Record which tier won one card. Returns the row id; commits.

    Upserts on `(session_id, card_oracle_id)`: re-deciding a card replaces the
    earlier pick, for the same reason `set_verdict` overwrites a verdict — a
    judgment is a current opinion, and the later read is the one to score
    against. The `UNIQUE` constraint is what makes that an update rather than
    a second opinion silently double-counted in the win rate.

    Raises:
        ValueError: `winner` is outside `PAIR_OUTCOMES`.
        UnknownSessionError: the session doesn't exist.
    """
    if pair.winner not in PAIR_OUTCOMES:
        raise ValueError(
            f"Unknown pair outcome {pair.winner!r}. Choose a tier "
            f"({', '.join(MODEL_TIERS)}) or {' / '.join(NO_WINNER)}."
        )
    require_session(connection, pair.session_id)

    cursor = connection.execute(
        """
        INSERT INTO benchmark_pairs (
            session_id, card_oracle_id, card_name, runs, winner, notes,
            decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (session_id, card_oracle_id) DO UPDATE SET
            card_name  = excluded.card_name,
            runs       = excluded.runs,
            winner     = excluded.winner,
            notes      = excluded.notes,
            decided_at = excluded.decided_at
        """,
        (
            pair.session_id,
            pair.card_oracle_id,
            pair.card_name,
            json.dumps({str(k): int(v) for k, v in pair.runs.items()}, sort_keys=True),
            pair.winner,
            pair.notes,
            _utc_iso(),
        ),
    )
    connection.commit()
    logger.debug(
        "Pair recorded: %s / %s -> %s", pair.session_id, pair.card_name, pair.winner
    )
    return int(cursor.lastrowid or 0)


def iter_pairs(
    connection: sqlite3.Connection, *, session_id: str | None = None
) -> list[sqlite3.Row]:
    """Pair verdicts, oldest first. `runs` comes back as a JSON string."""
    statement = "SELECT * FROM benchmark_pairs"
    params: list[Any] = []
    if session_id is not None:
        statement += " WHERE session_id = ?"
        params.append(session_id)
    statement += " ORDER BY id"
    return list(connection.execute(statement, params).fetchall())


def pair_count(connection: sqlite3.Connection, session_id: str | None = None) -> int:
    """How many cards have a pick. The review-progress number."""
    if session_id is None:
        return int(
            connection.execute("SELECT COUNT(*) FROM benchmark_pairs").fetchone()[0]
        )
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM benchmark_pairs WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    )


# --------------------------------------------------------------------------
# Demo data
# --------------------------------------------------------------------------

# The tiers' stand-in model identifiers. Prefixed `demo/` so they are wrong on
# sight in any report: the real ones are an Ollama tag and a pinned Claude
# model id (`benchmarking-and-testing.md#models-under-test`).
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

# ASCII only: this string is printed back out by the review CLI, and a Windows
# console at cp1252 turns anything else into mojibake.
_DEMO_SYSTEM_PROMPTS: dict[str, str] = {
    "one": (
        "DEMO SYSTEM PROMPT (pass one) -- placeholder for the taxonomy and "
        "game_stage calibration block. No model was called."
    ),
    "two": (
        "DEMO SYSTEM PROMPT (pass two) -- placeholder for the rating rubric. "
        "The per-card anchor ladders ride in the user message. No model was "
        "called."
    ),
}

# Placeholders for the two decoding grammars, fingerprinted the same way real
# ones are. Shaped like a JSON Schema only as far as a reader glancing at the
# session row needs; nothing decodes against them.
_DEMO_OUTPUT_SCHEMAS: dict[str, Any] = {
    "one": {"title": "DemoPassOne", "type": "object", "demo": True},
    "two": {"title": "DemoPassTwo", "type": "object", "demo": True},
}


def _demo_pass_output(
    rng: random.Random, card_name: str, pass_name: str
) -> dict[str, Any]:
    """One pass's output as a dict, plausible but obviously fake.

    Split the way production's two calls are: pass one holds the seven fields
    decided before any ladder is shown, pass two the two decided against the
    ladders pass one's tags selected. A single combined blob would have made
    the demo store the one place in the codebase where a run row carries a
    whole blueprint, which is the shape the report must stop assuming.
    """
    if pass_name == "two":
        return {
            "power_rating": float(rng.randint(1, 10)),
            "summary": (
                f"Synthetic demo metadata for {card_name}. Not a real analysis."
            ),
        }

    roles = rng.sample(_DEMO_ROLES, k=rng.randint(1, 2))
    return {
        "mechanics": f"Synthetic demo restatement of {card_name}'s rules text.",
        "synergy_tags": rng.sample(_DEMO_TAGS, k=1),
        "roles": roles,
        "primary_role": roles[0],
        "themes": rng.sample(_DEMO_THEMES, k=rng.randint(1, 2)),
        "game_stage": {
            "early": float(rng.randint(1, 10)),
            "mid": float(rng.randint(1, 10)),
            "late": float(rng.randint(1, 10)),
        },
        "strengths": ["Demo strength"],
        "weaknesses": ["Demo weakness"],
    }


def seed_demo(connection: sqlite3.Connection, *, cards: int = 12) -> int:
    """Fill the store with deterministic synthetic sessions. Returns rows added.

    Exists so the reporting and dashboard layer can be built and reviewed
    *before* a real benchmark is run — that needs hand-labeled cards and model
    runtimes standing up, and no aggregation code should have to wait on any of
    it.

    **One demo session per bucket**, so the report's tab bar has something to
    be a tab bar for, and each session is internally paired: every card in it
    gets a row for all three tiers *and* both of production's two passes, which
    is the shape a real session guarantees and the shape every paired number
    assumes. Roughly a third of
    the rows carry a human verdict so both sides of `iter_runs(reviewed=...)`
    have something in them, and every other card gets a pair verdict so the
    win-rate view has both decided and undecided cards to render.

    Everything is seeded from one fixed `random.Random`, and `run_at` is
    derived from a pinned epoch rather than the clock, so two seeds of the
    same `cards` count produce identical data. A report reviewed against
    yesterday's demo store looks the same today.

    Demo sessions already present are deleted first (cascading to their runs
    and pairs), so re-seeding replaces rather than accumulates — otherwise
    "deterministic" would only hold on a fresh file.

    Args:
        cards: How many synthetic cards in total, spread across the three
            bucket sessions. Six rows per card: three tiers x two passes.

    Raises:
        ValueError: `cards` is below 1.
        SeedRefusedError: the store already holds runs that aren't demo rows.
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

    # Cascades to the runs and pairs of those sessions, which is why the
    # foreign keys declare ON DELETE CASCADE and `connect` turns them on.
    connection.execute(
        "DELETE FROM benchmark_sessions WHERE id LIKE ?",
        (f"{DEMO_SESSION_PREFIX}%",),
    )
    connection.commit()

    rng = random.Random(_DEMO_SEED)
    per_bucket = max(1, cards // len(BUCKETS))
    inserted = 0
    numbered = 0

    for bucket_index, bucket in enumerate(BUCKETS, start=1):
        session_id = f"{DEMO_SESSION_PREFIX}{bucket_index:02d}"
        bucket_cards = []
        for _ in range(per_bucket):
            numbered += 1
            bucket_cards.append(
                (f"demo-oracle-{numbered:04d}", f"Demo Card {numbered:02d}")
            )

        create_session(
            connection,
            BenchmarkSession(
                buckets=(bucket,),
                cards_per_bucket=per_bucket,
                card_ids=tuple(oracle_id for oracle_id, _ in bucket_cards),
                tiers=MODEL_TIERS,
                models=dict(_DEMO_MODELS),
                draw_seed=_DEMO_SEED,
                num_ctx=12288,
                temperature=0.0,
                model_seed=_DEMO_SEED,
                system_prompts=dict(_DEMO_SYSTEM_PROMPTS),
                output_schemas=dict(_DEMO_OUTPUT_SCHEMAS),
                label=f"demo {bucket}",
                notes="Synthetic session from `seed-demo`. No model was called.",
            ),
            session_id=session_id,
        )

        for position, (oracle_id, card_name) in enumerate(bucket_cards, start=1):
            run_ids: dict[str, int] = {}
            for tier_index, tier in enumerate(MODEL_TIERS):
                low_ms, high_ms, cost_per_1k = _DEMO_PROFILE[tier]

                for pass_name in PASSES:
                    # One invalid-schema row in every seven, so the
                    # schema-validity rate the benchmark actually cares about
                    # isn't a flat 100% -- and always on pass *two*. A failed
                    # pass one has no pass two to record (the second call takes
                    # the first's output as input), so faking one would leave a
                    # hole in the grid while this function still reports
                    # `complete`, and the report's gate on that label is the
                    # one thing a demo store must not teach wrongly.
                    schema_valid = (
                        pass_name == "one" or (inserted + tier_index) % 7 != 3
                    )
                    parsed = (
                        _demo_pass_output(rng, card_name, pass_name)
                        if schema_valid
                        else None
                    )
                    raw_response = (
                        json.dumps(parsed, sort_keys=True)
                        if parsed is not None
                        else "Sure! The rating for that card is: {power: high,"
                    )

                    # Pass two sends the short rubric plus a few ladders rather
                    # than the whole taxonomy, and answers two fields rather
                    # than seven, so both token counts are a fraction of pass
                    # one's. Flat bands would hide exactly the asymmetry the
                    # per-pass view exists to show.
                    if pass_name == "one":
                        input_tokens = rng.randint(900, 1400)
                        output_tokens = rng.randint(120, 320)
                        latency_ms = rng.randint(low_ms, high_ms)
                    else:
                        input_tokens = rng.randint(300, 600)
                        output_tokens = rng.randint(20, 60)
                        latency_ms = rng.randint(low_ms // 3, high_ms // 3)
                    run_at = _utc_iso(_DEMO_EPOCH + timedelta(minutes=inserted))

                    cursor = connection.execute(
                        """
                        INSERT INTO benchmark_runs (
                            session_id, card_oracle_id, card_name, bucket,
                            model_tier, model_name, pass_name, user_prompt,
                            raw_response, schema_valid, parsed_output,
                            input_tokens, output_tokens, latency_ms, load_ms,
                            prompt_eval_ms, eval_ms, cost_usd, run_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            oracle_id,
                            card_name,
                            bucket,
                            tier,
                            _DEMO_MODELS[tier],
                            pass_name,
                            (
                                "DEMO USER PROMPT -- rendered card facts for "
                                f"{card_name} (pass {pass_name})."
                            ),
                            raw_response,
                            int(schema_valid),
                            (
                                json.dumps(parsed, sort_keys=True)
                                if parsed is not None
                                else None
                            ),
                            input_tokens,
                            output_tokens,
                            latency_ms,
                            0,
                            int(latency_ms * 0.6),
                            int(latency_ms * 0.4),
                            round(
                                (input_tokens + output_tokens) / 1000 * cost_per_1k,
                                6,
                            ),
                            run_at,
                        ),
                    )
                    # The cell's representative id is its *last* pass, so a
                    # pick points at the row carrying `power_rating` -- the
                    # field a pick is mostly made on.
                    run_ids[tier] = int(cursor.lastrowid or 0)
                    inserted += 1

                    # Every fifth row, not every fourth: a card now writes six
                    # rows (three tiers x two passes), and 4 divides 6's stride
                    # badly enough to land on the same (tier, pass) repeatedly
                    # -- the same mistake a stride of 3 made when there were
                    # three rows per card, which left review coverage at 100%
                    # for one tier and 0% for the others. 5 is coprime with 6,
                    # so the verdicts walk every tier and both passes.
                    if inserted % 5 == 0:
                        connection.execute(
                            """
                            UPDATE benchmark_runs
                               SET human_verdict = ?, human_notes = ?,
                                   reviewed_at = ?
                             WHERE id = ?
                            """,
                            (
                                HUMAN_VERDICTS[(inserted // 5) % len(HUMAN_VERDICTS)],
                                "Demo verdict -- not a real review.",
                                run_at,
                                cursor.lastrowid,
                            ),
                        )

            # A pick on every other card, so a demo session shows both a
            # partial win rate and the undecided remainder.
            if position % 2 == 0:
                connection.execute(
                    """
                    INSERT INTO benchmark_pairs (
                        session_id, card_oracle_id, card_name, runs, winner,
                        notes, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        oracle_id,
                        card_name,
                        json.dumps(run_ids, sort_keys=True),
                        PAIR_OUTCOMES[inserted % len(PAIR_OUTCOMES)],
                        "Demo pick -- not a real comparison.",
                        _utc_iso(_DEMO_EPOCH + timedelta(minutes=inserted)),
                    ),
                )

        finish_session(connection, session_id, status="complete")

    connection.commit()
    logger.info("Seeded %d demo runs across %d demo session(s)", inserted, len(BUCKETS))
    return inserted
