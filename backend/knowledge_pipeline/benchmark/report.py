"""Reads the benchmark run log and renders it for a human decision.

`docs/benchmarking-and-testing.md` specifies a 300-card, 3-tier benchmark whose
whole purpose is to answer three questions: is a 14B local model viable at all,
what signal should trigger escalation to frontier, and how much of the corpus
that rule would route to frontier. `store.py` records the raw material for those
answers — one row per (card, tier) generation call. This module turns ~900 such
rows into something a maintainer can actually read.

**The primary view is cross-tier comparison _within a difficulty bucket_, not
the overall average.** That is not a layout preference; it is the document's
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
  vocabulary has failed regardless of how good its judgment otherwise is. Both
  Qwen tiers are code-tuned rather than instruction-tuned, so this rate is the
  specific thing the benchmark was told to confirm rather than assume.
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
file with one denormalized table, and the fastest way to answer a question it
doesn't already answer is to ask SQLite directly::

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
`store` import lives inside `load_rows`/`main` rather than at module scope for
that reason — the aggregation layer has no opinion about where rows came from,
and nothing in it should break if the store grows a second backend.

CLI::

    python -m knowledge_pipeline.benchmark.report                      # text summary
    python -m knowledge_pipeline.benchmark.report --html report.html   # + HTML file
    python -m knowledge_pipeline.benchmark.report --bucket hard --tier local_high

The package's own `__main__.py` is owned elsewhere; a `report` subcommand could
be wired into it later, but this module stands alone in the meantime.
"""

from __future__ import annotations

import argparse
import html
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

    @property
    def schema_valid_rate(self) -> float | None:
        return _rate(self.schema_valid, self.runs)

    @property
    def is_empty(self) -> bool:
        return self.runs == 0


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


@dataclass
class BenchmarkReport:
    """The fully aggregated benchmark, ready to render.

    Holds `runs` as well as the aggregates because the HTML report includes
    every individual run with its prompts and raw response — the *Scoring*
    section's whole premise is that a maintainer reads each output, so an
    aggregate-only artifact would drop the thing the benchmark is actually for.
    At ~900 rows that is a few MB of HTML, which is fine for a local file.
    """

    runs: tuple[Run, ...]
    buckets: tuple[str, ...]
    tiers: tuple[str, ...]
    by_bucket_tier: Mapping[tuple[str, str], GroupStats]
    by_tier: Mapping[str, GroupStats]
    overall: GroupStats
    ground_truth_available: bool
    db_path: Path | None = None
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    @property
    def total_runs(self) -> int:
        return self.overall.runs

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
    ground_truth: Mapping[str, dict] | None = None,
    db_path: Path | None = None,
) -> BenchmarkReport:
    """Aggregate raw `benchmark_runs` rows into every view the renderers need.

    Args:
        rows: Anything yielding mappings with the `benchmark_runs` columns —
            `store.iter_runs`' `sqlite3.Row` list in production, plain dicts in
            tests.
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
        buckets=buckets,
        tiers=tiers,
        by_bucket_tier=by_bucket_tier,
        by_tier=by_tier,
        overall=summarize(
            list(materialized), bucket=None, model_tier=None, ground_truth=ground_truth
        ),
        ground_truth_available=ground_truth is not None,
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
        _tier_label(stats.model_tier),
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

    if overall.runs == 0:
        lines.append("No runs recorded. Nothing to report yet.")
        lines.append("")
        lines.append(_accuracy_note(report.ground_truth_available))
        return "\n".join(lines) + "\n"

    lines.append("PER BUCKET, TIERS SIDE BY SIDE  <- the primary view")
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

    lines.extend(_render_accuracy_text(report))
    return "\n".join(lines) + "\n"


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
            _tier_label(gap.model_tier),
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
        f"    baseline ({_tier_label(BASELINE_TIER)}): "
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
                    _tier_label(stats.model_tier),
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
"""

# No benchmark data is ever passed into JS -- the script only reads the DOM and
# toggles `display`, so untrusted model output never leaves an escaped text node
# and can never reach a string literal in a <script> block.
_JS = """
(function () {
  var table = document.getElementById('runs');
  if (!table) return;
  var bodies = Array.prototype.slice.call(table.tBodies);
  var count = document.getElementById('runcount');
  var inputs = Array.prototype.slice.call(
    document.querySelectorAll('[data-filter]')
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
            f'<td class="txt">{_esc(_tier_label(row["model_tier"]))}</td>'
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
            _details("user prompt (card facts)", row["user_prompt"]),
            _details("system prompt (taxonomy + anchors + rubric)", row["system_prompt"]),
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


def render_html(report: BenchmarkReport) -> str:
    """The shareable artifact: one self-contained file, readable offline.

    Structured in the same order as the text report and for the same reason --
    stat tiles, then the per-bucket comparison and its charts, then the
    hard-bucket gap, then the roll-up, then every individual run. A reader who
    stops after the first screen should have seen the comparison that decides
    something, not an overall average.
    """
    tier_labels = [_tier_label(tier) for tier in report.tiers]
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

    sections.append("<h2>Accuracy vs. ground truth</h2>" + _accuracy_html(report))
    sections.append(
        "<h2>Every run</h2>"
        '<p class="sub">Scoring is human review: 300 cards per tier is small '
        "enough to read every output directly. Prompts and responses are "
        "collapsed, not omitted.</p>" + _runs_table_html(report)
    )

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        "<title>Tome metadata benchmark</title>"
        f"<style>{_CSS}</style></head><body><main>"
        "<h1>Tome metadata benchmark</h1>"
        f'<p class="sub">Local vs. frontier metadata generation, scored per difficulty bucket.</p>'
        f'<p class="meta">source <code>{source}</code> &middot; generated {_esc(generated)}</p>'
        f'<div class="tiles">{tiles_html}</div>'
        + "".join(sections)
        + f"</main><script>{_JS}</script></body></html>\n"
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
            f'<td class="txt">{_esc(_tier_label(gap.model_tier))}</td>'
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
        f'<p class="callout">Baseline {_esc(_tier_label(BASELINE_TIER))}: '
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
                f'<td class="txt">{_esc(_tier_label(stats.model_tier))}</td>'
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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_rows(
    *,
    db_path: Path | None = None,
    bucket: str | None = None,
    model_tier: str | None = None,
) -> tuple[list[Run], Path]:
    """Pull rows from the benchmark store, returning them and the file read.

    `store` is imported here rather than at module scope on purpose: everything
    above operates on plain mappings and has no dependency on sqlite at all,
    which is what lets the aggregation be tested on fixtures and reused over any
    other row source later. It also means a missing or half-built store fails
    at the CLI boundary with a clear error instead of at import time.
    """
    from . import store

    resolved = Path(db_path) if db_path is not None else Path(store.DEFAULT_DB_PATH)
    connection = store.connect(resolved)
    try:
        return (
            list(store.iter_runs(connection, bucket=bucket, model_tier=model_tier)),
            resolved,
        )
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
        rows, resolved = load_rows(
            db_path=args.db, bucket=args.bucket, model_tier=args.tier
        )
    except FileNotFoundError:
        print(
            f"No benchmark database at {args.db or '(default location)'}. "
            "Run the benchmark first.",
            file=sys.stderr,
        )
        return 1

    # Filtering to one bucket or one tier deliberately does not disable the
    # comparison sections -- they render their own "nothing to compare against"
    # state, which is more honest than a table that looks complete because the
    # other tiers were filtered out upstream.
    report = build_report(rows, db_path=resolved)

    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(report), encoding="utf-8")
        print(f"Wrote {args.html} ({args.html.stat().st_size:,} bytes)", file=sys.stderr)

    if not args.quiet:
        print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
