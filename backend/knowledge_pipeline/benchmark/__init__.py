"""The metadata-generation benchmark: paired sessions, and their run log.

Backs `docs/benchmarking-and-testing.md#run-tracking`. A standalone SQLite
store holds one **session** per invocation of `run` — a frozen card set drawn
from one or more difficulty buckets — and one row per (card, model tier)
generation call inside it, with the full prompt, the raw response, tokens,
phase timings, latency, cost, the maintainer's eventual per-run verdict, and
the per-card pick of which tier answered better.

**The session is the unit, because the comparison is the question.** Every
tier of a session answers the same cards under the same prompt and the same
decoding settings, which is the only arrangement in which "which model is
better" means anything. The first version of this package had no such concept,
and the store it produced could not answer that question at all: the 7B had
run 15 cards, the 14B 30, all of them from the easy bucket, and nothing in the
harness could notice. `docs/lessons-learned.md` records it.

**The store and the harness both exist now.** `sample.py` picks the 300 cards
and draws a session's subset from them, `runner.py` runs that subset through
each tier in turn and records every call, `store.py` holds sessions, runs and
picks, `report.py` aggregates them into a tabbed page — one tab per session,
plus a pooled Overview. Two things are still missing: the hand-labeled ground
truth the accuracy metrics need, and the frontier tier — so `report` keeps
saying "unavailable" rather than zero for accuracy, and the hard-bucket gap
against frontier stays uncomputable. Without ground truth the sound judgment
is a *relative* one, which is what the per-card picks are for.

Only `runner.py` calls a model, and only `sample.py` and `runner.py` read the
knowledge corpus. `store.py` and `report.py` depend on neither — see `store.py`'s
docstring — so inspecting or reviewing a run needs no database URL, no model
runtime and no vendor SDK. Keep it that way: the import cost is the least of it,
and a reporting layer that cannot reach a model cannot accidentally score one
model's output against another's.

Maintainer-only, and unattached to either production database: no Alembic
migration, no SQLAlchemy model, no `KNOWLEDGE_DATABASE_URL` or
`LOCAL_DATABASE_URL`. See `store.py`'s module docstring for why.

Run it with::

    python -m knowledge_pipeline.benchmark init
    python -m knowledge_pipeline.benchmark sample --out
    python -m knowledge_pipeline.benchmark run --bucket hard --cards 30
    python -m knowledge_pipeline.benchmark sessions
    python -m knowledge_pipeline.benchmark report --html out.html
    python -m knowledge_pipeline.benchmark pair-verdict --from picks.json
"""

from .store import (
    BUCKETS,
    DEFAULT_DB_PATH,
    HUMAN_VERDICTS,
    MODEL_TIERS,
    NO_WINNER,
    PAIR_OUTCOMES,
    PASSES,
    SCHEMA_VERSION,
    SESSION_STATUSES,
    BenchmarkRun,
    BenchmarkSession,
    Bucket,
    HumanVerdict,
    ModelTier,
    PairOutcome,
    PairVerdict,
    PassName,
    SeedRefusedError,
    SessionStatus,
    StaleStoreError,
    UnknownRunError,
    UnknownSessionError,
    connect,
    create_session,
    finish_session,
    get_session,
    initialize,
    iter_pairs,
    iter_runs,
    iter_sessions,
    missing_cells,
    pair_count,
    prompt_fingerprint,
    fingerprint_payload,
    record_pair,
    record_run,
    recorded_cells,
    run_count,
    seed_demo,
    session_count,
    set_verdict,
)

__all__ = [
    "BUCKETS",
    "DEFAULT_DB_PATH",
    "HUMAN_VERDICTS",
    "MODEL_TIERS",
    "NO_WINNER",
    "PAIR_OUTCOMES",
    "PASSES",
    "SCHEMA_VERSION",
    "SESSION_STATUSES",
    "BenchmarkRun",
    "BenchmarkSession",
    "Bucket",
    "HumanVerdict",
    "ModelTier",
    "PairOutcome",
    "PairVerdict",
    "PassName",
    "SeedRefusedError",
    "SessionStatus",
    "StaleStoreError",
    "UnknownRunError",
    "UnknownSessionError",
    "connect",
    "create_session",
    "finish_session",
    "get_session",
    "initialize",
    "iter_pairs",
    "iter_runs",
    "iter_sessions",
    "missing_cells",
    "pair_count",
    "prompt_fingerprint",
    "fingerprint_payload",
    "record_pair",
    "record_run",
    "recorded_cells",
    "run_count",
    "seed_demo",
    "session_count",
    "set_verdict",
]
