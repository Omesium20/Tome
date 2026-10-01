"""Run tracking for the metadata-generation benchmark.

Backs `docs/benchmarking-and-testing.md#run-tracking`: a standalone SQLite
store holding one row per (card, model tier) generation call made while the
300-card benchmark is run, with the full prompt, the raw response, tokens,
latency, cost, and the maintainer's eventual verdict.

**The store exists; the benchmark has not been run.** Nothing here generates
metadata or calls a model — that is `metadata_generator`, and the escalation
rule it will eventually route on is exactly what the benchmark this store
records is meant to decide. What lands here first is the place the results go,
so the harness and the reporting layer can be built against a fixed contract
instead of inventing one each.

Maintainer-only, and unattached to either production database: no Alembic
migration, no SQLAlchemy model, no `KNOWLEDGE_DATABASE_URL` or
`LOCAL_DATABASE_URL`. See `store.py`'s module docstring for why.

Run it with::

    python -m knowledge_pipeline.benchmark init
    python -m knowledge_pipeline.benchmark seed-demo
    python -m knowledge_pipeline.benchmark review
"""

from .store import (
    BUCKETS,
    DEFAULT_DB_PATH,
    HUMAN_VERDICTS,
    MODEL_TIERS,
    BenchmarkRun,
    Bucket,
    HumanVerdict,
    ModelTier,
    SeedRefusedError,
    UnknownRunError,
    connect,
    initialize,
    iter_runs,
    record_run,
    run_count,
    seed_demo,
    set_verdict,
)

__all__ = [
    "BUCKETS",
    "DEFAULT_DB_PATH",
    "HUMAN_VERDICTS",
    "MODEL_TIERS",
    "BenchmarkRun",
    "Bucket",
    "HumanVerdict",
    "ModelTier",
    "SeedRefusedError",
    "UnknownRunError",
    "connect",
    "initialize",
    "iter_runs",
    "record_run",
    "run_count",
    "seed_demo",
    "set_verdict",
]
