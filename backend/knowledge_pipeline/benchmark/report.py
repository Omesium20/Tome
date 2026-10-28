"""Reads the benchmark run log and renders it for a human decision.

`docs/benchmarking-and-testing.md` specifies a 300-card, 3-tier benchmark whose
whole purpose is to answer three questions: is a 14B local model viable at all,
what signal should trigger escalation to frontier, and how much of the corpus
that rule would route to frontier. `store.py` records the raw material for those
answers — sessions, one row per (card, tier) generation call inside them, and
the per-card picks between tiers. This module turns that into something a
maintainer can actually read.

**The primary scope is one session, because that is the only scope in which a
comparison is sound.** A session is one invocation of `run`: a frozen card set
put through every tier in serial succession, under one prompt and one set of
decoding knobs. Pooling sessions is offered and is useful — a prompt revision
or a new model release makes a second session, and the trend across them
matters — but it is a deliberate step away from the comparison the benchmark
exists to produce, so the HTML output is tabbed by session with everything
pooled on an Overview tab, and the Overview says out loud when the sessions it
pools do not share a `system_prompt_sha256`.

Within a session, and within the Overview, the next scope down is the one the
document specifies:

**cross-tier comparison _within a difficulty bucket_, not the overall average.** That is not a layout preference; it is the document's
central instruction: "the number that decides anything is the *gap* between
tiers on the hard bucket, not the average across all 300." A report whose
headline is "local_high scored 87% overall" answers no question anyone is
asking — the easy bucket is two thirds of the sample and every tier is expected
to ace it, so an overall average mostly measures how easy the easy bucket was.
Everything here is therefore keyed by `(bucket, model_tier)` first, with the
per-tier roll-up kept as a secondary table and the hard-bucket gap promoted to
its own section in both renderers.

What this module computes, per `(bucket, tier)` group and per tier overall:

- **Schema validity rate** — the fraction of calls whose `raw_response` parsed
  as a valid `CardMetadataOutput`. Per the *Scoring* section this is the one
  quality number that needs no ground truth: a model that ignores the closed
  vocabulary has failed regardless of how good its judgment otherwise is. The
  tiers ran code-tuned Qwen models through S-001 and general instruction-tuned
  ones after it, so this rate is the specific thing the benchmark was told to
  confirm rather than assume for either line.
- **Latency** — mean, p50, p95 in ms. For the local tiers there is no bill, so
  this *is* their cost axis.
- **Cost** — total and per-run USD. Zero for local tiers by construction.
- **Token use** — mean input/output tokens, the thing that predicts cost for a
  frontier production run over ~31,830 cards.
- **Review coverage and the human-verdict distribution** — good / acceptable /
  wrong / other, plus how many rows nobody has looked at yet. Scoring is human
  review, so a table of metrics over a half-reviewed set is a trap unless it
  says out loud how much of it is reviewed.

## What this module deliberately does NOT compute by default

The *Scoring* section also calls for role/theme/synergy F1, `game_stage` MAE and
`power_rating` MAE. **Those require the hand-labeled ground truth, which does not
exist yet and is not in `benchmark_runs`.** There is no column in that table that
could stand in for it, and the document is explicit that model output "is never
scored against another model's output" — so scoring the local tiers against the
frontier tier's parsed output would be worse than reporting nothing: it would
produce a plausible-looking F1 that silently measures agreement with Claude
instead of correctness, which is precisely the mistake the benchmark exists to
avoid.

So accuracy is a *plug-in*, not a default. `build_report(...)` takes an optional
`ground_truth: Mapping[str, dict] | None` — oracle_id to a hand-labeled
`CardMetadataBlueprint`-shaped dict. Pass it and `score_accuracy` fills the
accuracy block in; omit it (today, always) and every accuracy figure reports as
*unavailable*, rendered as such in both the text and HTML output. Unavailable is
never rendered as `0.0`, because a zero F1 and an absent F1 would drive opposite
decisions.

## Reading the data by hand

This module is a convenience, not a gate. `benchmark.db` is an ordinary SQLite
file with three tables, and the fastest way to answer a question it doesn't
already answer is to ask SQLite directly::

    sqlite3 backend/knowledge_pipeline/benchmark/benchmark.db
    sqlite> .headers on
    sqlite> .mode column

The per-group summary this module's primary table renders::

    SELECT bucket,
           model_tier,
           COUNT(*)                            AS runs,
           ROUND(100.0 * AVG(schema_valid), 1) AS valid_pct,
           ROUND(AVG(latency_ms))              AS mean_ms,
           ROUND(SUM(cost_usd), 4)             AS cost_usd,
           ROUND(AVG(input_tokens))            AS in_tok,
           ROUND(AVG(output_tokens))           AS out_tok
    FROM benchmark_runs
    GROUP BY bucket, model_tier
    ORDER BY bucket, model_tier;

The deciding number — how the verdicts split per tier on the hard bucket
(``COALESCE`` so unreviewed rows show up as a row instead of vanishing)::

    SELECT model_tier,
           COALESCE(human_verdict, '(unreviewed)') AS verdict,
           COUNT(*)                                AS n
    FROM benchmark_runs
    WHERE bucket = 'hard'
    GROUP BY model_tier, verdict
    ORDER BY model_tier, verdict;

Escalation-rule hunting — the cards where the likely production tier produced
something unusable, which is where a trigger signal would have to fire::

    SELECT bucket, card_name, LENGTH(user_prompt) AS prompt_chars
    FROM benchmark_runs
    WHERE model_tier = 'local_high' AND schema_valid = 0
    ORDER BY bucket, card_name;

One card, all three tiers side by side, to read the raw output the way the
*Scoring* section says a maintainer must::

    SELECT model_tier, schema_valid, human_verdict, raw_response
    FROM benchmark_runs
    WHERE card_name = 'Sol Ring'
    ORDER BY model_tier;

How one session's picks landed -- the number a session exists to produce
(remember that it means nothing unless that session's status is `complete`)::

    SELECT winner, COUNT(*) AS n
    FROM benchmark_pairs
    WHERE session_id = 'S-003'
    GROUP BY winner
    ORDER BY n DESC;

Which cells of a session's grid are missing, i.e. why it is `partial`::

    SELECT s.id, s.status,
           json_array_length(s.card_ids) * json_array_length(s.tiers) AS expected,
           COUNT(r.id)                                                AS recorded
    FROM benchmark_sessions s
    LEFT JOIN benchmark_runs r ON r.session_id = s.id
    GROUP BY s.id
    ORDER BY s.started_at;

The review backlog, since every aggregate above is only as trustworthy as its
coverage::

    SELECT bucket,
           model_tier,
           SUM(human_verdict IS NULL) AS unreviewed,
           COUNT(*)                   AS runs
    FROM benchmark_runs
    GROUP BY bucket, model_tier;

And an export, when the question really does want a spreadsheet::

    sqlite3 -header -csv benchmark.db \\
      "SELECT card_name, bucket, model_tier, schema_valid, latency_ms, cost_usd,
              human_verdict FROM benchmark_runs" > runs.csv

## Layering

`build_report` and everything under it operate on a plain sequence of mappings,
not on a database handle: `sqlite3.Row` supports `row["column"]`, and so does a
dict, so tests and any future export path feed the same code the store does. The
`store` import lives inside `load_store`/`main` rather than at module scope for
that reason — the aggregation layer has no opinion about where rows came from,
and nothing in it should break if the store grows a second backend.

CLI::

    python -m knowledge_pipeline.benchmark.report                      # text summary
    python -m knowledge_pipeline.benchmark.report --html report.html   # + tabbed page
    python -m knowledge_pipeline.benchmark.report --session S-003      # one session
    python -m knowledge_pipeline.benchmark.report --bucket hard --tier local_high

The package's own `__main__.py` is owned elsewhere; a `report` subcommand could
be wired into it later, but this module stands alone in the meantime.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Deliberately the package-root module and not anything under
# `metadata_generator/`, whose `__init__` would pull pydantic, the Anthropic
# SDK and a session factory in behind it. See that module's docstring.
from ..cost_profile import facts_from_prompt, has_cost_exception

# One row of `benchmark_runs`. `sqlite3.Row` is not a `Mapping` subclass, but it
# supports the only operation anything here performs on a row — `row["name"]` —
# so the alias is documentation of the contract rather than an enforced type.
Run = Mapping[str, Any]

# Difficulty buckets and model tiers in the order the benchmark document lists
# them: easy -> hard is a difficulty ramp, and local_low -> frontier is a
# capability ramp. Sorting either alphabetically would scramble both and make
# the one comparison that matters (the rightward drift across a bucket's row)
# unreadable. Values outside these tuples are still reported; they sort after,
# alphabetically, so an unrecognised tier is visible rather than dropped.
BUCKET_ORDER: tuple[str, ...] = ("easy", "medium", "hard")
TIER_ORDER: tuple[str, ...] = ("local_low", "local_high", "frontier")

# The verdict vocabulary is "e.g." in the spec, not a closed enum, so these are
# the expected values and anything else lands in `other` rather than being
# discarded or crashing the report.
VERDICT_ORDER: tuple[str, ...] = ("good", "acceptable", "wrong")

TIER_LABELS: Mapping[str, str] = {
    "local_low": "local 7B",
    "local_high": "local 14B",
    "frontier": "frontier",
}

# `store.seed_demo` tags synthetic rows by prefixing `model_name`, and the store
# justifies that choice by claiming the prefix is "self-describing in every
# report the reporting layer prints". It was not: every table here labels a
# group by its *tier* (`TIER_LABELS`), so `demo/qwen2.5-coder-7b` rendered as
# plain "local 7B" and a reader had no way to tell fabricated latencies from
# measured ones. Honouring that claim is what `SYNTHETIC_MARKER` below is for.
#
# Duplicated from `store.DEMO_MODEL_PREFIX` rather than imported, because the
# module docstring's layering rule keeps `store` out of this module's imports
# so aggregation stays usable on any sequence of mappings. A test asserts the
# two stay equal, which is the half of an import that actually matters here.
SYNTHETIC_MODEL_PREFIX = "demo/"

# Appended to a tier label whose rows are synthetic. Wide and shouty on
# purpose: this marker's whole job is to be impossible to skim past.
SYNTHETIC_MARKER = " [DEMO]"

# The tier every other tier is measured against. The benchmark's whole question
# is "how far behind frontier is local, on hard cards" — so frontier is the
# baseline, not the best performer or the first column.
BASELINE_TIER = "frontier"


# --------------------------------------------------------------------------
# Small numeric helpers
#
# Every one of these returns `None` rather than 0 for an empty input. An empty
# group is a real state here — a bucket/tier cell with no runs exists as soon as
# a benchmark is run partway, and the report has to render it as "no data" and
# not as "0 ms, 0% valid, perfect and free".
# --------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(numerator: int, denominator: int) -> float | None:
    """A 0-1 rate, or None when there is nothing to take a rate of."""
    if denominator <= 0:
        return None
    return numerator / denominator


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear-interpolated percentile, matching the usual `numpy.percentile`.

    Written out rather than pulled from `statistics.quantiles`, which cuts a
    distribution into n equal groups and needs at least two data points — a
    benchmark group can legitimately hold one run, and "p95 of a single call"
    should be that call's latency, not an exception.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def _num(row: Run, key: str) -> float | None:
    """Read a numeric column, tolerating NULL.

    `cost_usd` is 0 for local tiers by design, but `input_tokens`,
    `output_tokens` and even `latency_ms` can be NULL for a call that failed
    before the model answered — and a NULL must not be averaged in as a zero.
    """
    value = row[key]
    if value is None:
        return None
    return float(value)


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictBreakdown:
    """The human side of a group: how many runs were read, and how they landed.

    `good_rate` is deliberately a share of *reviewed* runs, not of all runs. A
    tier whose 10 reviewed runs were all good is at 100% here, with
    `review_coverage` at 0.1 alongside to say how much weight that deserves.
    Dividing by total runs instead would blend "not good" and "not looked at
    yet" into one number, and those are opposite signals.
    """

    counts: Mapping[str, int]
    other: int
    unreviewed: int
    total: int

    @property
    def reviewed(self) -> int:
        return self.total - self.unreviewed

    @property
    def review_coverage(self) -> float | None:
        return _rate(self.reviewed, self.total)

    @property
    def good_rate(self) -> float | None:
        return _rate(self.counts.get("good", 0), self.reviewed)

    @property
    def wrong_rate(self) -> float | None:
        return _rate(self.counts.get("wrong", 0), self.reviewed)


@dataclass(frozen=True)
class AccuracyScores:
    """Ground-truth-scored accuracy for one group.

    Only ever constructed when a caller supplies hand labels. `scored` and
    `unscorable` are part of the result on purpose: an F1 computed over 40 of a
    group's 100 runs (because the rest failed schema validation, or aren't in
    the label set) means something different from one computed over all 100, and
    the renderers print both.
    """

    role_f1: float | None
    theme_f1: float | None
    synergy_f1: float | None
    game_stage_mae: float | None
    power_rating_mae: float | None
    scored: int
    unscorable: int


@dataclass(frozen=True)
class TimingScores:
    """`game_stage` calibration, scored without ground truth.

    The two metrics here are the only ones in this module that say whether
    `game_stage` is any good, and they cost no human labelling at all -- which
    is what makes them worth having while the hand-labelled truth set is still
    missing (`docs/benchmarking-and-testing.md`).

    **`castability_corr` is computed over plain-cost cards only.** For 17% of
    the corpus mana value is not the cost to function
    (`metadata_generator/cost_profile.py`), and on those cards a weak
    correlation is the *correct* answer -- Convoke really does make a five-drop
    castable early. Pooling them in would mark right answers wrong. Measured on
    S-001 the scoping matters in the other direction too: the figure is -0.13
    over all rows and -0.03 over plain-cost rows alone, so the exceptions were
    masking how flat the plain cards are.
    """

    plain_runs: int
    exception_runs: int
    castability_corr: float | None
    early_mean: float | None
    shapes: Mapping[str, int]
    exception_cards: tuple[str, ...]

    @property
    def scored(self) -> int:
        return self.plain_runs

    @property
    def shaped(self) -> int:
        return sum(self.shapes.values())

    def shape_rate(self, shape: str) -> float | None:
        return _rate(self.shapes.get(shape, 0), self.shaped)


SHAPE_ORDER: tuple[str, ...] = ("rising", "falling", "peaked", "dipped", "flat")
"""`game_stage` curve shapes, in the order the renderers list them.

Shape is tracked because the measured bias is in the *curve*, not in any one
axis: on S-001 half of all profiles were non-decreasing, a sixth fell, and
exactly one of sixty was flat -- while `docs/data-model.md`'s own worked
example (Cultivate, 9/4/1) is a declining curve.
"""


@dataclass(frozen=True)
class GroupStats:
    """Every metric for one slice of the run log.

    A slice is either a `(bucket, tier)` cell, a whole tier (`bucket is None`),
    or the whole benchmark (both None). One dataclass for all three so the
    renderers have exactly one row-formatting path.
    """

    bucket: str | None
    model_tier: str | None
    runs: int
    cards: int
    model_names: tuple[str, ...]
    schema_valid: int
    latency_mean: float | None
    latency_p50: float | None
    latency_p95: float | None
    cost_total: float
    cost_mean: float | None
    input_tokens_mean: float | None
    output_tokens_mean: float | None
    verdicts: VerdictBreakdown
    accuracy: AccuracyScores | None
    timing: TimingScores | None = None

    @property
    def schema_valid_rate(self) -> float | None:
        return _rate(self.schema_valid, self.runs)

    @property
    def is_empty(self) -> bool:
        return self.runs == 0

    @property
    def synthetic_model_names(self) -> tuple[str, ...]:
        """The `demo/`-prefixed models in this group, if any."""
        return tuple(
            name for name in self.model_names if name.startswith(SYNTHETIC_MODEL_PREFIX)
        )

    @property
    def has_synthetic(self) -> bool:
        """Whether any run here came from `seed_demo` rather than a real model.

        Every metric on a group that answers `True` is fabricated, and the
        renderers must say so. `model_names` was already collected for exactly
        this kind of question; it simply went unasked.
        """
        return bool(self.synthetic_model_names)

    @property
    def is_mixed(self) -> bool:
        """Synthetic *and* measured runs in one group -- the worst case.

        `store.seed_demo` and `runner._refuse_demo_rows` both refuse to create
        this state, so reaching it means rows were written by something that
        bypassed both. It cannot be rendered honestly as either kind, so the
        renderers flag the group itself as untrustworthy rather than labelling
        it synthetic and implying the measured rows are fake too.
        """
        return self.has_synthetic and len(self.synthetic_model_names) < len(
            self.model_names
        )


@dataclass(frozen=True)
class TierGap:
    """One tier measured against the baseline tier, inside one bucket.

    This is the report's thesis object. Deltas are *tier minus baseline*, so a
    negative `schema_valid_delta` means the local tier is behind frontier, which
    is the direction the benchmark is looking for. `latency_ratio` is a ratio
    rather than a delta because "3x slower" survives a change of hardware in a
    way "+1,800 ms" does not.
    """

    bucket: str
    model_tier: str
    baseline_tier: str
    schema_valid_delta: float | None
    good_rate_delta: float | None
    latency_p50: float | None
    latency_ratio: float | None
    cost_mean: float | None
    cost_saved_per_run: float | None


@dataclass(frozen=True)
class PassStats:
    """One (tier, pass) group's call-level facts. Never merged across passes.

    The numbers here answer "how fast is this tier, and at what?", which is the
    one question the merged view cannot answer. Prefill and decode are reported
    separately for the reason `GenerationAttempt` spells out — prefill is
    compute-bound over the whole prompt, decode is bandwidth-bound one token at
    a time, and this project once shipped a wrong conclusion by blending them
    (`docs/lessons-learned.md`).

    The split matters more now than it did, because the two passes differ in
    exactly the dimension that confuses a blended rate: pass one prefills the
    taxonomy to generate seven fields, pass two prefills a short rubric plus a
    few ladders to generate two.
    """

    model_tier: str
    pass_name: str
    calls: int
    schema_valid_rate: float | None
    latency_p50: float | None
    input_tokens_mean: float | None
    output_tokens_mean: float | None
    decode_tokens_per_second: float | None
    prefill_tokens_per_second: float | None


def summarize_passes(rows: Iterable[Run]) -> dict[tuple[str, str], PassStats]:
    """Per-(tier, pass) call statistics, from the *unmerged* rows.

    Takes `store.iter_runs`' output directly. Rates are computed from the
    phase columns rather than from `latency_ms`, so model load and prefill
    cannot leak into the decode figure; a row whose backend reported no phase
    split (all three zero) contributes to the counts and to nothing else,
    because a zero there means "unknown" and not "instant".
    """
    grouped: dict[tuple[str, str], list[Run]] = {}
    for row in rows:
        grouped.setdefault(
            (str(row["model_tier"]), str(row["pass_name"])), []
        ).append(row)

    stats: dict[tuple[str, str], PassStats] = {}
    for (tier, pass_name), group in grouped.items():
        decode_tokens = sum(
            (row["output_tokens"] or 0) for row in group if (row["eval_ms"] or 0) > 0
        )
        decode_ms = sum(row["eval_ms"] or 0 for row in group)
        prefill_tokens = sum(
            (row["input_tokens"] or 0)
            for row in group
            if (row["prompt_eval_ms"] or 0) > 0
        )
        prefill_ms = sum(row["prompt_eval_ms"] or 0 for row in group)

        stats[(tier, pass_name)] = PassStats(
            model_tier=tier,
            pass_name=pass_name,
            calls=len(group),
            schema_valid_rate=(
                sum(1 for row in group if row["schema_valid"]) / len(group)
                if group
                else None
            ),
            latency_p50=_percentile(
                [float(row["latency_ms"] or 0) for row in group], 0.5
            ),
            input_tokens_mean=_mean([float(row["input_tokens"] or 0) for row in group]),
            output_tokens_mean=_mean(
                [float(row["output_tokens"] or 0) for row in group]
            ),
            decode_tokens_per_second=(
                decode_tokens / (decode_ms / 1000) if decode_ms > 0 else None
            ),
            prefill_tokens_per_second=(
                prefill_tokens / (prefill_ms / 1000) if prefill_ms > 0 else None
            ),
        )
    return stats


@dataclass(frozen=True)
class BenchmarkReport:
    """The fully aggregated benchmark, ready to render.

    Holds `runs` as well as the aggregates because the HTML report includes
    every individual run with its prompts and raw response — the *Scoring*
    section's whole premise is that a maintainer reads each output, so an
    aggregate-only artifact would drop the thing the benchmark is actually for.
    At ~900 rows that is a few MB of HTML, which is fine for a local file.

    `runs` is one row per (card, tier): production's two calls merged, which is
    the unit every view here reasons about. `raw_runs` is the store's own shape,
    one row per (card, tier, pass), kept because throughput must not be read
    off the merge — a pass-one call and a pass-two call have different prompt
    sizes and different answer lengths, and a blended rate describes neither.
    """

    runs: tuple[Run, ...]
    buckets: tuple[str, ...]
    tiers: tuple[str, ...]
    by_bucket_tier: Mapping[tuple[str, str], GroupStats]
    by_tier: Mapping[str, GroupStats]
    overall: GroupStats
    ground_truth_available: bool
    raw_runs: tuple[Run, ...] = ()
    per_pass: Mapping[tuple[str, str], "PassStats"] = field(default_factory=dict)
    sessions: tuple["SessionView", ...] = ()
    db_path: Path | None = None
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    @property
    def total_runs(self) -> int:
        return self.overall.runs

    def session(self, session_id: str) -> "SessionView | None":
        for view in self.sessions:
            if view.session_id == session_id:
                return view
        return None

    @property
    def prompt_fingerprints(self) -> tuple[str, ...]:
        """Every distinct system-prompt SHA-256 across the sessions shown.

        More than one means the cross-session tables are pooling runs made
        against *different prompts* — different anchors, rubric or taxonomy —
        and every pooled number is then a comparison of two things that were
        not asked the same question. The renderers say so rather than letting
        the pooling look clean, because the prompt is a ~48KB blob nobody
        diffs by eye and the fingerprint is the only way to notice.
        """
        return tuple(sorted({view.prompt_sha256 for view in self.sessions}))

    @property
    def schema_fingerprints(self) -> tuple[str, ...]:
        """Every distinct output-schema SHA-256 across the sessions shown.

        The other half of the comparability key, and the half this report once
        went without. Under constrained decoding the schema *is* the grammar,
        so two sessions can share a prompt byte for byte and still not be
        poolable — which is not hypothetical: S-002 and S-003 of the previous
        store differed only here, and the difference decided whether either
        tier emitted `roles` at all (`docs/lessons-learned.md`).
        """
        return tuple(sorted({view.schemas_sha256 for view in self.sessions}))

    @property
    def pools_mixed_prompts(self) -> bool:
        return len(self.prompt_fingerprints) > 1

    @property
    def synthetic_runs(self) -> int:
        """How many rows came from `seed_demo` rather than a model call."""
        return sum(
            1
            for row in self.runs
            if str(row["model_name"] or "").startswith(SYNTHETIC_MODEL_PREFIX)
        )

    @property
    def has_synthetic(self) -> bool:
        return self.overall.has_synthetic

    @property
    def is_fully_synthetic(self) -> bool:
        """Every row is fabricated -- the state a freshly seeded store is in."""
        return self.total_runs > 0 and self.synthetic_runs == self.total_runs

    def bucket_view(self, bucket: str) -> list[GroupStats]:
        """The tiers of one bucket, side by side, in capability order.

        Returns an entry for every tier present anywhere in the report — not
        just the ones with runs in this bucket — so the comparison grid stays
        rectangular and a tier that was never run against the hard bucket shows
        up as a visible gap instead of silently missing column.
        """
        return [self.by_bucket_tier[(bucket, tier)] for tier in self.tiers]

    def gaps(self, bucket: str, *, baseline_tier: str = BASELINE_TIER) -> list[TierGap]:
        """Every non-baseline tier's distance from the baseline, in one bucket.

        Empty when the baseline tier has no runs in this bucket: there is
        nothing to measure against, and inventing a zero baseline would make a
        missing frontier run look like a local tier winning.
        """
        base = self.by_bucket_tier.get((bucket, baseline_tier))
        if base is None or base.is_empty:
            return []

        gaps: list[TierGap] = []
        for tier in self.tiers:
            if tier == baseline_tier:
                continue
            stats = self.by_bucket_tier[(bucket, tier)]
            if stats.is_empty:
                continue
            gaps.append(
                TierGap(
                    bucket=bucket,
                    model_tier=tier,
                    baseline_tier=baseline_tier,
                    schema_valid_delta=_delta(
                        stats.schema_valid_rate, base.schema_valid_rate
                    ),
                    good_rate_delta=_delta(
                        stats.verdicts.good_rate, base.verdicts.good_rate
                    ),
                    latency_p50=stats.latency_p50,
                    latency_ratio=_ratio(stats.latency_p50, base.latency_p50),
                    cost_mean=stats.cost_mean,
                    cost_saved_per_run=_delta(base.cost_mean, stats.cost_mean),
                )
            )
        return gaps


def _delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return value - baseline


def _ratio(value: float | None, baseline: float | None) -> float | None:
    if value is None or not baseline:
        return None
    return value / baseline


# --------------------------------------------------------------------------
# Sessions: the paired view
#
# A session is one invocation of `run` — a frozen card set put through every
# tier in turn (`store.py`). It is the only scope in which "which model is
# better" is a well-posed question, because it is the only scope that
# guarantees the tiers answered the same cards under the same prompt. The
# cross-session tables further down pool sessions and say so; these types are
# what the per-session tabs render.
# --------------------------------------------------------------------------

# How many Commander-legal cards a production run would have to cover.
# 31,575 eligible in `sample.json`'s corpus block plus the 255 anchor rungs it
# excludes. Used only to turn a measured per-card latency into the wall-clock
# figure that the win rate has to be weighed against — a model that wins by a
# nose and costs nine extra days is not obviously the right pick.
CORPUS_CARD_COUNT = 31_830

# Multi-label fields compared tier-against-tier for the agreement score, and
# the numeric ones. Agreement is NOT accuracy: it says the models said the same
# thing, not that either was right. Its job is to order a review queue.
_AGREEMENT_SET_FIELDS: tuple[str, ...] = ("roles", "themes", "synergy_tags")
_GAME_STAGES: tuple[str, ...] = ("early", "mid", "late")


def _jaccard(left: Iterable[Any], right: Iterable[Any]) -> float | None:
    """Intersection over union of two label sets, or None if both are empty.

    None rather than 1.0 for two empty sets: "neither model assigned a theme"
    is not the models agreeing about themes, and feeding it in as perfect
    agreement would quietly pull a card down the review queue.
    """
    first = {str(value) for value in left or ()}
    second = {str(value) for value in right or ()}
    union = first | second
    if not union:
        return None
    return len(first & second) / len(union)


# The keys a merged row carries from each pass. Everything else is either
# identical across the two (card, tier, bucket) or summed.
_PASS_ORDER: tuple[str, ...] = ("one", "two")

# Columns that are wall clock or counts, and are therefore summed across the
# two passes. `latency_ms` is the card's total cost; the *phase* split is
# summed too, which is sound only because both passes are the same model on the
# same machine moments apart -- and which is exactly why the per-pass view
# exists beside this one rather than instead of it.
_SUMMED_COLUMNS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "load_ms",
    "prompt_eval_ms",
    "eval_ms",
    "cost_usd",
)


def merge_passes(rows: Iterable[Run]) -> tuple[Run, ...]:
    """Collapse production's two calls per card into one row per (card, tier).

    Every card-level view in this module — the side-by-side, the agreement
    scores, `power_rating` and `game_stage` calibration, the pairwise picks —
    asks a question about a card's *finished answer*, and a finished answer is
    both passes: pass one holds the tags and `game_stage`, pass two holds
    `power_rating` and `summary`. Fed the raw rows, every one of those views
    would see two half-answers per card and silently double-count each tier.

    So the raw rows stay the store's shape and this is the view that card-level
    readers take. What is *not* merged is throughput: `render_per_pass_table`
    reads the raw rows, because a pass-one call prefills ~2,000 tokens of
    taxonomy to generate seven fields while a pass-two call prefills a short
    rubric to generate two, and one blended rate describes neither.

    Merge rules, and each is a judgment rather than an obvious default:

    * `parsed_output` is the two parses merged, pass two's keys last — which is
      `CardMetadataBlueprint.from_passes` in all but name. `None` unless *both*
      passes parsed: half a blueprint is not a card's answer, and letting it
      through would put a card with no `power_rating` into the rating
      distribution as a gap rather than as a missing card.
    * `schema_valid` is the conjunction, for the same reason.
    * The timing and token columns are summed, so a card's cost is its real
      cost.
    * `raw_response` and `user_prompt` are concatenated under labelled headers,
      because the HTML shows them and a reviewer reading a rating needs to see
      the ladders it was rated against — which live in pass two's user message.
    * `id` is pass two's row id where there is one, so a `review` or
      `pair-verdict` id read off the page points at the row carrying the
      rating. Falls back to pass one's for a card whose rating call never
      landed.
    * `human_verdict` takes the first verdict found, pass one first: the
      per-run verdicts stay per *run* in the store, and a merged row is not
      where two of them get reconciled.

    A card with only one pass recorded still produces a row — it is a real
    state (an interrupted session, or a pass one that failed its grammar) and
    hiding it would make a partial session look complete.
    """
    grouped: dict[tuple[str, str, str], dict[str, Run]] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows:
        key = (
            str(row["session_id"]),
            str(row["card_oracle_id"]),
            str(row["model_tier"]),
        )
        if key not in grouped:
            grouped[key] = {}
            order.append(key)
        grouped[key][str(row["pass_name"])] = row

    merged: list[Run] = []
    for key in order:
        passes = grouped[key]
        # Known passes in generation order, then anything unrecognized in the
        # order it arrived. A reporting layer must not crash on a store written
        # by a newer schema with a third pass in it; it should show what it can
        # and leave the rest legible.
        present = [name for name in _PASS_ORDER if name in passes]
        present += [name for name in passes if name not in _PASS_ORDER]
        anchor = passes[present[-1]]

        combined: dict[str, Any] = dict(anchor)
        for column in _SUMMED_COLUMNS:
            combined[column] = sum(
                (passes[name][column] or 0) for name in present
            )

        parses = {name: _parsed(passes[name]) for name in present}
        known = [name for name in present if name in _PASS_ORDER]
        complete = len(known) == len(_PASS_ORDER) and all(
            parses[name] is not None for name in known
        )
        if complete:
            blueprint: dict[str, Any] = {}
            for name in present:
                blueprint.update(parses[name] or {})
            # Re-encoded rather than handed over as a dict: `iter_runs`'
            # contract is that `parsed_output` is JSON TEXT, every reader here
            # decodes it for itself, and the HTML prints it verbatim. A merged
            # row that broke that would be the one row in the report whose type
            # differs from every other.
            combined["parsed_output"] = json.dumps(blueprint, sort_keys=True)
            combined["schema_valid"] = 1
        else:
            combined["parsed_output"] = None
            combined["schema_valid"] = 0

        combined["raw_response"] = "\n\n".join(
            f"--- pass {name} ---\n{passes[name]['raw_response']}"
            for name in present
        )
        combined["user_prompt"] = "\n\n".join(
            f"--- pass {name} user message ---\n{passes[name]['user_prompt']}"
            for name in present
        )
        combined["system_prompt"] = "\n\n".join(
            f"--- pass {name} system prompt ---\n{passes[name]['system_prompt']}"
            for name in present
        )
        combined["pass_name"] = "+".join(present)
        combined["passes_recorded"] = len(present)

        for name in present:
            if passes[name]["human_verdict"] is not None:
                combined["human_verdict"] = passes[name]["human_verdict"]
                combined["human_notes"] = passes[name]["human_notes"]
                combined["reviewed_at"] = passes[name]["reviewed_at"]
                break

        merged.append(combined)

    return tuple(merged)


def _parsed(row: Run) -> dict[str, Any] | None:
    """A run's `parsed_output`, decoded. `None` when it never parsed.

    The store hands back raw JSON TEXT by design (`iter_runs`), so every reader
    decodes for itself; this is that decode, plus tolerance for a row whose
    JSON is unreadable, which would otherwise take down a whole report over
    one bad cell.
    """
    raw = row["parsed_output"]
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class AgreementScores:
    """How closely two tiers' answers to one card line up.

    Deliberately not called "accuracy". With no ground truth, two models
    agreeing says only that; both can be wrong together, and on a hard card
    they often are. What this is *for* is ordering the review queue: on a
    30-card session, reading the eight cards where the tiers disagree is worth
    more than reading all 30 in alphabetical order, and tier disagreement is
    itself one of the candidate escalation signals the benchmark doc asks this
    to test.
    """

    left_tier: str
    right_tier: str
    set_jaccard: Mapping[str, float | None]
    power_rating_delta: float | None
    game_stage_delta: float | None

    @property
    def score(self) -> float | None:
        """One 0-1 agreement number, or None when nothing was comparable.

        The set fields average into it directly. The two numeric fields enter
        as `1 - delta/9`, i.e. a full-scale disagreement on a 1-10 rating
        counts as total disagreement — crude, deliberately, because this
        number's only job is to sort rows.
        """
        parts = [value for value in self.set_jaccard.values() if value is not None]
        for delta in (self.power_rating_delta, self.game_stage_delta):
            if delta is not None:
                parts.append(max(0.0, 1.0 - (delta / 9.0)))
        if not parts:
            return None
        return sum(parts) / len(parts)

    @property
    def disagreement(self) -> float:
        """Sort key for the review queue. Unknown sorts as maximal.

        A card where one tier produced nothing parseable has no agreement
        score, and it is the *most* interesting row on the page — so it sorts
        first rather than last.
        """
        score = self.score
        return 1.0 if score is None else 1.0 - score


def compare_runs(left: Run, right: Run) -> AgreementScores | None:
    """Field-by-field agreement between two tiers' answers to one card.

    Returns `None` if the two runs are from the same tier, which would make the
    comparison meaningless rather than merely empty.
    """
    left_tier = str(left["model_tier"])
    right_tier = str(right["model_tier"])
    if left_tier == right_tier:
        return None

    left_parsed = _parsed(left)
    right_parsed = _parsed(right)

    if left_parsed is None or right_parsed is None:
        # One tier produced nothing parseable, so there is nothing to compare.
        # Every field reports unknown rather than 0% agreement: an empty answer
        # against a populated one is not a *measured* disagreement, and scoring
        # it as one would put a number on a comparison that never happened.
        # `disagreement` still treats unknown as maximal, so the card keeps its
        # place at the top of the review queue, which is where it belongs.
        return AgreementScores(
            left_tier=left_tier,
            right_tier=right_tier,
            set_jaccard=dict.fromkeys(_AGREEMENT_SET_FIELDS, None),
            power_rating_delta=None,
            game_stage_delta=None,
        )

    set_scores = {
        field: _jaccard(left_parsed.get(field) or (), right_parsed.get(field) or ())
        for field in _AGREEMENT_SET_FIELDS
    }

    power_delta: float | None = None
    left_power, right_power = left_parsed.get("power_rating"), right_parsed.get(
        "power_rating"
    )
    if isinstance(left_power, (int, float)) and isinstance(right_power, (int, float)):
        power_delta = abs(float(left_power) - float(right_power))

    stage_delta: float | None = None
    left_stage = left_parsed.get("game_stage") or {}
    right_stage = right_parsed.get("game_stage") or {}
    if isinstance(left_stage, Mapping) and isinstance(right_stage, Mapping):
        deltas = [
            abs(float(left_stage[stage]) - float(right_stage[stage]))
            for stage in _GAME_STAGES
            if isinstance(left_stage.get(stage), (int, float))
            and isinstance(right_stage.get(stage), (int, float))
        ]
        if deltas:
            stage_delta = sum(deltas) / len(deltas)

    return AgreementScores(
        left_tier=left_tier,
        right_tier=right_tier,
        set_jaccard=set_scores,
        power_rating_delta=power_delta,
        game_stage_delta=stage_delta,
    )


@dataclass(frozen=True)
class CardComparison:
    """One card of a session, as every tier answered it.

    `runs` is keyed by tier and may be missing one: that is a cell of the
    session's grid with no row, and the renderers mark it rather than quietly
    comparing the tiers that did answer. `winner` is the recorded pick from
    `benchmark_pairs`, or `None` while the card is still undecided.
    """

    oracle_id: str
    name: str
    bucket: str
    runs: Mapping[str, Run]
    tiers: tuple[str, ...]
    winner: str | None = None
    pair_notes: str | None = None
    agreement: AgreementScores | None = None

    @property
    def complete(self) -> bool:
        """Whether every tier of the session answered this card."""
        return all(tier in self.runs for tier in self.tiers)

    @property
    def missing_tiers(self) -> tuple[str, ...]:
        return tuple(tier for tier in self.tiers if tier not in self.runs)

    @property
    def decided(self) -> bool:
        return self.winner is not None

    @property
    def disagreement(self) -> float:
        """The review-queue sort key. An incomplete card sorts first of all."""
        if not self.complete:
            return 2.0
        return self.agreement.disagreement if self.agreement else 1.0


@dataclass(frozen=True)
class WinTally:
    """How a session's decided cards split between its tiers.

    `interval` is a 95% Wilson score interval on the leading tier's share, and
    it is not decoration. A 30-card session that splits 17-13 looks like a
    winner and is noise: the interval on 17/30 runs roughly 39%-74%, which
    includes "the other model is better". Reporting a bare percentage here
    would be the single easiest way to read a conclusion out of this benchmark
    that the benchmark does not support.
    """

    counts: Mapping[str, int]
    decided: int
    undecided: int

    @property
    def tally(self) -> str:
        """`17-13-0` style, in the order the session ran its tiers."""
        return "-".join(str(count) for count in self.counts.values())

    def rate(self, tier: str) -> float | None:
        return _rate(self.counts.get(tier, 0), self.decided)

    def interval(self, tier: str) -> tuple[float, float] | None:
        return wilson_interval(self.counts.get(tier, 0), self.decided)


def wilson_interval(
    successes: int, total: int, *, z: float = 1.96
) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion. `None` when `total` is 0.

    Wilson rather than the textbook normal approximation because the normal one
    is wrong in exactly this regime — small n, proportions near 0 or 1 — and
    can produce bounds outside [0, 1], which would read as a bug rather than as
    the caveat it is meant to be.
    """
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
        / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass(frozen=True)
class SessionView:
    """One session, fully aggregated: its provenance, cards and per-tier stats.

    This is what a tab in the HTML report is. Everything a reader needs to
    decide whether the session's numbers mean anything lives on it — the frozen
    card count, the status, the prompt fingerprint, which models answered —
    because a tab that showed only results would invite exactly the comparison
    across incompatible sessions that sessions were introduced to prevent.
    """

    session_id: str
    label: str | None
    status: str
    started_at: str
    finished_at: str | None
    buckets: tuple[str, ...]
    cards_per_bucket: int
    tiers: tuple[str, ...]
    models: Mapping[str, str]
    num_ctx: int
    temperature: float
    model_seed: int | None
    draw_seed: int
    sample_path: str | None
    sample_seed: int | None
    prompt_sha256: str
    schemas_sha256: str
    notes: str | None
    cards: tuple[CardComparison, ...]
    by_tier: Mapping[str, GroupStats]
    runs: tuple[Run, ...]
    expected_cards: int

    @property
    def expected_calls(self) -> int:
        return self.expected_cards * len(self.tiers)

    @property
    def recorded_calls(self) -> int:
        return len(self.runs)

    @property
    def missing_cells(self) -> int:
        return max(0, self.expected_calls - self.recorded_calls)

    @property
    def complete_cards(self) -> tuple[CardComparison, ...]:
        """Only the cards every tier answered — the comparable subset."""
        return tuple(card for card in self.cards if card.complete)

    @property
    def is_paired(self) -> bool:
        """Whether a win rate over this session compares like with like."""
        return self.missing_cells == 0 and len(self.tiers) > 1

    @property
    def is_synthetic(self) -> bool:
        return any(
            str(model).startswith(SYNTHETIC_MODEL_PREFIX) for model in self.models.values()
        )

    @property
    def wins(self) -> WinTally:
        counts: dict[str, int] = {tier: 0 for tier in self.tiers}
        for outcome in VERDICT_NO_WINNER:
            counts[outcome] = 0
        decided = 0
        for card in self.cards:
            if card.winner is None:
                continue
            decided += 1
            counts[card.winner] = counts.get(card.winner, 0) + 1
        return WinTally(
            counts=counts, decided=decided, undecided=len(self.cards) - decided
        )

    @property
    def disagreement_order(self) -> tuple[CardComparison, ...]:
        """Cards worst-agreement first — the order to review them in."""
        return tuple(
            sorted(self.cards, key=lambda card: (-card.disagreement, card.name))
        )

    def projected_corpus_hours(self, tier: str) -> float | None:
        """Wall clock to run the whole corpus at this tier's measured p50.

        The other half of every verdict this page renders. The 14B winning a
        session is a finding; the 14B winning a session and costing nine extra
        days of inference for ~31,830 cards is a decision, and a page that
        showed only the first would be arguing one side of it.
        """
        stats = self.by_tier.get(tier)
        if stats is None or stats.latency_p50 is None:
            return None
        return stats.latency_p50 * CORPUS_CARD_COUNT / 1000 / 3600


# Pair outcomes that are not a tier. Duplicated from `store.NO_WINNER` for the
# same layering reason `SYNTHETIC_MODEL_PREFIX` is duplicated: nothing here
# imports the store. A test asserts the two stay equal.
VERDICT_NO_WINNER: tuple[str, ...] = ("tie", "both_bad")


def build_session_views(
    rows: Sequence[Run],
    sessions: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    *,
    ground_truth: Mapping[str, dict] | None = None,
) -> tuple[SessionView, ...]:
    """One `SessionView` per session row, newest first.

    Takes session and pair rows as plain mappings, like everything else here,
    so the aggregation layer still never imports the store. JSON columns arrive
    as strings (that is what SQLite gives back) and are decoded here.
    """
    views: list[SessionView] = []

    for session in sessions:
        session_id = str(session["id"])
        tiers = tuple(_decode_list(session["tiers"]))
        card_ids = tuple(_decode_list(session["card_ids"]))
        session_rows = tuple(
            row for row in rows if str(row["session_id"]) == session_id
        )

        by_card: dict[str, dict[str, Run]] = {}
        for row in session_rows:
            by_card.setdefault(str(row["card_oracle_id"]), {})[
                str(row["model_tier"])
            ] = row

        decisions = {
            str(pair["card_oracle_id"]): pair
            for pair in pairs
            if str(pair["session_id"]) == session_id
        }

        comparisons: list[CardComparison] = []
        # Driven by the session's frozen card list, not by the rows: a card
        # nothing was recorded for still has to appear, as the missing cell it
        # is. Deriving the list from the rows could only ever show a complete
        # session.
        for card_id in card_ids:
            tier_runs = by_card.get(card_id, {})
            any_run = next(iter(tier_runs.values()), None)
            pair = decisions.get(card_id)

            agreement = None
            present = [tier_runs[tier] for tier in tiers if tier in tier_runs]
            if len(present) >= 2:
                agreement = compare_runs(present[0], present[1])

            comparisons.append(
                CardComparison(
                    oracle_id=card_id,
                    name=str(any_run["card_name"]) if any_run else card_id,
                    bucket=str(any_run["bucket"]) if any_run else "?",
                    runs=tier_runs,
                    tiers=tiers,
                    winner=str(pair["winner"]) if pair else None,
                    pair_notes=(pair["notes"] if pair else None),
                    agreement=agreement,
                )
            )

        views.append(
            SessionView(
                session_id=session_id,
                label=session["label"],
                status=str(session["status"]),
                started_at=str(session["started_at"]),
                finished_at=session["finished_at"],
                buckets=tuple(_decode_list(session["buckets"])),
                cards_per_bucket=int(session["cards_per_bucket"]),
                tiers=tiers,
                models=_decode_mapping(session["models"]),
                num_ctx=int(session["num_ctx"]),
                temperature=float(session["temperature"]),
                model_seed=(
                    int(session["model_seed"])
                    if session["model_seed"] is not None
                    else None
                ),
                draw_seed=int(session["draw_seed"]),
                sample_path=session["sample_path"],
                sample_seed=session["sample_seed"],
                prompt_sha256=str(session["system_prompts_sha256"]),
                schemas_sha256=str(session["output_schemas_sha256"]),
                notes=session["notes"],
                cards=tuple(comparisons),
                by_tier={
                    tier: summarize(
                        [row for row in session_rows if row["model_tier"] == tier],
                        bucket=None,
                        model_tier=tier,
                        ground_truth=ground_truth,
                    )
                    for tier in tiers
                },
                runs=session_rows,
                expected_cards=len(card_ids),
            )
        )

    # Newest first: the session a maintainer just ran is the one they opened
    # the page for. The store returns them oldest-first (one ordering
    # convention, no presentation opinions), so the reversal happens here.
    return tuple(reversed(views))


def _decode_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _decode_mapping(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in decoded.items()} if isinstance(decoded, dict) else {}


# --------------------------------------------------------------------------
# Accuracy scoring — the plug-in that has nothing to plug into yet
# --------------------------------------------------------------------------


def _multilabel_counts(
    predicted: Iterable[Any], actual: Iterable[Any]
) -> tuple[int, int, int]:
    """True positives, false positives, false negatives for one card's labels.

    Set-based because roles/themes/synergy_tags are unordered multi-label fields
    scored as precision/recall/F1, "not exact-match" per the *Scoring* section:
    predicting Ramp + Card Draw when the truth is Ramp should score as a partial
    hit, not a miss.
    """
    predicted_set = {str(item) for item in predicted or ()}
    actual_set = {str(item) for item in actual or ()}
    true_positive = len(predicted_set & actual_set)
    return (
        true_positive,
        len(predicted_set) - true_positive,
        len(actual_set) - true_positive,
    )


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float | None:
    """Micro-averaged F1 from pooled counts.

    Micro rather than macro: pooling every card's hits and misses before the
    division weights each *label decision* equally, so a card the model tagged
    with six roles counts for more than one it tagged with a single role. Macro
    would let a one-label card that happened to match paper over six wrong
    labels elsewhere.

    Returns None when there were no predictions *and* no labels — an F1 over
    nothing is undefined, and 1.0 ("predicted every label correctly") would be
    the most misleading possible answer.
    """
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator == 0:
        return None
    return 2 * true_positive / denominator


_MANA_VALUE_LINE = re.compile(r"^Mana value: ([0-9.]+)\s*$", re.MULTILINE)


def _curve_shape(early: float, mid: float, late: float) -> str:
    """Which of `SHAPE_ORDER` this profile is. Exhaustive by construction."""
    if early == mid == late:
        return "flat"
    if early <= mid <= late:
        return "rising"
    if early >= mid >= late:
        return "falling"
    if mid >= early and mid >= late:
        return "peaked"
    return "dipped"


def _correlation(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Pearson r, or None when it is undefined rather than zero.

    Two runs on the same card give identical mana values, so a small slice can
    easily have no variance on the x axis at all. `statistics.correlation`
    raises there; reporting that as 0.0 would read as "no relationship", which
    is a measurement, when the honest answer is "not measurable".
    """
    if len(pairs) < 3:
        return None
    try:
        return statistics.correlation([x for x, _ in pairs], [y for _, y in pairs])
    except statistics.StatisticsError:
        return None


def score_timing(rows: Sequence[Run]) -> TimingScores | None:
    """Score `game_stage` against the one fact that needs no hand labels.

    A card you cannot cast until turn seven is not doing much on turn two, so
    `early` should track mana value downward. It is the rare metadata field
    with a free, objective check -- but only on cards whose mana value *is*
    their cost, which is why every row is classified first.

    Reads the card's facts back out of the stored `user_prompt` rather than
    from the corpus, so this module still reaches neither a database nor a
    model (`CLAUDE.md`). `cost_profile.facts_from_prompt` documents the one
    bounded gap in doing it that way.
    """
    plain: list[tuple[float, float]] = []
    early_values: list[float] = []
    shapes: dict[str, int] = dict.fromkeys(SHAPE_ORDER, 0)
    exceptions: set[str] = set()
    exception_runs = 0

    for row in rows:
        raw_parsed = row["parsed_output"]
        if not row["schema_valid"] or not raw_parsed:
            continue
        try:
            parsed = json.loads(raw_parsed) if isinstance(raw_parsed, str) else raw_parsed
        except (TypeError, ValueError):
            continue
        stage = parsed.get("game_stage") if isinstance(parsed, dict) else None
        if not isinstance(stage, dict):
            continue
        try:
            early = float(stage["early"])
            mid = float(stage["mid"])
            late = float(stage["late"])
        except (KeyError, TypeError, ValueError):
            continue

        shapes[_curve_shape(early, mid, late)] += 1

        prompt_text = row["user_prompt"] or ""
        if has_cost_exception(facts_from_prompt(prompt_text)):
            exception_runs += 1
            exceptions.add(str(row["card_name"]))
            continue

        early_values.append(early)
        match = _MANA_VALUE_LINE.search(prompt_text)
        if match:
            plain.append((float(match.group(1)), early))

    if not any(shapes.values()) and not plain:
        return None

    return TimingScores(
        plain_runs=len(plain),
        exception_runs=exception_runs,
        castability_corr=_correlation(plain),
        early_mean=_mean(early_values),
        shapes=shapes,
        exception_cards=tuple(sorted(exceptions)),
    )


def score_accuracy(
    rows: Sequence[Run], ground_truth: Mapping[str, dict] | None
) -> AccuracyScores | None:
    """Score parsed model output against hand labels, or return None.

    **`ground_truth` is `None` in every code path that exists today.** No
    hand-labeled set has been produced yet (`docs/benchmarking-and-testing.md`,
    *Ground truth*), `benchmark_runs` holds no column that could substitute for
    one, and scoring a local tier against the frontier tier's output is
    explicitly forbidden — it would measure agreement with Claude and read as
    accuracy. So this returns `None`, the renderers print "unavailable", and the
    metrics stay honestly absent rather than quietly zero.

    When labels do exist, pass `{oracle_id: {...}}` where each value has the
    `CardMetadataBlueprint` shape (`metadata_generator/schema.py`): `roles`,
    `themes`, `synergy_tags` as lists, `game_stage` as
    `{"early": x, "mid": y, "late": z}`, and `power_rating` as a number. Only
    rows that parsed (`schema_valid`, non-null `parsed_output`) *and* have a
    label are scored; everything else is counted in `unscorable`, because a
    schema failure is already reported as a schema failure and folding it in
    here as an F1 of zero would double-count the same defect.
    """
    if ground_truth is None:
        return None

    import json

    role = [0, 0, 0]
    theme = [0, 0, 0]
    synergy = [0, 0, 0]
    stage_errors: list[float] = []
    power_errors: list[float] = []
    scored = 0
    unscorable = 0

    for row in rows:
        truth = ground_truth.get(row["card_oracle_id"])
        raw_parsed = row["parsed_output"]
        if truth is None or not row["schema_valid"] or not raw_parsed:
            unscorable += 1
            continue

        try:
            parsed = json.loads(raw_parsed) if isinstance(raw_parsed, str) else raw_parsed
        except (TypeError, ValueError):
            # A row flagged schema_valid whose parsed_output isn't JSON is a
            # store-level inconsistency, not a model error; don't let it
            # masquerade as one.
            unscorable += 1
            continue

        scored += 1
        for accumulator, key in (
            (role, "roles"),
            (theme, "themes"),
            (synergy, "synergy_tags"),
        ):
            counts = _multilabel_counts(parsed.get(key, ()), truth.get(key, ()))
            for index, value in enumerate(counts):
                accumulator[index] += value

        predicted_stage = parsed.get("game_stage") or {}
        truth_stage = truth.get("game_stage") or {}
        # MAE "per stage" per the spec: each of early/mid/late contributes its
        # own absolute error to one pooled mean, rather than the three being
        # averaged per card first.
        for stage in ("early", "mid", "late"):
            if stage in predicted_stage and stage in truth_stage:
                stage_errors.append(
                    abs(float(predicted_stage[stage]) - float(truth_stage[stage]))
                )

        if parsed.get("power_rating") is not None and truth.get("power_rating") is not None:
            power_errors.append(
                abs(float(parsed["power_rating"]) - float(truth["power_rating"]))
            )

    return AccuracyScores(
        role_f1=_f1(*role),
        theme_f1=_f1(*theme),
        synergy_f1=_f1(*synergy),
        game_stage_mae=_mean(stage_errors),
        power_rating_mae=_mean(power_errors),
        scored=scored,
        unscorable=unscorable,
    )


# --------------------------------------------------------------------------
# Building the report
# --------------------------------------------------------------------------


def summarize(
    rows: Sequence[Run],
    *,
    bucket: str | None,
    model_tier: str | None,
    ground_truth: Mapping[str, dict] | None = None,
) -> GroupStats:
    """Aggregate one already-filtered slice of runs.

    `bucket`/`model_tier` are labels for the resulting `GroupStats`, not
    filters — the caller has already selected `rows`. Passing an empty sequence
    is supported and produces a fully-`None` group; that is the path every
    "this tier was never run against this bucket" cell takes, and the one an
    empty database takes for every cell at once.
    """
    latencies = [value for value in (_num(row, "latency_ms") for row in rows) if value is not None]
    costs = [value for value in (_num(row, "cost_usd") for row in rows) if value is not None]
    inputs = [value for value in (_num(row, "input_tokens") for row in rows) if value is not None]
    outputs = [value for value in (_num(row, "output_tokens") for row in rows) if value is not None]

    verdict_counts = dict.fromkeys(VERDICT_ORDER, 0)
    other = 0
    unreviewed = 0
    for row in rows:
        verdict = row["human_verdict"]
        if verdict is None or str(verdict).strip() == "":
            unreviewed += 1
        elif verdict in verdict_counts:
            verdict_counts[verdict] += 1
        else:
            other += 1

    return GroupStats(
        bucket=bucket,
        model_tier=model_tier,
        runs=len(rows),
        cards=len({row["card_oracle_id"] for row in rows}),
        model_names=tuple(sorted({row["model_name"] for row in rows if row["model_name"]})),
        schema_valid=sum(1 for row in rows if row["schema_valid"]),
        latency_mean=_mean(latencies),
        latency_p50=_percentile(latencies, 0.50),
        latency_p95=_percentile(latencies, 0.95),
        # `sum([])` is 0, and here that is the right answer rather than a
        # divide-by-zero dodge: a group with no runs did in fact cost nothing.
        # The *mean* still has to be None.
        cost_total=sum(costs),
        cost_mean=_mean(costs),
        input_tokens_mean=_mean(inputs),
        output_tokens_mean=_mean(outputs),
        verdicts=VerdictBreakdown(
            counts=verdict_counts, other=other, unreviewed=unreviewed, total=len(rows)
        ),
        accuracy=score_accuracy(rows, ground_truth),
        timing=score_timing(rows),
    )


def _ordered(values: Iterable[str], preferred: Sequence[str]) -> tuple[str, ...]:
    """Known values in their meaningful order, then anything unexpected, sorted."""
    present = {value for value in values if value is not None}
    known = [value for value in preferred if value in present]
    unknown = sorted(present.difference(preferred))
    return tuple(known + unknown)


def build_report(
    rows: Iterable[Run],
    *,
    raw_runs: Iterable[Run] = (),
    sessions: Sequence[Mapping[str, Any]] = (),
    pairs: Sequence[Mapping[str, Any]] = (),
    ground_truth: Mapping[str, dict] | None = None,
    db_path: Path | None = None,
) -> BenchmarkReport:
    """Aggregate raw `benchmark_runs` rows into every view the renderers need.

    Args:
        rows: One row per (card, tier) — a card's *finished* answer, which
            spans both of production's two calls. Callers reading the store
            pass `merge_passes(store.iter_runs(...))`; the merge is the
            caller's explicit step rather than something this function does to
            its input, so that every aggregate below can state its unit as "a
            card's answer" and mean it. Passing the store's unmerged rows
            straight in would double-count every tier and silently halve its
            schema-validity rate.
        raw_runs: The unmerged rows, if available, for the per-pass table —
            the one view that must not be merged, because a pass-one call and a
            pass-two call differ in exactly the dimension a blended throughput
            number hides. Optional: a report built without them simply omits
            that section.
        sessions: `benchmark_sessions` rows, which become the per-session tabs.
            Optional so aggregation stays testable on bare run fixtures; a
            report built without them renders the pooled views only.
        pairs: `benchmark_pairs` rows — the recorded per-card picks.
        ground_truth: Hand labels keyed by oracle_id, or `None`. See
            `score_accuracy`; `None` today, always.
        db_path: Recorded on the report so both renderers can name the file the
            numbers came from. A benchmark that gets re-run against a new model
            release produces a second database, and a report that doesn't say
            which one it read is a report you can't trust six months later.

    Buckets and tiers are taken from the data rather than from `BUCKET_ORDER`/
    `TIER_ORDER`, so a partially-run benchmark reports on what it has — but
    within that, the fixed orders decide the sequence.
    """
    materialized = tuple(rows)
    raw = tuple(raw_runs)
    buckets = _ordered((row["bucket"] for row in materialized), BUCKET_ORDER)
    tiers = _ordered((row["model_tier"] for row in materialized), TIER_ORDER)

    by_bucket_tier = {
        (bucket, tier): summarize(
            [
                row
                for row in materialized
                if row["bucket"] == bucket and row["model_tier"] == tier
            ],
            bucket=bucket,
            model_tier=tier,
            ground_truth=ground_truth,
        )
        for bucket in buckets
        for tier in tiers
    }
    by_tier = {
        tier: summarize(
            [row for row in materialized if row["model_tier"] == tier],
            bucket=None,
            model_tier=tier,
            ground_truth=ground_truth,
        )
        for tier in tiers
    }

    return BenchmarkReport(
        runs=materialized,
        raw_runs=raw,
        buckets=buckets,
        tiers=tiers,
        by_bucket_tier=by_bucket_tier,
        by_tier=by_tier,
        overall=summarize(
            list(materialized), bucket=None, model_tier=None, ground_truth=ground_truth
        ),
        ground_truth_available=ground_truth is not None,
        sessions=build_session_views(
            materialized, sessions, pairs, ground_truth=ground_truth
        ),
        per_pass=summarize_passes(raw),
        db_path=db_path,
    )


# --------------------------------------------------------------------------
# Formatting primitives shared by both renderers
# --------------------------------------------------------------------------

DASH = "--"


def _pct(value: float | None, *, digits: int = 1) -> str:
    return DASH if value is None else f"{value * 100:.{digits}f}%"


def _signed_pct(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return DASH
    return f"{value * 100:+.{digits}f}pp"


def _ms(value: float | None) -> str:
    return DASH if value is None else f"{value:,.0f}"


def _money(value: float | None, *, digits: int = 4) -> str:
    if value is None:
        return DASH
    if value == 0:
        return "$0"
    return f"${value:,.{digits}f}"


def _count(value: float | None) -> str:
    return DASH if value is None else f"{value:,.0f}"


def _tier_label(tier: str | None) -> str:
    if tier is None:
        return "all tiers"
    return TIER_LABELS.get(tier, tier)


def _group_label(stats: GroupStats) -> str:
    """A tier label that admits when its numbers are not measurements.

    Marked per row rather than only in the header: a bucket block can be
    copied out of a terminal on its own, and a header warning left behind is
    a warning that did not work.
    """
    label = _tier_label(stats.model_tier)
    if stats.is_mixed:
        return f"{label} [MIXED!]"
    if stats.has_synthetic:
        return f"{label}{SYNTHETIC_MARKER}"
    return label


def _run_tier_label(row: Run) -> str:
    """Tier label for one individual run, marked if that row is synthetic.

    Reads `model_name` straight off the row rather than going through a
    `GroupStats`: the runs table renders rows one at a time, and in a mixed
    store the group marker would tar measured rows with the same brush.
    """
    label = _tier_label(row["model_tier"])
    if str(row["model_name"] or "").startswith(SYNTHETIC_MODEL_PREFIX):
        return f"{label}{SYNTHETIC_MARKER}"
    return label


def _synthetic_banner(report: BenchmarkReport) -> list[str]:
    """The header warning for a report built on seeded rows. Empty if clean."""
    if not report.has_synthetic:
        return []

    if report.is_fully_synthetic:
        headline = (
            f"WARNING: all {report.total_runs:,} rows are SYNTHETIC demo data "
            "-- every number below is fabricated."
        )
    else:
        headline = (
            f"WARNING: {report.synthetic_runs:,} of {report.total_runs:,} rows "
            "are SYNTHETIC demo data, mixed in with measured runs."
        )

    return [
        "  " + "=" * 72,
        f"  {headline}",
        "  Rows written by `seed-demo` to exercise this report before the",
        "  benchmark existed. Latencies, tokens and verdicts were invented;",
        "  no model was called. Delete the store (or use --db) and run the",
        "  benchmark for real numbers. Affected groups are marked"
        f" '{SYNTHETIC_MARKER.strip()}'.",
        "  " + "=" * 72,
        "",
    ]


def _accuracy_note(available: bool) -> str:
    if available:
        return "Accuracy scored against the supplied hand-labeled ground truth."
    return (
        "Accuracy (role/theme/synergy F1, game_stage MAE, power_rating MAE): "
        "UNAVAILABLE -- no hand-labeled ground truth has been produced yet "
        "(docs/benchmarking-and-testing.md, 'Ground truth'). These are absent, "
        "not zero, and model output is never scored against another model's."
    )


# --------------------------------------------------------------------------
# Plain-text rendering
# --------------------------------------------------------------------------

# (header, alignment) for the metric table used by both the per-bucket blocks
# and the per-tier roll-up. One definition so the two tables stay column-for-
# column comparable, which is the only reason reading them together works.
_METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tier", "<"),
    ("runs", ">"),
    ("valid", ">"),
    ("p50 ms", ">"),
    ("p95 ms", ">"),
    ("mean ms", ">"),
    ("$ total", ">"),
    ("$/run", ">"),
    ("in tok", ">"),
    ("out tok", ">"),
    ("revd", ">"),
    ("good", ">"),
    ("acc", ">"),
    ("wrong", ">"),
)


def _metric_row(stats: GroupStats) -> list[str]:
    verdicts = stats.verdicts
    return [
        _group_label(stats),
        f"{stats.runs:,}",
        _pct(stats.schema_valid_rate, digits=0),
        _ms(stats.latency_p50),
        _ms(stats.latency_p95),
        _ms(stats.latency_mean),
        _money(stats.cost_total, digits=2),
        _money(stats.cost_mean),
        _count(stats.input_tokens_mean),
        _count(stats.output_tokens_mean),
        _pct(verdicts.review_coverage, digits=0),
        str(verdicts.counts.get("good", 0)),
        str(verdicts.counts.get("acceptable", 0)),
        str(verdicts.counts.get("wrong", 0)),
    ]


def _render_table(
    columns: Sequence[tuple[str, str]], rows: Sequence[Sequence[str]], indent: str = "  "
) -> list[str]:
    """Column-aligned fixed-width text table."""
    headers = [header for header, _ in columns]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(columns))
    ]

    def format_row(values: Sequence[str]) -> str:
        cells = [
            f"{value:{align}{width}}"
            for value, (_, align), width in zip(values, columns, widths)
        ]
        return indent + "  ".join(cells).rstrip()

    lines = [format_row(headers), indent + "  ".join("-" * width for width in widths)]
    lines.extend(format_row(row) for row in rows)
    return lines


def render_text(report: BenchmarkReport) -> str:
    """The day-to-day view: the whole benchmark, aligned, in one terminal screen.

    Ordered by what decides something. The per-bucket blocks come first because
    the tier comparison inside a bucket is the comparison; the hard-bucket gap
    gets its own section right after, because that one row of numbers is what
    the escalation rule is argued from; the per-tier roll-up comes last, clearly
    marked as the number *not* to decide on.
    """
    lines: list[str] = []
    overall = report.overall

    lines.append("Tome metadata benchmark -- run log summary")
    source = str(report.db_path) if report.db_path else "(rows supplied directly)"
    lines.append(f"  source     {source}")
    lines.append(f"  generated  {report.generated_at:%Y-%m-%d %H:%M} UTC")
    lines.append(
        f"  runs       {overall.runs:,} across {overall.cards:,} cards, "
        f"{len(report.tiers)} tier(s), {len(report.buckets)} bucket(s)"
    )
    lines.append(
        f"  reviewed   {overall.verdicts.reviewed:,} of {overall.runs:,} "
        f"({_pct(overall.verdicts.review_coverage, digits=0)})"
    )
    lines.append("")
    lines.extend(_synthetic_banner(report))

    if overall.runs == 0:
        lines.append("No runs recorded. Nothing to report yet.")
        lines.append("")
        lines.append(_accuracy_note(report.ground_truth_available))
        return "\n".join(lines) + "\n"

    lines.extend(_render_sessions_text(report))

    lines.append("PER BUCKET, TIERS SIDE BY SIDE  (pooled across sessions)")
    lines.append("")
    for bucket in report.buckets:
        group = report.bucket_view(bucket)
        cards = len(
            {row["card_oracle_id"] for row in report.runs if row["bucket"] == bucket}
        )
        lines.append(f"  [{bucket}]  {cards:,} cards")
        lines.extend(
            _render_table(
                _METRIC_COLUMNS, [_metric_row(stats) for stats in group], indent="    "
            )
        )
        lines.append("")

    lines.extend(_render_hard_gap_text(report))

    lines.append("PER TIER, ALL BUCKETS  (context only -- the average across all")
    lines.append("300 cards is not what decides the escalation rule)")
    lines.append("")
    lines.extend(
        _render_table(
            _METRIC_COLUMNS,
            [_metric_row(report.by_tier[tier]) for tier in report.tiers],
            indent="    ",
        )
    )
    lines.append("")

    lines.extend(_render_per_pass_text(report))
    lines.extend(_render_timing_text(report))
    lines.extend(_render_accuracy_text(report))
    return "\n".join(lines) + "\n"


_PER_PASS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tier", "<"),
    ("pass", "<"),
    ("calls", ">"),
    ("valid", ">"),
    ("p50 ms", ">"),
    ("in tok", ">"),
    ("out tok", ">"),
    ("prefill tok/s", ">"),
    ("decode tok/s", ">"),
)

_PER_PASS_NOTE = (
    "One row per model call, NOT per card -- the only table here that is not\n"
    "    merged across the two passes, and the reason the unmerged rows are\n"
    "    kept. Pass one prefills the taxonomy to generate seven fields; pass\n"
    "    two prefills a short rubric plus the ladders pass one selected to\n"
    "    generate two. Their prefill and decode rates are different numbers\n"
    "    about different work, and averaging them produces one that describes\n"
    "    neither. Rates come from the phase timers, never from total latency."
)


def _render_per_pass_text(report: BenchmarkReport) -> list[str]:
    """Per-(tier, pass) call statistics. The view the merge cannot give."""
    if not report.per_pass:
        return []

    lines = ["PER PASS  (call-level; not merged)", ""]
    rows: list[tuple[str, ...]] = []
    for tier in report.tiers:
        for pass_name in ("one", "two"):
            stats = report.per_pass.get((tier, pass_name))
            if stats is None:
                continue
            rows.append(
                (
                    _tier_label(tier),
                    pass_name,
                    f"{stats.calls:,}",
                    _pct(stats.schema_valid_rate, digits=0),
                    _ms(stats.latency_p50),
                    _count(stats.input_tokens_mean),
                    _count(stats.output_tokens_mean),
                    (
                        DASH
                        if stats.prefill_tokens_per_second is None
                        else f"{stats.prefill_tokens_per_second:,.0f}"
                    ),
                    (
                        DASH
                        if stats.decode_tokens_per_second is None
                        else f"{stats.decode_tokens_per_second:,.1f}"
                    ),
                )
            )
    lines.extend(_render_table(_PER_PASS_COLUMNS, rows, indent="    "))
    lines.append("")
    lines.append(f"    {_PER_PASS_NOTE}")
    lines.append("")
    return lines


_TIMING_NOTE = (
    "corr(mana value, early) over plain-cost cards only -- cards whose mana\n"
    "    value is NOT their cost to function (convoke, equip costs, {X}, lands;\n"
    "    ~17% of the corpus) are excluded, because a weak correlation is the\n"
    "    right answer for them. Strongly negative is the target: a card you\n"
    "    cannot cast until turn seven is not doing much on turn two."
)


def _render_timing_text(report: BenchmarkReport) -> list[str]:
    """`game_stage` calibration -- the half of scoring that needs no labels."""
    lines = ["GAME STAGE CALIBRATION", ""]

    columns = (
        ("bucket", "<"),
        ("tier", "<"),
        ("castability r", ">"),
        ("early mean", ">"),
        ("plain", ">"),
        ("excepted", ">"),
        *((shape, ">") for shape in SHAPE_ORDER),
    )
    rows = []
    for bucket in report.buckets:
        for stats in report.bucket_view(bucket):
            scores = stats.timing
            if scores is None or stats.is_empty:
                continue
            rows.append(
                [
                    bucket,
                    _group_label(stats),
                    DASH
                    if scores.castability_corr is None
                    else f"{scores.castability_corr:+.3f}",
                    DASH if scores.early_mean is None else f"{scores.early_mean:.2f}",
                    f"{scores.plain_runs:,}",
                    f"{scores.exception_runs:,}",
                    *(_pct(scores.shape_rate(shape), digits=0) for shape in SHAPE_ORDER),
                ]
            )

    if not rows:
        lines.append("    No parsed game_stage values recorded yet.")
        lines.append("")
        return lines

    lines.extend(_render_table(columns, rows, indent="    "))
    lines.append("")
    lines.append(f"    {_TIMING_NOTE}")
    lines.append("")
    return lines


_SESSION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("session", "<"),
    ("buckets", "<"),
    ("cards", ">"),
    ("tiers", "<"),
    ("status", "<"),
    ("decided", ">"),
    ("tally", "<"),
    ("valid", ">"),
    ("p50 ms", ">"),
    ("prompt", "<"),
)


def _render_sessions_text(report: BenchmarkReport) -> list[str]:
    """The paired view, first, because it is the only sound comparison.

    Everything below it in the text report pools sessions. That pooling is
    useful and is also the thing most likely to be misread, so the per-session
    table comes first and the pooled headings say what they are.
    """
    lines = ["SESSIONS  <- the paired view: one card set, every tier", ""]
    if not report.sessions:
        lines.append(
            "  No sessions recorded. `run --bucket hard --cards 30` creates one."
        )
        lines.append("")
        return lines

    rows = []
    for view in report.sessions:
        tally = view.wins
        rows.append(
            [
                view.session_id + (f" ({view.label})" if view.label else ""),
                "+".join(view.buckets),
                f"{len(view.cards):,}",
                ">".join(_tier_label(tier) for tier in view.tiers),
                view.status + ("" if view.is_paired else " UNPAIRED"),
                f"{tally.decided:,}/{len(view.cards):,}",
                tally.tally,
                " / ".join(
                    _pct(view.by_tier[tier].schema_valid_rate, digits=0)
                    for tier in view.tiers
                ),
                " / ".join(_ms(view.by_tier[tier].latency_p50) for tier in view.tiers),
                view.prompt_sha256[:8],
            ]
        )
    lines.extend(_render_table(_SESSION_COLUMNS, rows, indent="    "))
    lines.append("")

    for view in report.sessions:
        tally = view.wins
        if tally.decided == 0:
            lines.append(
                f"  {view.session_id}: no picks recorded yet "
                f"({len(view.cards):,} card(s) to decide). "
                f"`report --html` renders the side-by-side page to pick on."
            )
            continue
        for tier in view.tiers:
            interval = tally.interval(tier)
            hours = view.projected_corpus_hours(tier)
            lines.append(
                f"  {view.session_id} {_tier_label(tier):10} "
                f"wins {tally.counts.get(tier, 0):>3}/{tally.decided:<3} "
                f"= {_pct(tally.rate(tier), digits=0):>5}"
                + (
                    f"  95% CI {_pct(interval[0], digits=0)}-{_pct(interval[1], digits=0)}"
                    if interval
                    else ""
                )
                + (
                    f"  full corpus ~{hours:,.0f}h"
                    if hours is not None
                    else ""
                )
            )
        if not view.is_paired:
            lines.append(
                f"    ! {view.session_id} is UNPAIRED: {view.missing_cells:,} "
                "(card, tier) cell(s) missing, so these rates compare "
                "different card sets. Finish it with "
                f"`run --resume {view.session_id}`."
            )

    lines.append("")
    lines.append(
        "  tally = wins per tier in run order, then tie, then both-bad."
    )
    lines.append(
        "  A win rate over ~30 cards carries roughly +/-18 points of 95%"
    )
    lines.append(
        "  uncertainty: a near-even tally is noise, not a finding. The slower"
    )
    lines.append(
        "  tier also has to win by enough to justify its full-corpus hours."
    )
    lines.append("")
    if report.pools_mixed_prompts:
        lines.append(
            f"  ! These sessions ran against {len(report.prompt_fingerprints)} "
            "different system prompts, so every pooled table below averages"
        )
        lines.append(
            "    answers to different questions (the taxonomy, rubric and anchor"
        )
        lines.append(
            "    ladders all live in that block). Read the sessions separately."
        )
        lines.append("")
    return lines


def _render_hard_gap_text(report: BenchmarkReport) -> list[str]:
    """The deciding section: every tier's distance from frontier on hard cards."""
    lines = ["THE DECIDING NUMBERS  (hard bucket, each tier vs frontier)", ""]

    if "hard" not in report.buckets:
        lines.append("    No hard-bucket runs recorded yet -- nothing decided.")
        lines.append("")
        return lines

    gaps = report.gaps("hard")
    if not gaps:
        lines.append(
            "    No frontier baseline in the hard bucket -- run the frontier tier "
            "before comparing."
        )
        lines.append("")
        return lines

    columns = (
        ("tier", "<"),
        ("d valid", ">"),
        ("d good", ">"),
        ("p50 ms", ">"),
        ("vs base", ">"),
        ("$/run", ">"),
        ("saved/run", ">"),
    )
    rows = [
        [
            _group_label(report.by_bucket_tier[("hard", gap.model_tier)]),
            _signed_pct(gap.schema_valid_delta, digits=0),
            _signed_pct(gap.good_rate_delta, digits=0),
            _ms(gap.latency_p50),
            DASH if gap.latency_ratio is None else f"{gap.latency_ratio:.2f}x",
            _money(gap.cost_mean),
            _money(gap.cost_saved_per_run),
        ]
        for gap in gaps
    ]
    lines.extend(_render_table(columns, rows, indent="    "))

    baseline = report.by_bucket_tier[("hard", BASELINE_TIER)]
    lines.append("")
    lines.append(
        f"    baseline ({_group_label(baseline)}): "
        f"{_pct(baseline.schema_valid_rate, digits=0)} schema-valid, "
        f"{_pct(baseline.verdicts.good_rate, digits=0)} good of "
        f"{baseline.verdicts.reviewed:,} reviewed, "
        f"{_ms(baseline.latency_p50)} ms p50, {_money(baseline.cost_mean)}/run"
    )
    for line in _review_caveats(report, "hard"):
        lines.append(f"    NOTE: {line}")
    lines.append("")
    return lines


def _review_caveats(report: BenchmarkReport, bucket: str) -> list[str]:
    """Why a verdict column in this bucket may be blank or provisional.

    A `--` in the verdict-gap column means "nobody has read those outputs yet",
    not "the tier scored nothing", and the two would be read very differently
    by someone skimming for a decision. Rather than filling the gap with a
    number, say which tiers the review hasn't reached.
    """
    caveats: list[str] = []
    baseline = report.by_bucket_tier.get((bucket, BASELINE_TIER))
    if baseline is not None and baseline.verdicts.reviewed < baseline.runs:
        caveats.append(
            f"only {baseline.verdicts.reviewed:,} of {baseline.runs:,} "
            f"{bucket}-bucket {_tier_label(BASELINE_TIER)} runs are reviewed; "
            "the verdict gap is provisional until a maintainer has read the rest."
        )

    unreviewed = [
        _tier_label(tier)
        for tier in report.tiers
        if tier != BASELINE_TIER
        and not report.by_bucket_tier[(bucket, tier)].is_empty
        and report.by_bucket_tier[(bucket, tier)].verdicts.reviewed == 0
    ]
    if unreviewed:
        caveats.append(
            f"no {bucket}-bucket runs reviewed yet for {', '.join(unreviewed)} -- "
            "their verdict gap reads as no data, not as a zero score."
        )
    return caveats


def _render_accuracy_text(report: BenchmarkReport) -> list[str]:
    lines = ["ACCURACY", ""]
    if not report.ground_truth_available:
        lines.append(f"    {_accuracy_note(False)}")
        lines.append("")
        return lines

    columns = (
        ("bucket", "<"),
        ("tier", "<"),
        ("role F1", ">"),
        ("theme F1", ">"),
        ("synergy F1", ">"),
        ("stage MAE", ">"),
        ("power MAE", ">"),
        ("scored", ">"),
    )
    rows = []
    for bucket in report.buckets:
        for stats in report.bucket_view(bucket):
            scores = stats.accuracy
            if scores is None or stats.is_empty:
                continue
            rows.append(
                [
                    bucket,
                    _group_label(stats),
                    DASH if scores.role_f1 is None else f"{scores.role_f1:.3f}",
                    DASH if scores.theme_f1 is None else f"{scores.theme_f1:.3f}",
                    DASH if scores.synergy_f1 is None else f"{scores.synergy_f1:.3f}",
                    DASH if scores.game_stage_mae is None else f"{scores.game_stage_mae:.2f}",
                    DASH if scores.power_rating_mae is None else f"{scores.power_rating_mae:.2f}",
                    f"{scores.scored:,}/{scores.scored + scores.unscorable:,}",
                ]
            )
    lines.extend(_render_table(columns, rows, indent="    "))
    lines.append("")
    return lines


# --------------------------------------------------------------------------
# HTML rendering
#
# One file, no network. Every byte of CSS, JS and chart geometry is inline, and
# there is not a single external reference -- no CDN, no webfont, no image host.
# That is a hard requirement rather than a preference: the artifact gets opened
# from a `file://` path, mailed around, and read months after the benchmark ran,
# possibly offline, and anything that 404s silently changes what the maintainer
# sees. It also means the report can be read without trusting a third party with
# the model output it contains.
#
# Charts are hand-written SVG for the same reason -- a chart library is a CDN
# script or a build step, and the two charts here are a grouped bar chart and a
# pair of comparison bars.
# --------------------------------------------------------------------------

# Categorical hues for the three tiers, taken from the reference data-viz
# palette's first three slots and validated with its checker against both
# surfaces (`--pairs all`, since tiers appear side by side and pair up in every
# direction, not just adjacently): worst CVD separation dE 9.2 light / 9.4 dark
# against a >= 8 target, worst normal-vision dE 24.0 / 20.9 against a >= 15
# floor. Colour is never the only channel -- every bar carries a printed value,
# the legend is always present, and the full run table repeats every number as
# text, which also discharges the light-mode contrast warning on the aqua slot.
#
# Tiers are assigned by identity (capability order), not by rank, so filtering
# or a tier dropping out never repaints the survivors.
_SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a")
_SERIES_DARK = ("#3987e5", "#d95926", "#199e70")

_CSS = """
:root {
  color-scheme: light;
  --surface-page: #f9f9f7;
  --surface-1: #fcfcfb;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --gridline: #e1e0d9;
  --axis: #c3c2b7;
  --border: rgba(11, 11, 11, 0.10);
  --code-surface: #f2f1ed;
  --good: #0ca30c;
  --critical: #d03b3b;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface-page: #0d0d0d;
    --surface-1: #1a1a19;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --gridline: #2c2c2a;
    --axis: #383835;
    --border: rgba(255, 255, 255, 0.10);
    --code-surface: #151514;
    --good: #0ca30c;
    --critical: #e66767;
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 24px 96px;
  background: var(--surface-page);
  color: var(--text-primary);
  font: 14px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1120px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 15px; margin: 40px 0 4px; letter-spacing: -0.005em; }
h3 { font-size: 13px; margin: 20px 0 8px; color: var(--text-secondary); font-weight: 600; }
p.sub { margin: 0 0 4px; color: var(--text-secondary); font-size: 13px; }
p.note { margin: 6px 0 0; color: var(--text-muted); font-size: 12px; max-width: 78ch; }
.meta { color: var(--text-muted); font-size: 12px; margin: 0 0 24px; }
.meta code { font-size: 12px; }
.panel {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  margin-top: 12px;
  overflow-x: auto;
}
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
.tile {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 16px;
  min-width: 132px;
  flex: 1 1 132px;
}
.tile .value { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; }
.tile .label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.callout {
  border-left: 3px solid var(--series-1);
  padding: 2px 0 2px 12px;
  margin: 10px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
  max-width: 80ch;
}
/* Synthetic-data warning. Loud by design: this banner is the only thing
   standing between a seeded store and a reader quoting invented latencies. */
.synthetic {
  border: 2px solid #b4541e;
  background: #2a160c;
  border-radius: 10px;
  padding: 14px 18px;
  margin: 18px 0 0;
  color: #f3c89e;
  font-size: 13px;
  max-width: 90ch;
}
.synthetic strong { color: #ffb570; display: block; margin-bottom: 4px; font-size: 14px; }
.synthetic code { color: #ffd9b0; }
.unavailable {
  border-left: 3px solid var(--text-muted);
  background: var(--surface-1);
  border-radius: 0 8px 8px 0;
  padding: 10px 14px;
  color: var(--text-secondary);
  font-size: 13px;
  max-width: 84ch;
}
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 10px; text-align: right; white-space: nowrap; }
th:first-child, td:first-child, th.txt, td.txt { text-align: left; }
thead th {
  color: var(--text-muted);
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--axis);
}
tbody td { border-bottom: 1px solid var(--gridline); font-variant-numeric: tabular-nums; }
tbody tr:hover td { background: var(--code-surface); }
td.num, th.num { font-variant-numeric: tabular-nums; }
.swatch {
  display: inline-block; width: 9px; height: 9px; border-radius: 2px;
  margin-right: 7px; vertical-align: baseline;
}
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: var(--text-secondary); margin: 0 0 4px; }
.charts { display: flex; flex-wrap: wrap; gap: 12px; }
.charts .panel { flex: 1 1 380px; margin-top: 0; }
svg { display: block; width: 100%; height: auto; }
svg .bar { transition: opacity 120ms ease; }
svg g.group:hover .bar { opacity: 0.55; }
svg g.group .bar:hover { opacity: 1; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 12px 0 0; }
.controls label { font-size: 12px; color: var(--text-muted); display: flex; gap: 6px; align-items: center; }
.controls select, .controls input {
  font: inherit; font-size: 13px; padding: 4px 8px;
  background: var(--surface-1); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 6px;
}
.controls input { min-width: 220px; }
#runcount { font-size: 12px; color: var(--text-muted); }
table.runs th[data-sort] { cursor: pointer; user-select: none; }
table.runs th[data-sort]:hover { color: var(--text-primary); }
table.runs th[data-sort]::after { content: ""; font-size: 9px; }
table.runs th[aria-sort="ascending"]::after { content: " \\2191"; }
table.runs th[aria-sort="descending"]::after { content: " \\2193"; }
tbody.run + tbody.run { border-top: 0; }
tbody.run tr.detail td { border-bottom: 1px solid var(--axis); padding-top: 0; }
details { margin: 6px 0; }
details summary {
  cursor: pointer; font-size: 12px; color: var(--text-secondary);
  padding: 2px 0; white-space: normal;
}
details pre {
  margin: 6px 0 12px;
  padding: 10px 12px;
  background: var(--code-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
}
.pill {
  display: inline-block; padding: 1px 7px; border-radius: 999px;
  font-size: 11px; border: 1px solid var(--border);
}
.pill.good { color: var(--good); }
.pill.wrong { color: var(--critical); }
.pill.none { color: var(--text-muted); }
.empty { color: var(--text-muted); font-style: italic; padding: 24px 0; }

/* --- tabs: one per session, plus the pooled Overview ------------------- */
.tabs {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin: 20px 0 0; padding-bottom: 10px;
  border-bottom: 1px solid var(--axis);
}
.tab {
  font: inherit; font-size: 12px; cursor: pointer;
  padding: 5px 11px; border-radius: 999px;
  background: var(--surface-1); color: var(--text-secondary);
  border: 1px solid var(--border);
}
.tab:hover { color: var(--text-primary); }
.tab[aria-selected="true"] {
  background: var(--series-1); border-color: var(--series-1); color: #fff;
}
.panel-body[hidden] { display: none; }
.panel-body > h2:first-child { margin-top: 24px; }

/* Provenance strip. Deliberately dense and above the numbers. */
.metagrid {
  display: grid; gap: 8px 24px;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  font-size: 12px;
}
.meta-item { display: flex; gap: 10px; }
.meta-key {
  color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.05em; min-width: 72px; flex: none;
}
.tile .sublabel { font-size: 11px; color: var(--text-muted); margin-top: 4px; line-height: 1.4; }
.tile .value .of { font-size: 13px; color: var(--text-muted); font-weight: 400; }

/* Per-card comparison. One row per card, a column per tier. */
table.compare td.missing { color: var(--text-muted); font-style: italic; }
table.compare tbody.card-row + tbody.card-row { border-top: 0; }
table.compare tbody.card-row tr.detail td { border-bottom: 1px solid var(--axis); padding-top: 0; }
td.picks { white-space: normal; min-width: 240px; }
label.pick {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; color: var(--text-secondary); margin-right: 8px;
}
input.picknote {
  font: inherit; font-size: 12px; margin-top: 4px; width: 100%;
  padding: 3px 6px; border-radius: 6px;
  background: var(--surface-1); color: var(--text-primary);
  border: 1px solid var(--border);
}
.sides { display: flex; flex-wrap: wrap; gap: 14px; }
.sides .side { flex: 1 1 320px; min-width: 0; }
.sides .side.wide { flex-basis: 100%; }
.sides h4 {
  font-size: 11px; margin: 8px 0 2px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.05em;
}
.copybox { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.copybox .note { margin: 0; flex: 1 1 320px; }
button.action {
  font: inherit; font-size: 13px; cursor: pointer; padding: 6px 14px;
  border-radius: 8px; background: var(--series-1); color: #fff;
  border: 1px solid var(--series-1);
}
textarea.copyout {
  flex-basis: 100%; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  padding: 10px 12px; border-radius: 8px; resize: vertical;
  background: var(--code-surface); color: var(--text-primary);
  border: 1px solid var(--border);
}
"""

# No benchmark data is ever passed into JS -- the script only reads the DOM and
# toggles `display`, so untrusted model output never leaves an escaped text node
# and can never reach a string literal in a <script> block.
#
# The one place the script reads data rather than only toggling it is the pick
# collector, and what it reads is deliberately restricted to ids and tier names
# out of `data-` attributes: oracle ids, run ids, the chosen tier, plus a note
# the *reader* typed. A card name goes along for legibility and is the only
# corpus-derived string involved. No model output is ever read back.
_JS = """
(function () {
  /* --- tabs ---------------------------------------------------------- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel-body'));

  function select(name) {
    var matched = false;
    panels.forEach(function (panel) {
      var isMatch = panel.id === 'panel-' + name;
      panel.hidden = !isMatch;
      if (isMatch) matched = true;
    });
    tabs.forEach(function (tab) {
      tab.setAttribute(
        'aria-selected', tab.getAttribute('data-tab') === name ? 'true' : 'false'
      );
    });
    return matched;
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var name = tab.getAttribute('data-tab');
      if (select(name)) {
        /* Replace rather than push: the tab bar is a view switch, not a
           history of clicks to walk back through. */
        history.replaceState(null, '', '#' + name);
      }
    });
  });

  var fromHash = (location.hash || '').replace(/^#/, '');
  if (!fromHash || !select(fromHash)) {
    var fallback = document.querySelector('.tab[data-default]') || tabs[0];
    if (fallback) select(fallback.getAttribute('data-tab'));
  }

  /* --- collecting picks ---------------------------------------------- */
  Array.prototype.slice.call(document.querySelectorAll('[data-copy]')).forEach(
    function (button) {
      button.addEventListener('click', function () {
        var sessionId = button.getAttribute('data-copy');
        var panel = document.getElementById('panel-session-' + sessionId);
        var output = document.querySelector(
          '[data-copyout="' + sessionId + '"]'
        );
        if (!panel || !output) return;

        var picks = [];
        Array.prototype.slice.call(panel.querySelectorAll('td.picks')).forEach(
          function (cell) {
            var chosen = cell.querySelector('input[type="radio"]:checked');
            if (!chosen) return;
            var note = cell.querySelector('input.picknote');
            var runs = {};
            try { runs = JSON.parse(cell.getAttribute('data-runs') || '{}'); }
            catch (err) { runs = {}; }
            picks.push({
              card_oracle_id: cell.getAttribute('data-oracle'),
              card_name: cell.getAttribute('data-card'),
              winner: chosen.value,
              runs: runs,
              notes: note && note.value ? note.value : null
            });
          }
        );

        output.value = JSON.stringify(
          { session_id: sessionId, picks: picks }, null, 1
        );
        output.focus();
        output.select();
        /* Best-effort only: a file:// page may have no clipboard permission,
           which is why the text is left selected in a visible box rather than
           copied invisibly and assumed to have worked. */
        try {
          if (navigator.clipboard) { navigator.clipboard.writeText(output.value); }
          else { document.execCommand('copy'); }
        } catch (err) { /* the box is the fallback */ }
      });
    }
  );

  /* --- the Overview's flat run table --------------------------------- */
  var table = document.getElementById('runs');
  if (!table) return;
  var bodies = Array.prototype.slice.call(table.tBodies);
  var count = document.getElementById('runcount');
  /* Scoped to the Overview panel: the session tabs have their own controls,
     and a global [data-filter] sweep would bind them together. */
  var inputs = Array.prototype.slice.call(
    document.querySelectorAll('#panel-overview [data-filter]')
  );

  function applyFilters() {
    var shown = 0;
    bodies.forEach(function (body) {
      var visible = inputs.every(function (input) {
        var value = input.value;
        if (!value) return true;
        var key = input.getAttribute('data-filter');
        if (key === 'q') {
          return (body.getAttribute('data-search') || '')
            .indexOf(value.toLowerCase()) !== -1;
        }
        return body.getAttribute('data-' + key) === value;
      });
      body.style.display = visible ? '' : 'none';
      if (visible) shown += 1;
    });
    if (count) {
      count.textContent = shown + ' of ' + bodies.length + ' runs shown';
    }
  }

  inputs.forEach(function (input) {
    input.addEventListener('input', applyFilters);
    input.addEventListener('change', applyFilters);
  });

  var headers = Array.prototype.slice.call(table.querySelectorAll('th[data-sort]'));
  headers.forEach(function (header) {
    header.addEventListener('click', function () {
      var key = header.getAttribute('data-sort');
      var numeric = header.getAttribute('data-type') === 'num';
      var descending = header.getAttribute('aria-sort') === 'descending';
      var direction = descending ? 1 : -1;
      headers.forEach(function (other) { other.removeAttribute('aria-sort'); });
      header.setAttribute('aria-sort', descending ? 'ascending' : 'descending');
      bodies.slice().sort(function (a, b) {
        var left = a.getAttribute('data-' + key) || '';
        var right = b.getAttribute('data-' + key) || '';
        if (numeric) {
          return (parseFloat(left) - parseFloat(right)) * direction;
        }
        return left.localeCompare(right) * direction;
      }).forEach(function (body) { table.appendChild(body); });
    });
  });

  applyFilters();
})();
"""


def _esc(value: Any) -> str:
    """HTML-escape anything, including None and non-strings.

    Everything user- or model-supplied goes through this: card names, model
    identifiers, prompts, raw responses, human notes. `raw_response` in
    particular is untrusted text -- it is whatever a model emitted, it routinely
    contains JSON and markdown fences, and a code-tuned model asked for
    structured output is exactly the kind of thing that emits angle brackets.
    Quotes are escaped too (the `html.escape` default), because these strings
    also land in `data-` attributes used by the filter script.
    """
    if value is None:
        return ""
    return html.escape(str(value))


def _nice_ceiling(value: float) -> float:
    """A round axis maximum at or above `value`, so tick labels read cleanly."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10.0**exponent
    for multiple in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= multiple * base:
            return multiple * base
    return 10 * base


def _bar_path(x: float, y: float, width: float, height: float) -> str:
    """A vertical bar with 4px-rounded top corners, square on the baseline.

    Rounded at the data end only: the baseline edge is a measurement and must
    stay flat against the axis, while the rounded top is what keeps a dense
    grouped chart from reading as a picket fence.
    """
    radius = min(4.0, width / 2, height)
    bottom = y + height
    return (
        f"M{x:.1f},{bottom:.1f} "
        f"L{x:.1f},{y + radius:.1f} "
        f"Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} "
        f"L{x + width - radius:.1f},{y:.1f} "
        f"Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + radius:.1f} "
        f"L{x + width:.1f},{bottom:.1f} Z"
    )


def _grouped_bar_svg(
    *,
    groups: Sequence[str],
    series: Sequence[str],
    values: Mapping[tuple[str, str], float | None],
    labels: Mapping[tuple[str, str], str],
    axis_format,
    axis_max: float | None = None,
    height: int = 260,
) -> str:
    """A grouped bar chart: one cluster per group, one bar per series.

    Deliberately a *grouped* chart and never a dual-axis one -- where two
    measures of different scale need comparing (latency and cost), this gets
    called twice and the two charts sit side by side. Two y-scales on one frame
    would let the pair be arranged to tell any story at all.

    Every bar is directly labeled with its value. That is both the relief for
    the light-mode contrast warning on one of the three hues and the honest
    answer to "what exactly is that bar" -- there are at most nine bars, so
    labeling them all costs nothing a legend-plus-guess would save.
    """
    width = 760
    left, right, top, bottom = 52, 14, 26, 46
    plot_width = width - left - right
    plot_height = height - top - bottom

    numeric = [value for value in values.values() if value is not None]
    ceiling = axis_max if axis_max is not None else _nice_ceiling(max(numeric, default=0.0))
    if ceiling <= 0:
        ceiling = 1.0

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]

    # Gridlines and y ticks -- recessive, behind the marks.
    ticks = 4
    for index in range(ticks + 1):
        value = ceiling * index / ticks
        y = top + plot_height - (plot_height * index / ticks)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" '
            f'stroke="var(--gridline)" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="var(--text-muted)" style="font-variant-numeric:tabular-nums">'
            f"{_esc(axis_format(value))}</text>"
        )
    parts.append(
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
        f'y2="{top + plot_height}" stroke="var(--axis)" stroke-width="1" />'
    )

    group_width = plot_width / max(len(groups), 1)
    cluster_width = group_width * 0.66
    bar_slot = cluster_width / max(len(series), 1)
    # A 2px surface gap between neighbouring bars, per the mark spec: touching
    # fills read as one shape, which is the opposite of what a comparison wants.
    bar_width = max(bar_slot - 2, 2.0)

    for group_index, group in enumerate(groups):
        group_center = left + group_width * (group_index + 0.5)
        parts.append('<g class="group">')
        for series_index, name in enumerate(series):
            value = values.get((group, name))
            x = group_center - cluster_width / 2 + bar_slot * series_index + 1
            color = f"var(--series-{series_index + 1})"
            label = labels.get((group, name), "")
            if value is None:
                parts.append(
                    f'<text x="{x + bar_width / 2:.1f}" y="{top + plot_height - 6}" '
                    f'text-anchor="middle" font-size="10" fill="var(--text-muted)">'
                    f"n/a</text>"
                )
                continue
            bar_height = max(plot_height * (value / ceiling), 0.0)
            y = top + plot_height - bar_height
            parts.append(
                f'<path class="bar" d="{_bar_path(x, y, bar_width, bar_height)}" '
                f'fill="{color}"><title>{_esc(group)} / {_esc(name)}: '
                f"{_esc(label)}</title></path>"
            )
            parts.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" '
                f'text-anchor="middle" font-size="10" fill="var(--text-secondary)" '
                f'style="font-variant-numeric:tabular-nums">{_esc(label)}</text>'
            )
        parts.append("</g>")
        parts.append(
            f'<text x="{group_center:.1f}" y="{top + plot_height + 18}" '
            f'text-anchor="middle" font-size="12" fill="var(--text-secondary)">'
            f"{_esc(group)}</text>"
        )

    parts.append("</svg>")
    return "".join(parts)


def _legend(series: Sequence[str]) -> str:
    items = "".join(
        f'<span><span class="swatch" style="background:var(--series-{index + 1})"></span>'
        f"{_esc(name)}</span>"
        for index, name in enumerate(series)
    )
    return f'<div class="legend">{items}</div>'


def _chart_panel(title: str, note: str, series: Sequence[str], svg: str) -> str:
    return (
        f'<div class="panel"><h3>{_esc(title)}</h3>{_legend(series)}{svg}'
        f'<p class="note">{_esc(note)}</p></div>'
    )


def _verdict_pill(verdict: Any) -> str:
    if verdict is None or str(verdict).strip() == "":
        return '<span class="pill none">unreviewed</span>'
    text = str(verdict)
    css = text if text in ("good", "wrong") else ""
    return f'<span class="pill {css}">{_esc(text)}</span>'


def _metric_table_html(rows: Sequence[GroupStats]) -> str:
    headers = "".join(
        f'<th class="{"txt" if align == "<" else "num"}">{_esc(header)}</th>'
        for header, align in _METRIC_COLUMNS
    )
    body = []
    for stats in rows:
        cells = _metric_row(stats)
        swatch_index = 1
        if stats.model_tier in TIER_ORDER:
            swatch_index = TIER_ORDER.index(stats.model_tier) + 1
        first = (
            f'<td class="txt"><span class="swatch" '
            f'style="background:var(--series-{swatch_index})"></span>{_esc(cells[0])}</td>'
        )
        rest = "".join(f'<td class="num">{_esc(cell)}</td>' for cell in cells[1:])
        body.append(f"<tr>{first}{rest}</tr>")
    return (
        f"<table><thead><tr>{headers}</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _runs_table_html(report: BenchmarkReport) -> str:
    """Every individual run, filterable and sortable, output behind <details>.

    This is the part of the report that matters most and looks least impressive.
    The benchmark's *Scoring* section is explicit that 300 cards per tier is
    small enough for a maintainer to read every output directly, and that the
    aggregates exist to make that read fast rather than to replace it -- so the
    aggregates above are the index, and this is the document.

    Prompts and responses are collapsed rather than omitted: a 900-row page with
    every system prompt expanded is unreadable, and a page without them can't be
    used to review anything. `<details>` keeps both, at the cost of one click.
    """
    if not report.runs:
        return '<p class="empty">No runs recorded.</p>'

    bucket_options = "".join(
        f'<option value="{_esc(bucket)}">{_esc(bucket)}</option>'
        for bucket in report.buckets
    )
    tier_options = "".join(
        f'<option value="{_esc(tier)}">{_esc(_tier_label(tier))}</option>'
        for tier in report.tiers
    )
    verdict_options = "".join(
        f'<option value="{_esc(verdict)}">{_esc(verdict)}</option>'
        for verdict in sorted(
            {
                str(row["human_verdict"])
                for row in report.runs
                if row["human_verdict"] not in (None, "")
            }
        )
    )

    controls = (
        '<div class="controls">'
        f'<label>bucket <select data-filter="bucket"><option value="">all</option>{bucket_options}</select></label>'
        f'<label>tier <select data-filter="tier"><option value="">all</option>{tier_options}</select></label>'
        f'<label>verdict <select data-filter="verdict"><option value="">all</option>{verdict_options}</select></label>'
        '<label>schema <select data-filter="valid"><option value="">all</option>'
        '<option value="1">valid</option><option value="0">invalid</option></select></label>'
        '<label>search <input data-filter="q" type="search" placeholder="card, model, response text" /></label>'
        '<span id="runcount"></span>'
        "</div>"
    )

    columns = (
        ("card", "card", "txt", "card"),
        ("bucket", "bucket", "txt", "bucket"),
        ("tier", "tier", "txt", "tier"),
        ("model", None, "txt", None),
        ("valid", "valid", "num", "num"),
        ("latency ms", "latency", "num", "num"),
        ("cost", "cost", "num", "num"),
        ("in", "intok", "num", "num"),
        ("out", "outtok", "num", "num"),
        ("run at", "runat", "txt", "txt"),
        ("verdict", "verdict", "txt", "txt"),
    )
    header_cells = []
    for label, sort_key, css, sort_type in columns:
        if sort_key is None:
            header_cells.append(f'<th class="{css}">{_esc(label)}</th>')
        else:
            header_cells.append(
                f'<th class="{css}" data-sort="{sort_key}" data-type="{sort_type}" '
                f'scope="col">{_esc(label)}</th>'
            )

    bodies = []
    for row in report.runs:
        verdict = row["human_verdict"]
        # The searchable blob is lowercased here rather than in JS so the filter
        # stays a substring test over an already-escaped attribute.
        search = " ".join(
            str(part or "")
            for part in (
                row["card_name"],
                row["model_name"],
                row["bucket"],
                row["model_tier"],
                verdict,
                row["human_notes"],
                row["raw_response"],
            )
        ).lower()
        attributes = (
            f'data-bucket="{_esc(row["bucket"])}" '
            f'data-tier="{_esc(row["model_tier"])}" '
            f'data-verdict="{_esc("" if verdict is None else verdict)}" '
            f'data-valid="{1 if row["schema_valid"] else 0}" '
            f'data-card="{_esc(row["card_name"])}" '
            f'data-latency="{_esc(row["latency_ms"] if row["latency_ms"] is not None else "")}" '
            f'data-cost="{_esc(row["cost_usd"] if row["cost_usd"] is not None else "")}" '
            f'data-intok="{_esc(row["input_tokens"] if row["input_tokens"] is not None else "")}" '
            f'data-outtok="{_esc(row["output_tokens"] if row["output_tokens"] is not None else "")}" '
            f'data-runat="{_esc(row["run_at"])}" '
            f'data-search="{_esc(search)}"'
        )

        main_cells = (
            f'<td class="txt">{_esc(row["card_name"])}</td>'
            f'<td class="txt">{_esc(row["bucket"])}</td>'
            f'<td class="txt">{_esc(_run_tier_label(row))}</td>'
            f'<td class="txt">{_esc(row["model_name"])}</td>'
            f'<td class="num">{"yes" if row["schema_valid"] else "<b>no</b>"}</td>'
            f'<td class="num">{_esc(_ms(_num(row, "latency_ms")))}</td>'
            f'<td class="num">{_esc(_money(_num(row, "cost_usd")))}</td>'
            f'<td class="num">{_esc(_count(_num(row, "input_tokens")))}</td>'
            f'<td class="num">{_esc(_count(_num(row, "output_tokens")))}</td>'
            f'<td class="txt">{_esc(row["run_at"])}</td>'
            f'<td class="txt">{_verdict_pill(verdict)}</td>'
        )

        details = [
            _details("raw response", row["raw_response"], open_by_default=not row["schema_valid"]),
            _details("parsed output", row["parsed_output"]),
            _details("user prompts (card facts; pass two's anchor ladders)", row["user_prompt"]),
            _details("system prompts (taxonomy + calibration; rating rubric)", row["system_prompt"]),
        ]
        if row["human_notes"]:
            details.insert(0, _details("human notes", row["human_notes"], open_by_default=True))

        bodies.append(
            f'<tbody class="run" {attributes}>'
            f"<tr>{main_cells}</tr>"
            f'<tr class="detail"><td class="txt" colspan="{len(columns)}">'
            + "".join(details)
            + "</td></tr></tbody>"
        )

    return (
        controls
        + '<div class="panel"><table class="runs" id="runs"><thead><tr>'
        + "".join(header_cells)
        + "</tr></thead>"
        + "".join(bodies)
        + "</table></div>"
    )


def _details(summary: str, body: Any, *, open_by_default: bool = False) -> str:
    if body is None or str(body).strip() == "":
        return ""
    attribute = " open" if open_by_default else ""
    return (
        f"<details{attribute}><summary>{_esc(summary)}</summary>"
        f"<pre>{_esc(body)}</pre></details>"
    )


def _tab_label(view: SessionView) -> str:
    """What a session's chip says: enough to pick it out of a row of them."""
    buckets = "+".join(view.buckets) or "?"
    label = f"{view.session_id} &middot; {_esc(buckets)}&times;{view.cards_per_bucket}"
    if view.label:
        label += f" &middot; {_esc(view.label)}"
    if view.is_synthetic:
        label += _esc(SYNTHETIC_MARKER)
    return label


def _tabs_html(report: BenchmarkReport) -> str:
    """The tab bar: Overview, then every session, newest first.

    Buttons rather than links, and `aria-selected` rather than a class alone,
    so the bar is operable and announced as what it is. The newest session is
    the default panel: a maintainer opening this page almost always just ran
    that one, and the pooled Overview is the view that needs a deliberate
    click, not the other way round.
    """
    chips = [
        '<button type="button" class="tab" role="tab" data-tab="overview" '
        + ("" if report.sessions else 'data-default="1" ')
        + 'aria-selected="false">Overview</button>'
    ]
    for index, view in enumerate(report.sessions):
        chips.append(
            f'<button type="button" class="tab" role="tab" '
            f'data-tab="session-{_esc(view.session_id)}" '
            + ('data-default="1" ' if index == 0 else "")
            + f'aria-selected="false">{_tab_label(view)}</button>'
        )
    return f'<div class="tabs" role="tablist">{"".join(chips)}</div>'


def _session_meta_html(view: SessionView) -> str:
    """The provenance strip: everything that decides whether to trust the tab.

    Deliberately above the numbers. A reader who scrolls past this has seen
    which models answered, how many cards, under which prompt, and whether the
    session is complete — the four facts that make the rest of the panel mean
    anything.
    """
    rows = [
        ("session", f"{view.session_id}" + (f" ({view.label})" if view.label else "")),
        ("status", view.status + (" -- PAIRED" if view.is_paired else " -- not paired")),
        (
            "cards",
            f"{len(view.cards):,} from {'+'.join(view.buckets) or '?'} "
            f"({view.cards_per_bucket} per bucket), drawn with seed {view.draw_seed}",
        ),
        (
            "tiers",
            " then ".join(
                f"{_tier_label(tier)} = {view.models.get(tier, '?')}"
                for tier in view.tiers
            ),
        ),
        (
            "decoding",
            f"num_ctx {view.num_ctx:,} &middot; temperature {view.temperature} "
            f"&middot; seed {view.model_seed}",
        ),
        ("prompts", f"sha256 {view.prompt_sha256[:16]}"),
        ("grammars", f"sha256 {view.schemas_sha256[:16]}"),
        (
            "sample",
            f"{view.sample_path or '(unrecorded)'} (seed {view.sample_seed})",
        ),
        ("ran", f"{view.started_at} &rarr; {view.finished_at or '(unfinished)'}"),
    ]
    cells = "".join(
        f'<div class="meta-item"><span class="meta-key">{_esc(key)}</span>'
        f"<span>{value}</span></div>"
        for key, value in rows
    )
    warning = ""
    if not view.is_paired:
        warning = (
            '<p class="note"><strong>This session is not a like-for-like '
            f"comparison.</strong> {view.missing_cells:,} of its "
            f"{view.expected_calls:,} (card, tier) cells have no row, so the "
            "tiers did not answer the same cards. Finish it with "
            f"<code>run --resume {_esc(view.session_id)}</code> before reading "
            "the win rate below.</p>"
        )
    return f'<div class="panel"><div class="metagrid">{cells}</div>{warning}</div>'


def _win_rate_html(view: SessionView) -> str:
    """The session's verdict, with the uncertainty it has to be read with."""
    tally = view.wins
    if tally.decided == 0:
        return (
            '<div class="unavailable">No cards in this session have a pick yet. '
            "Pick a winner per card in the table below, copy the result out, and "
            f"record it with <code>pair-verdict --from picks.json</code>. "
            f"{len(view.cards):,} card(s) are waiting.</div>"
        )

    tiles = []
    for tier in view.tiers:
        rate = tally.rate(tier)
        interval = tally.interval(tier)
        hours = view.projected_corpus_hours(tier)
        detail = (
            f"95% CI {_pct(interval[0], digits=0)}-{_pct(interval[1], digits=0)}"
            if interval
            else DASH
        )
        corpus = (
            f"{hours:,.0f}h for {CORPUS_CARD_COUNT:,} cards"
            if hours is not None
            else "corpus cost unknown"
        )
        tiles.append(
            f'<div class="tile"><div class="value">{_esc(_pct(rate, digits=0))}</div>'
            f'<div class="label">{_esc(_tier_label(tier))} wins</div>'
            f'<div class="sublabel">{_esc(detail)}<br />{_esc(corpus)}</div></div>'
        )

    for outcome in VERDICT_NO_WINNER:
        tiles.append(
            f'<div class="tile"><div class="value">{tally.counts.get(outcome, 0):,}</div>'
            f'<div class="label">{_esc(outcome.replace("_", " "))}</div></div>'
        )
    tiles.append(
        f'<div class="tile"><div class="value">{tally.decided:,}'
        f'<span class="of">/{len(view.cards):,}</span></div>'
        '<div class="label">cards decided</div></div>'
    )

    note = (
        '<p class="note"><strong>Read the interval, not the percentage.</strong> '
        f"This session decided {tally.decided:,} card(s) (tally "
        f"{_esc(tally.tally)} in tier order, then tie, then both-bad). At this "
        "size a win rate carries roughly &plusmn;18 points of 95% uncertainty, "
        "so a near-even split is noise and only a lopsided one is a finding. "
        "Note also that the slower tier has to win by enough to justify its "
        "projected corpus hours above.</p>"
    )
    return f'<div class="tiles">{"".join(tiles)}</div>{note}'


def _run_cell_html(run: Run | None) -> str:
    """One tier's answer to one card, as a compact cell."""
    if run is None:
        return '<td class="num missing">no row</td>'
    valid = "yes" if run["schema_valid"] else "<b>INVALID</b>"
    latency = _ms(_num(run, "latency_ms"))
    verdict = run["human_verdict"]
    verdict_html = (
        f' {_verdict_pill(verdict)}' if verdict not in (None, "") else ""
    )
    return f'<td class="num">{valid} &middot; {_esc(latency)} ms{verdict_html}</td>'


def _agreement_cell_html(card: CardComparison) -> str:
    if not card.complete:
        return '<td class="num missing">--</td>'
    agreement = card.agreement
    if agreement is None or agreement.score is None:
        return '<td class="num missing">unknown</td>'
    detail = " &middot; ".join(
        f"{field[:4]} {_pct(value, digits=0)}"
        for field, value in agreement.set_jaccard.items()
        if value is not None
    )
    return (
        f'<td class="num"><span title="{_esc(detail)}">'
        f"{_esc(_pct(agreement.score, digits=0))}</span></td>"
    )


def _pick_cell_html(view: SessionView, card: CardComparison) -> str:
    """The radio group a maintainer clicks, plus a notes box.

    Carries the run ids in a `data-` attribute so the copy-out can point a
    verdict at the exact rows it was formed against. Ids and tier names only —
    no model-written text ever reaches an attribute the script reads, which is
    the same rule the filter script follows.
    """
    name = f"pick-{view.session_id}-{card.oracle_id}"
    options = [(tier, _tier_label(tier)) for tier in view.tiers]
    options += [(outcome, outcome.replace("_", " ")) for outcome in VERDICT_NO_WINNER]
    radios = "".join(
        f'<label class="pick"><input type="radio" name="{_esc(name)}" '
        f'value="{_esc(value)}"'
        + (" checked" if card.winner == value else "")
        + f" />{_esc(label)}</label>"
        for value, label in options
    )
    runs_json = json.dumps(
        {tier: int(run["id"]) for tier, run in card.runs.items()}, sort_keys=True
    )
    notes = _esc(card.pair_notes or "")
    return (
        f'<td class="txt picks" data-oracle="{_esc(card.oracle_id)}" '
        f'data-card="{_esc(card.name)}" data-runs="{_esc(runs_json)}">'
        f"{radios}"
        f'<input class="picknote" type="text" placeholder="notes" '
        f'value="{notes}" />'
        "</td>"
    )


def _comparison_table_html(view: SessionView) -> str:
    """The session's cards, every tier's answer side by side.

    **Sorted by disagreement, descending**, and that is the point of the view.
    On a 30-card session the cards where the tiers said different things are
    where a human read changes a conclusion; the ones they agree on mostly
    confirm each other. Incomplete cards sort above everything, because a
    missing cell is a hole in the comparison rather than a result in it.
    """
    if not view.cards:
        return '<p class="empty">This session has no cards recorded.</p>'

    headers = ['<th class="txt">card</th>', '<th class="txt">bucket</th>']
    headers += [
        f'<th class="num">{_esc(_tier_label(tier))}</th>' for tier in view.tiers
    ]
    headers += ['<th class="num">agree</th>', '<th class="txt">pick</th>']

    bodies = []
    for card in view.disagreement_order:
        cells = [
            f'<td class="txt">{_esc(card.name)}</td>',
            f'<td class="txt">{_esc(card.bucket)}</td>',
        ]
        cells += [_run_cell_html(card.runs.get(tier)) for tier in view.tiers]
        cells.append(_agreement_cell_html(card))
        cells.append(_pick_cell_html(view, card))

        details = []
        for tier in view.tiers:
            run = card.runs.get(tier)
            if run is None:
                continue
            details.append(
                '<div class="side">'
                f"<h4>{_esc(_tier_label(tier))} &middot; "
                f'{_esc(run["model_name"])}</h4>'
                + _details(
                    "raw response",
                    run["raw_response"],
                    open_by_default=not run["schema_valid"],
                )
                + _details("parsed output", run["parsed_output"])
                + (
                    _details("human notes", run["human_notes"], open_by_default=True)
                    if run["human_notes"]
                    else ""
                )
                + "</div>"
            )
        any_run = next(iter(card.runs.values()), None)
        if any_run is not None:
            details.append(
                '<div class="side wide">'
                + _details("user prompt (card facts)", any_run["user_prompt"])
                + "</div>"
            )

        span = len(view.tiers) + 4
        bodies.append(
            f'<tbody class="card-row">'
            f'<tr>{"".join(cells)}</tr>'
            f'<tr class="detail"><td class="txt" colspan="{span}">'
            f'<div class="sides">{"".join(details)}</div>'
            "</td></tr></tbody>"
        )

    return (
        '<div class="panel"><table class="compare">'
        f'<thead><tr>{"".join(headers)}</tr></thead>'
        + "".join(bodies)
        + "</table></div>"
    )


def _copy_box_html(view: SessionView) -> str:
    """The hand-off: picks out of the page and into the store.

    The page never writes to the database, and should not be able to — the
    reporting layer is read-only by design (`store.py`'s split), and a browser
    with a write path to measured evidence is a worse trade than one copy-paste
    step.
    """
    return (
        '<div class="panel copybox">'
        f'<button type="button" class="action" data-copy="{_esc(view.session_id)}">'
        "Collect picks</button>"
        '<span class="note">Click, then copy the JSON below and run '
        f"<code>python -m knowledge_pipeline.benchmark pair-verdict --from "
        f"picks.json</code></span>"
        f'<textarea class="copyout" data-copyout="{_esc(view.session_id)}" rows="6" '
        'readonly placeholder="Picks appear here."></textarea>'
        "</div>"
    )


def _session_panel_html(view: SessionView) -> str:
    """One tab's contents: provenance, verdict, cards, then per-tier stats."""
    return (
        f'<section class="panel-body session-panel" '
        f'id="panel-session-{_esc(view.session_id)}" role="tabpanel" hidden>'
        f"<h2>Session {_esc(view.session_id)}</h2>"
        '<p class="sub">One card set, every tier, in serial succession -- the '
        "only scope in which &quot;which model is better&quot; is a well-posed "
        "question.</p>"
        + _session_meta_html(view)
        + "<h3>Which tier won</h3>"
        + _win_rate_html(view)
        + "<h3>Card by card</h3>"
        '<p class="sub">Sorted by disagreement: the cards the tiers answered '
        "differently are the ones worth reading. Expand a row for both raw "
        "responses.</p>"
        + _comparison_table_html(view)
        + _copy_box_html(view)
        + "<h3>Per tier, this session only</h3>"
        f'<div class="panel">{_metric_table_html([view.by_tier[t] for t in view.tiers])}</div>'
        "</section>"
    )


def _per_pass_html(report: BenchmarkReport) -> str:
    """The one table on the page that counts calls rather than cards.

    Everything else merges production's two calls into one answer per card,
    which is right for every question about *quality* and wrong for every
    question about *speed*: the two passes send prompts of very different sizes
    and generate answers of very different lengths. Rates come from the phase
    timers rather than from total latency, for the reason the blended-rate
    mistake in `docs/lessons-learned.md` records.
    """
    if not report.per_pass:
        return '<p class="empty">No calls recorded.</p>'

    header = (
        "<thead><tr>"
        '<th class="txt">tier</th><th class="txt">pass</th>'
        '<th class="num">calls</th><th class="num">schema valid</th>'
        '<th class="num">p50 latency</th><th class="num">in tok</th>'
        '<th class="num">out tok</th><th class="num">prefill tok/s</th>'
        '<th class="num">decode tok/s</th>'
        "</tr></thead>"
    )
    body: list[str] = []
    for tier in report.tiers:
        for pass_name in ("one", "two"):
            stats = report.per_pass.get((tier, pass_name))
            if stats is None:
                continue
            body.append(
                "<tr>"
                f'<td class="txt">{_esc(_tier_label(tier))}</td>'
                f'<td class="txt">{_esc(pass_name)}</td>'
                f'<td class="num">{stats.calls:,}</td>'
                f'<td class="num">{_esc(_pct(stats.schema_valid_rate, digits=0))}</td>'
                f'<td class="num">{_esc(_ms(stats.latency_p50))}</td>'
                f'<td class="num">{_esc(_count(stats.input_tokens_mean))}</td>'
                f'<td class="num">{_esc(_count(stats.output_tokens_mean))}</td>'
                f'<td class="num">'
                + (
                    DASH
                    if stats.prefill_tokens_per_second is None
                    else f"{stats.prefill_tokens_per_second:,.0f}"
                )
                + "</td>"
                f'<td class="num">'
                + (
                    DASH
                    if stats.decode_tokens_per_second is None
                    else f"{stats.decode_tokens_per_second:,.1f}"
                )
                + "</td></tr>"
            )

    return (
        '<p class="sub">Call-level, and the only table here not merged across '
        "the two passes. Pass one prefills the taxonomy to answer seven fields; "
        "pass two prefills a short rubric plus the ladders pass one selected to "
        "answer two. One blended rate would describe neither.</p>"
        f'<div class="panel"><table>{header}<tbody>'
        + "".join(body)
        + "</tbody></table></div>"
    )


def render_html(report: BenchmarkReport) -> str:
    """The shareable artifact: one self-contained file, readable offline.

    **Tabbed by session**, with everything cross-session on an Overview tab.
    That split is not a layout preference: a session is the only scope whose
    tiers are known to have answered the same cards under the same prompt, so
    it is the only scope where a comparison is sound. Pooling sessions is still
    offered -- a prompt revision or a new model release produces a second
    session, and the trend across them is worth seeing -- but it is a
    deliberate click away, and the Overview says out loud when the sessions it
    pools do not share a prompt.

    Within the Overview, the order is the same as the text report and for the
    same reason -- stat tiles, then the per-bucket comparison and its charts,
    then the hard-bucket gap, then the roll-up, then every individual run. A
    reader who stops after the first screen should have seen the comparison
    that decides something, not an overall average.
    """
    # Marked labels, so a chart lifted out of the page as an image still
    # says which bars are fabricated.
    tier_labels = [_group_label(report.by_tier[tier]) for tier in report.tiers]
    overall = report.overall
    generated = f"{report.generated_at:%Y-%m-%d %H:%M} UTC"
    source = _esc(report.db_path) if report.db_path else "rows supplied directly"

    tiles = [
        ("runs logged", f"{overall.runs:,}"),
        ("cards covered", f"{overall.cards:,}"),
        ("schema valid", _pct(overall.schema_valid_rate, digits=0)),
        ("human-reviewed", _pct(overall.verdicts.review_coverage, digits=0)),
        ("total cost", _money(overall.cost_total, digits=2)),
        ("p50 latency", f"{_ms(overall.latency_p50)} ms"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="value">{_esc(value)}</div>'
        f'<div class="label">{_esc(label)}</div></div>'
        for label, value in tiles
    )

    sections: list[str] = []

    if overall.runs == 0:
        sections.append(
            '<p class="empty">No runs recorded in this database. Run the '
            "benchmark, or seed demo data, and regenerate this report.</p>"
        )
    else:
        sections.append(
            "<h2>Per bucket, tiers side by side</h2>"
            '<p class="sub">The primary view. What decides the escalation rule is '
            "the gap between tiers <em>within</em> a difficulty bucket -- above all "
            "the hard one -- not the average across all 300 cards.</p>"
        )
        sections.append(_charts_html(report, tier_labels))
        for bucket in report.buckets:
            sections.append(
                f"<h3>{_esc(bucket)} bucket</h3>"
                f'<div class="panel">{_metric_table_html(report.bucket_view(bucket))}</div>'
            )

        sections.append(_gap_html(report))

        sections.append(
            "<h2>Per tier, all buckets</h2>"
            '<p class="sub">Context only. Two thirds of the sample is easy and '
            "medium, so an overall average mostly measures those.</p>"
            f'<div class="panel">{_metric_table_html([report.by_tier[tier] for tier in report.tiers])}</div>'
        )

    sections.append("<h2>Per pass</h2>" + _per_pass_html(report))
    sections.append(
        "<h2>Game stage calibration</h2>" + _timing_html(report)
    )
    sections.append("<h2>Accuracy vs. ground truth</h2>" + _accuracy_html(report))
    sections.append(
        "<h2>Every run</h2>"
        '<p class="sub">Scoring is human review: a session is small enough to '
        "read every output directly. Prompts and responses are collapsed, not "
        "omitted.</p>" + _runs_table_html(report)
    )

    overview = (
        '<section class="panel-body" id="panel-overview" role="tabpanel" hidden>'
        "<h2>Across every session</h2>"
        '<p class="sub">Pooled. Sound only to the extent the sessions below '
        "are comparable -- same prompt, same decoding settings -- which is "
        "what the fingerprint check reports.</p>"
        + _prompt_pooling_html(report)
        + _sessions_table_html(report)
        + f'<div class="tiles">{tiles_html}</div>'
        + "".join(sections)
        + "</section>"
    )

    panels = [overview] + [_session_panel_html(view) for view in report.sessions]

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        "<title>Tome metadata benchmark</title>"
        f"<style>{_CSS}</style></head><body><main>"
        "<h1>Tome metadata benchmark</h1>"
        '<p class="sub">Local tiers compared on one card set at a time. Each '
        "tab is one session: the same cards, every tier, in serial "
        "succession.</p>"
        f'<p class="meta">source <code>{source}</code> &middot; generated '
        f"{_esc(generated)} &middot; {len(report.sessions):,} session(s)</p>"
        + _synthetic_banner_html(report)
        + _tabs_html(report)
        + "".join(panels)
        + f"</main><script>{_JS}</script></body></html>\n"
    )


def _prompt_pooling_html(report: BenchmarkReport) -> str:
    """Say when the pooled tables are mixing runs made against two prompts."""
    if not report.pools_mixed_prompts:
        return ""
    fingerprints = ", ".join(value[:12] for value in report.prompt_fingerprints)
    return (
        '<div class="synthetic">'
        f"<strong>These sessions ran against {len(report.prompt_fingerprints)} "
        "different system prompts.</strong>"
        "Every pooled number on this tab therefore averages answers to "
        "different questions: the closed taxonomy, the rating rubric and the "
        "anchor ladders all live in that block, so a rating made under one "
        "version is not on the same scale as a rating made under another. "
        f"Prompt fingerprints seen: <code>{_esc(fingerprints)}</code>. Compare "
        "the session tabs individually instead."
        "</div>"
    )


def _sessions_table_html(report: BenchmarkReport) -> str:
    """Every session as one row: what it measured and how it landed."""
    if not report.sessions:
        return (
            '<div class="unavailable">No sessions recorded. '
            "<code>run --bucket hard --cards 30</code> creates one.</div>"
        )

    head = (
        "<tr><th class='txt'>session</th><th class='txt'>buckets</th>"
        "<th class='num'>cards</th><th class='txt'>tiers</th>"
        "<th class='txt'>status</th><th class='num'>decided</th>"
        "<th class='txt'>tally</th><th class='num'>valid</th>"
        "<th class='num'>p50 ms</th><th class='txt'>prompt</th>"
        "<th class='txt'>ran</th></tr>"
    )
    rows = []
    for view in report.sessions:
        tally = view.wins
        valid = " / ".join(
            _pct(view.by_tier[tier].schema_valid_rate, digits=0) for tier in view.tiers
        )
        p50 = " / ".join(
            _ms(view.by_tier[tier].latency_p50) for tier in view.tiers
        )
        label = view.session_id + (f" ({view.label})" if view.label else "")
        rows.append(
            f"<tr><td class='txt'>{_esc(label)}</td>"
            f"<td class='txt'>{_esc('+'.join(view.buckets))}</td>"
            f"<td class='num'>{len(view.cards):,}</td>"
            f"<td class='txt'>{_esc(' then '.join(_tier_label(t) for t in view.tiers))}</td>"
            f"<td class='txt'>{_esc(view.status)}"
            + ("" if view.is_paired else " <b>unpaired</b>")
            + "</td>"
            f"<td class='num'>{tally.decided:,}</td>"
            f"<td class='txt'>{_esc(tally.tally)}</td>"
            f"<td class='num'>{_esc(valid)}</td>"
            f"<td class='num'>{_esc(p50)}</td>"
            f"<td class='txt'><code>{_esc(view.prompt_sha256[:8])}</code></td>"
            f"<td class='txt'>{_esc(view.started_at)}</td></tr>"
        )
    return (
        f'<div class="panel"><table><thead>{head}</thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _synthetic_banner_html(report: BenchmarkReport) -> str:
    """The HTML twin of `_synthetic_banner`. Empty string if the store is clean."""
    if not report.has_synthetic:
        return ""

    if report.is_fully_synthetic:
        headline = (
            f"All {report.total_runs:,} rows are synthetic demo data -- "
            "every number on this page is fabricated."
        )
    else:
        headline = (
            f"{report.synthetic_runs:,} of {report.total_runs:,} rows are "
            "synthetic demo data, mixed in with measured runs."
        )

    return (
        '<div class="synthetic">'
        f"<strong>{_esc(headline)}</strong>"
        "These rows were written by <code>seed-demo</code> to exercise this "
        "report before the benchmark existed. Latencies, token counts and "
        "verdicts were invented; no model was called. Delete the store (or "
        "pass <code>--db</code>) and run the benchmark for real numbers. "
        f"Affected rows are marked <code>{_esc(SYNTHETIC_MARKER.strip())}</code>."
        "</div>"
    )


def _charts_html(report: BenchmarkReport, tier_labels: Sequence[str]) -> str:
    """Three charts: quality, then the two cost axes that quality is traded against."""
    buckets = list(report.buckets)

    validity_values: dict[tuple[str, str], float | None] = {}
    validity_labels: dict[tuple[str, str], str] = {}
    latency_values: dict[tuple[str, str], float | None] = {}
    latency_labels: dict[tuple[str, str], str] = {}
    cost_values: dict[tuple[str, str], float | None] = {}
    cost_labels: dict[tuple[str, str], str] = {}

    for bucket in buckets:
        for tier, label in zip(report.tiers, tier_labels):
            stats = report.by_bucket_tier[(bucket, tier)]
            rate = stats.schema_valid_rate
            validity_values[(bucket, label)] = None if rate is None else rate * 100
            validity_labels[(bucket, label)] = _pct(rate, digits=0)
            latency_values[(bucket, label)] = stats.latency_p50
            latency_labels[(bucket, label)] = _ms(stats.latency_p50)
            cost_values[(bucket, label)] = stats.cost_mean
            cost_labels[(bucket, label)] = _money(stats.cost_mean)

    quality = _chart_panel(
        "Schema validity by bucket (%)",
        "The one quality axis that needs no ground truth: did the output parse as a "
        "valid CardMetadataOutput at all. A code-tuned model that ignores the closed "
        "vocabulary fails here regardless of how good its judgment is.",
        tier_labels,
        _grouped_bar_svg(
            groups=buckets,
            series=list(tier_labels),
            values=validity_values,
            labels=validity_labels,
            axis_format=lambda value: f"{value:.0f}%",
            axis_max=100.0,
        ),
    )
    latency = _chart_panel(
        "Median latency by bucket (ms)",
        "For the local tiers there is no bill, so wall-clock time is their cost axis. "
        "Charted separately from money rather than on a second y-axis.",
        tier_labels,
        _grouped_bar_svg(
            groups=buckets,
            series=list(tier_labels),
            values=latency_values,
            labels=latency_labels,
            axis_format=lambda value: f"{value:,.0f}",
            height=220,
        ),
    )
    cost = _chart_panel(
        "Mean billed cost per run (USD)",
        "Zero for the local tiers by construction. Multiply the frontier bar by the "
        "share of ~31,830 cards an escalation rule would route to it to price a "
        "production run.",
        tier_labels,
        _grouped_bar_svg(
            groups=buckets,
            series=list(tier_labels),
            values=cost_values,
            labels=cost_labels,
            axis_format=lambda value: f"${value:.4f}".rstrip("0").rstrip(".") or "$0",
            height=220,
        ),
    )
    return quality + f'<div class="charts">{latency}{cost}</div>'


def _gap_html(report: BenchmarkReport) -> str:
    heading = (
        "<h2>The deciding numbers: hard bucket vs frontier</h2>"
        '<p class="sub">Whether a local tier is viable at all, and what an '
        "escalation rule has to buy, are both argued from this table.</p>"
    )
    if "hard" not in report.buckets:
        return heading + '<div class="unavailable">No hard-bucket runs recorded yet.</div>'

    gaps = report.gaps("hard")
    if not gaps:
        return heading + (
            '<div class="unavailable">No frontier baseline in the hard bucket. '
            "Run the frontier tier before comparing -- an absent baseline is not a "
            "zero baseline.</div>"
        )

    rows = []
    for gap in gaps:
        rows.append(
            "<tr>"
            f'<td class="txt">'
            f'{_esc(_group_label(report.by_bucket_tier[("hard", gap.model_tier)]))}</td>'
            f'<td class="num">{_esc(_signed_pct(gap.schema_valid_delta, digits=0))}</td>'
            f'<td class="num">{_esc(_signed_pct(gap.good_rate_delta, digits=0))}</td>'
            f'<td class="num">{_esc(_ms(gap.latency_p50))}</td>'
            f'<td class="num">{_esc(DASH if gap.latency_ratio is None else f"{gap.latency_ratio:.2f}x")}</td>'
            f'<td class="num">{_esc(_money(gap.cost_mean))}</td>'
            f'<td class="num">{_esc(_money(gap.cost_saved_per_run))}</td>'
            "</tr>"
        )

    baseline = report.by_bucket_tier[("hard", BASELINE_TIER)]
    caveat = "".join(
        f'<p class="note">{_esc(line)}</p>' for line in _review_caveats(report, "hard")
    )

    return (
        heading
        + '<div class="panel"><table><thead><tr>'
        '<th class="txt">tier</th><th class="num">&Delta; schema valid</th>'
        '<th class="num">&Delta; good verdicts</th><th class="num">p50 ms</th>'
        '<th class="num">vs baseline</th><th class="num">$/run</th>'
        '<th class="num">$ saved/run</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f'<p class="callout">Baseline {_esc(_group_label(baseline))}: '
        f"{_esc(_pct(baseline.schema_valid_rate, digits=0))} schema-valid, "
        f"{_esc(_pct(baseline.verdicts.good_rate, digits=0))} good of "
        f"{baseline.verdicts.reviewed:,} reviewed, "
        f"{_esc(_ms(baseline.latency_p50))} ms p50, "
        f"{_esc(_money(baseline.cost_mean))} per run.</p>" + caveat + "</div>"
    )


def _accuracy_html(report: BenchmarkReport) -> str:
    if not report.ground_truth_available:
        return (
            '<div class="unavailable"><strong>Unavailable.</strong> Role, theme and '
            "synergy F1, <code>game_stage</code> MAE and <code>power_rating</code> MAE "
            "all need the hand-labeled ground truth described in "
            "<code>docs/benchmarking-and-testing.md</code>, and no such label set "
            "exists yet. These metrics are <em>absent</em>, not zero. They are "
            "deliberately not approximated by scoring the local tiers against the "
            "frontier tier's output: that would measure agreement with Claude while "
            "reading as accuracy, which is the specific mistake this benchmark "
            "exists to avoid.</div>"
        )

    rows = []
    for bucket in report.buckets:
        for stats in report.bucket_view(bucket):
            scores = stats.accuracy
            if scores is None or stats.is_empty:
                continue

            def number(value: float | None, digits: int = 3) -> str:
                return DASH if value is None else f"{value:.{digits}f}"

            rows.append(
                "<tr>"
                f'<td class="txt">{_esc(bucket)}</td>'
                f'<td class="txt">{_esc(_group_label(stats))}</td>'
                f'<td class="num">{_esc(number(scores.role_f1))}</td>'
                f'<td class="num">{_esc(number(scores.theme_f1))}</td>'
                f'<td class="num">{_esc(number(scores.synergy_f1))}</td>'
                f'<td class="num">{_esc(number(scores.game_stage_mae, 2))}</td>'
                f'<td class="num">{_esc(number(scores.power_rating_mae, 2))}</td>'
                f'<td class="num">{scores.scored:,}/{scores.scored + scores.unscorable:,}</td>'
                "</tr>"
            )

    return (
        '<div class="panel"><table><thead><tr>'
        '<th class="txt">bucket</th><th class="txt">tier</th>'
        '<th class="num">role F1</th><th class="num">theme F1</th>'
        '<th class="num">synergy F1</th><th class="num">stage MAE</th>'
        '<th class="num">power MAE</th><th class="num">scored</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _timing_html(report: BenchmarkReport) -> str:
    """The `game_stage` calibration panel.

    Carries the exception cards by name under the table. They are excluded
    from the correlation, and a number that silently drops rows should say
    which ones -- the same rule the cap-logging note in `CLAUDE.md` states.
    """
    rows = []
    named: list[str] = []
    for bucket in report.buckets:
        for stats in report.bucket_view(bucket):
            scores = stats.timing
            if scores is None or stats.is_empty:
                continue
            named.extend(scores.exception_cards)
            corr = (
                DASH
                if scores.castability_corr is None
                else f"{scores.castability_corr:+.3f}"
            )
            shape_cells = "".join(
                f'<td class="num">{_esc(_pct(scores.shape_rate(shape), digits=0))}</td>'
                for shape in SHAPE_ORDER
            )
            rows.append(
                "<tr>"
                f'<td class="txt">{_esc(bucket)}</td>'
                f'<td class="txt">{_esc(_group_label(stats))}</td>'
                f'<td class="num">{_esc(corr)}</td>'
                f'<td class="num">'
                f'{DASH if scores.early_mean is None else f"{scores.early_mean:.2f}"}'
                "</td>"
                f'<td class="num">{scores.plain_runs:,}</td>'
                f'<td class="num">{scores.exception_runs:,}</td>'
                f"{shape_cells}"
                "</tr>"
            )

    if not rows:
        return '<div class="panel"><p>No parsed game_stage values recorded yet.</p></div>'

    shape_headers = "".join(f'<th class="num">{_esc(s)}</th>' for s in SHAPE_ORDER)
    excluded = ""
    if named:
        unique = sorted(set(named))
        excluded = (
            "<p><strong>Excluded from the correlation</strong> "
            f"({len(unique)} cards whose mana value is not their cost to "
            f"function): {_esc(', '.join(unique))}</p>"
        )

    return (
        '<div class="panel"><table><thead><tr>'
        '<th class="txt">bucket</th><th class="txt">tier</th>'
        '<th class="num">castability r</th><th class="num">early mean</th>'
        '<th class="num">plain</th><th class="num">excepted</th>'
        f"{shape_headers}"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f"<p>{_esc(_TIMING_NOTE.replace(chr(10), ' ').replace('    ', ''))}</p>"
        f"{excluded}</div>"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_store(
    *,
    db_path: Path | None = None,
    session_id: str | None = None,
    bucket: str | None = None,
    model_tier: str | None = None,
) -> tuple[list[Run], list[Run], list[Run], Path]:
    """Pull runs, sessions and pairs from the store. Returns them and the file.

    `store` is imported here rather than at module scope on purpose: everything
    above operates on plain mappings and has no dependency on sqlite at all,
    which is what lets the aggregation be tested on fixtures and reused over any
    other row source later. It also means a missing or half-built store fails
    at the CLI boundary with a clear error instead of at import time.

    `session_id` scopes all three, so `--session` renders one tab's worth of
    data rather than filtering a page that still claims to summarize the store.
    The run rows arrive with the session's `system_prompt` joined in under that
    name (`store.iter_runs`), so nothing downstream knows the column moved.
    """
    from . import store

    resolved = Path(db_path) if db_path is not None else Path(store.DEFAULT_DB_PATH)
    connection = store.connect(resolved)
    try:
        runs = list(
            store.iter_runs(
                connection,
                session_id=session_id,
                bucket=bucket,
                model_tier=model_tier,
            )
        )
        if session_id is not None:
            found = store.get_session(connection, session_id)
            sessions = [found] if found is not None else []
        else:
            sessions = list(store.iter_sessions(connection))
        pairs = list(store.iter_pairs(connection, session_id=session_id))
        return runs, sessions, pairs, resolved
    finally:
        connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.benchmark.report",
        description=(
            "Summarize the metadata-generation benchmark run log. Reads only; "
            "never writes to benchmark.db."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "With no arguments, prints the text summary for the whole database.\n"
            "Accuracy metrics (role/theme/synergy F1, game_stage and power_rating\n"
            "MAE) need hand-labeled ground truth that does not exist yet, and are\n"
            "reported as unavailable rather than as zero.\n"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        metavar="PATH",
        help="Benchmark SQLite file. Defaults to the store's own location.",
    )
    parser.add_argument(
        "--html",
        type=Path,
        metavar="OUT.html",
        help=(
            "Also write a self-contained HTML report here. No external requests: "
            "it opens correctly from a file:// path, offline."
        ),
    )
    parser.add_argument(
        "--session",
        metavar="S-NNN",
        help=(
            "Only this session -- its runs, its own tab, its picks. The "
            "paired comparison the benchmark is for lives at this scope; "
            "without it the page shows every session plus the pooled views."
        ),
    )
    parser.add_argument(
        "--bucket",
        metavar="B",
        help="Only runs in this difficulty bucket (easy/medium/hard).",
    )
    parser.add_argument(
        "--tier",
        metavar="T",
        help="Only runs from this model tier (local_low/local_high/frontier).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the text summary. Only useful alongside --html.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        rows, sessions, pairs, resolved = load_store(
            db_path=args.db,
            session_id=args.session,
            bucket=args.bucket,
            model_tier=args.tier,
        )
    except FileNotFoundError:
        print(
            f"No benchmark database at {args.db or '(default location)'}. "
            "Run the benchmark first.",
            file=sys.stderr,
        )
        return 1

    if args.session and not sessions:
        print(
            f"No session {args.session!r} in {resolved}. "
            "`benchmark sessions` lists what is there.",
            file=sys.stderr,
        )
        return 1

    # Filtering to one bucket or one tier deliberately does not disable the
    # comparison sections -- they render their own "nothing to compare against"
    # state, which is more honest than a table that looks complete because the
    # other tiers were filtered out upstream.
    # The merge is explicit here rather than inside `build_report`: the store
    # holds one row per (card, tier, pass) and every view but the per-pass
    # table reasons about a card's finished answer. See `merge_passes`.
    report = build_report(
        merge_passes(rows),
        raw_runs=rows,
        sessions=sessions,
        pairs=pairs,
        db_path=resolved,
    )

    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(report), encoding="utf-8")
        print(f"Wrote {args.html} ({args.html.stat().st_size:,} bytes)", file=sys.stderr)

    if not args.quiet:
        print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
