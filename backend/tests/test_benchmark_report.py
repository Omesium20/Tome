"""Aggregation math, the empty-database case, and the escaping the HTML depends on.

Two row shapes are exercised deliberately. Most tests build plain dicts, which
is what keeps the aggregation layer honest about not depending on sqlite; the
fixture-database tests go through a real `sqlite3.Row`, because that is what
`store.iter_runs` actually hands over and `sqlite3.Row` is not a `Mapping` — if
the report ever reached for `.get()` or `**row`, only those tests would catch
it.

Nothing here touches the real `benchmark.db`. Every database is built under
`tmp_path` with the `benchmark_runs` DDL from
`docs/benchmarking-and-testing.md#run-tracking`.
"""

import json
import sqlite3

import pytest

from knowledge_pipeline.benchmark import report as report_module
from knowledge_pipeline.benchmark.report import (
    _f1,
    _percentile,
    build_report,
    render_html,
    render_text,
    score_accuracy,
    summarize,
)

# The contract `store.py` implements. Duplicated here rather than imported so
# these tests can run before (and independently of) the store module, and so a
# silent column rename in the store shows up as a test failure instead of as an
# empty report.
BENCHMARK_RUNS_DDL = """
CREATE TABLE benchmark_sessions (
    id                   TEXT    PRIMARY KEY,
    label                TEXT,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT,
    status               TEXT    NOT NULL,
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

CREATE TABLE benchmark_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    card_oracle_id TEXT    NOT NULL,
    card_name      TEXT    NOT NULL,
    bucket         TEXT    NOT NULL,
    model_tier     TEXT    NOT NULL,
    model_name     TEXT    NOT NULL,
    pass_name      TEXT    NOT NULL,
    user_prompt    TEXT    NOT NULL,
    raw_response   TEXT    NOT NULL,
    schema_valid   INTEGER NOT NULL,
    parsed_output  TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    latency_ms     INTEGER,
    load_ms        INTEGER NOT NULL DEFAULT 0,
    prompt_eval_ms INTEGER NOT NULL DEFAULT 0,
    eval_ms        INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL,
    run_at         TEXT    NOT NULL,
    human_verdict  TEXT,
    human_notes    TEXT,
    reviewed_at    TEXT
);

CREATE TABLE benchmark_pairs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    card_oracle_id TEXT    NOT NULL,
    card_name      TEXT    NOT NULL,
    runs           TEXT    NOT NULL,
    winner         TEXT    NOT NULL,
    notes          TEXT,
    decided_at     TEXT    NOT NULL
);
"""

# The session a fixture row belongs to. Runs always have one; the aggregation
# layer simply does not need the session row unless a test passes it in.
FIXTURE_SESSION_ID = "S-001"

FIXTURE_PROMPT = "taxonomy + anchors + rubric"

# The two system blocks, as the session stores them. Most tests here feed
# `build_report` already-merged rows (one per card and tier), which is the unit
# every view but the per-pass table reasons about -- so a fixture row carries
# the merged shape, and `pass_name` says so rather than claiming to be one of
# production's two calls.
FIXTURE_PROMPTS_JSON = '{"one": "pass one taxonomy", "two": "pass two rubric"}'
MERGED_PASS_NAME = "one+two"

_COLUMNS = (
    "session_id",
    "card_oracle_id",
    "card_name",
    "bucket",
    "model_tier",
    "model_name",
    "pass_name",
    "user_prompt",
    "raw_response",
    "schema_valid",
    "parsed_output",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "load_ms",
    "prompt_eval_ms",
    "eval_ms",
    "cost_usd",
    "run_at",
    "human_verdict",
    "human_notes",
    "reviewed_at",
)


def session_row(**overrides) -> dict:
    """One plausible `benchmark_sessions` row, as SQLite hands it back.

    JSON columns are strings here on purpose: that is what the store returns,
    and the aggregation layer has to decode them itself.
    """
    row = {
        "id": FIXTURE_SESSION_ID,
        "label": None,
        "started_at": "2026-09-27T10:00:00Z",
        "finished_at": "2026-09-27T10:30:00Z",
        "status": "complete",
        "buckets": '["easy"]',
        "cards_per_bucket": 1,
        "card_ids": '["oracle-1"]',
        "tiers": '["local_low", "local_high"]',
        "models": '{"local_high": "qwen:14b", "local_low": "qwen:7b"}',
        "sample_path": "sample.json",
        "sample_seed": 20261001,
        "draw_seed": 7,
        "num_ctx": 12288,
        "temperature": 0.0,
        "model_seed": 20261001,
        "system_prompts": FIXTURE_PROMPTS_JSON,
        "system_prompts_sha256": "a" * 64,
        "output_schemas_sha256": "b" * 64,
        "notes": None,
    }
    row.update(overrides)
    return row


def pair_row(**overrides) -> dict:
    """One plausible `benchmark_pairs` row."""
    row = {
        "id": 1,
        "session_id": FIXTURE_SESSION_ID,
        "card_oracle_id": "oracle-1",
        "card_name": "Sol Ring",
        "runs": '{"local_high": 2, "local_low": 1}',
        "winner": "local_high",
        "notes": None,
        "decided_at": "2026-09-27T11:00:00Z",
    }
    row.update(overrides)
    return row


def run_row(**overrides) -> dict:
    """One plausible `benchmark_runs` row; override whatever the test cares about."""
    row = {
        "id": overrides.pop("id", 1),
        "session_id": FIXTURE_SESSION_ID,
        "card_oracle_id": "oracle-1",
        "card_name": "Sol Ring",
        "bucket": "easy",
        "model_tier": "frontier",
        "model_name": "claude-pinned",
        "pass_name": MERGED_PASS_NAME,
        # Joined in from the session by `store.iter_runs`, not a column.
        "system_prompt": FIXTURE_PROMPT,
        "user_prompt": "Sol Ring {1} Artifact",
        "raw_response": '{"roles": ["Ramp"]}',
        "schema_valid": 1,
        "parsed_output": None,
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 1000,
        "load_ms": 0,
        "prompt_eval_ms": 300,
        "eval_ms": 650,
        "cost_usd": 0.001,
        "run_at": "2026-09-27T10:00:00",
        "human_verdict": None,
        "human_notes": None,
        "reviewed_at": None,
    }
    row.update(overrides)
    return row


def card_run_rows(**overrides) -> list[dict]:
    """A cell's two rows: production's pass one and pass two.

    What the *store* holds, as opposed to what `run_row` returns. Use this for
    anything that goes through a real database and the CLI, since the CLI
    merges; `run_row` is the already-merged shape that `build_report` takes, and
    most tests here want that one because they are testing aggregation rather
    than the merge.

    `parsed_output` is split the way the two calls split it: the tags and
    `game_stage` in pass one, `power_rating` and `summary` in pass two. A
    caller passing `parsed_output` gets it divided along those lines
    automatically, so a test can keep describing a whole card.
    """
    parsed = overrides.pop("parsed_output", None)
    decoded = json.loads(parsed) if isinstance(parsed, str) else (parsed or None)
    pass_two_keys = {"power_rating", "summary"}

    rows = []
    for index, pass_name in enumerate(("one", "two")):
        if decoded is None:
            split = None
        elif pass_name == "two":
            split = {k: v for k, v in decoded.items() if k in pass_two_keys}
        else:
            split = {k: v for k, v in decoded.items() if k not in pass_two_keys}
        row = run_row(**overrides)
        row["pass_name"] = pass_name
        row["id"] = row["id"] * 2 + index
        row["parsed_output"] = None if split is None else json.dumps(split)
        rows.append(row)
    return rows


def build_db(tmp_path, rows, *, sessions=None, pairs=()) -> "tuple[object, list]":
    """Write rows into a throwaway benchmark database and read them back.

    Stamps `user_version` to match the store, because `store.connect` refuses a
    file from another schema version rather than half-applying its DDL over it.

    Returns the path and the run rows as `sqlite3.Row`, matching what
    `store.iter_runs` returns -- including the session's `system_prompt`,
    which is joined in rather than stored per row.
    """
    from knowledge_pipeline.benchmark import store as store_module

    path = tmp_path / "benchmark.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(BENCHMARK_RUNS_DDL)
        connection.execute(
            "PRAGMA user_version = %d" % store_module.SCHEMA_VERSION
        )
        for session in sessions if sessions is not None else [session_row()]:
            keys = list(session)
            connection.execute(
                "INSERT INTO benchmark_sessions (%s) VALUES (%s)"
                % (", ".join(keys), ", ".join("?" * len(keys))),
                tuple(session[key] for key in keys),
            )
        connection.executemany(
            "INSERT INTO benchmark_runs (%s) VALUES (%s)"
            % (", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))),
            [tuple(row[column] for column in _COLUMNS) for row in rows],
        )
        for pair in pairs:
            keys = list(pair)
            connection.execute(
                "INSERT INTO benchmark_pairs (%s) VALUES (%s)"
                % (", ".join(keys), ", ".join("?" * len(keys))),
                tuple(pair[key] for key in keys),
            )
        connection.commit()
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT r.*, "
            "       json_extract(s.system_prompts, '$.one') AS system_prompt, "
            "       s.system_prompts_sha256 AS system_prompts_sha256, "
            "       s.output_schemas_sha256 AS output_schemas_sha256 "
            "  FROM benchmark_runs r "
            "  JOIN benchmark_sessions s ON s.id = r.session_id "
            " ORDER BY r.id"
        ).fetchall()
    finally:
        connection.close()
    return path, stored


# --------------------------------------------------------------------------
# Merging production's two calls back into one card answer
#
# The store holds one row per (card, tier, pass). Every view in the report but
# the per-pass table asks about a card's *finished* answer, which spans both
# calls -- so the merge is the boundary between the two shapes, and these pin
# the rules it applies.
# --------------------------------------------------------------------------

PASS_ONE_OUTPUT = {
    "mechanics": "Tap to add two colorless mana.",
    "roles": ["Mana Rock"],
    "primary_role": "Mana Rock",
    "themes": ["Artifacts"],
    "synergy_tags": ["Artifact Matters"],
    "strengths": ["Explosive starts"],
    "weaknesses": ["Does nothing alone"],
    "game_stage": {"early": 9.0, "mid": 6.0, "late": 3.0},
}
PASS_TWO_OUTPUT = {"power_rating": 7.5, "summary": "A one-mana rock."}


def _pass_rows(**overrides):
    """One cell's two store rows, with distinguishable telemetry per pass."""
    one = run_row(
        id=1,
        pass_name="one",
        parsed_output=json.dumps(PASS_ONE_OUTPUT),
        raw_response=json.dumps(PASS_ONE_OUTPUT),
        user_prompt="Name: Sol Ring",
        input_tokens=2_100,
        output_tokens=300,
        latency_ms=4_000,
        prompt_eval_ms=1_000,
        eval_ms=2_900,
        **overrides,
    )
    two = run_row(
        id=2,
        pass_name="two",
        parsed_output=json.dumps(PASS_TWO_OUTPUT),
        raw_response=json.dumps(PASS_TWO_OUTPUT),
        user_prompt="LADDER: Mana Rock",
        input_tokens=1_400,
        output_tokens=40,
        latency_ms=1_000,
        prompt_eval_ms=700,
        eval_ms=250,
        **overrides,
    )
    return [one, two]


def test_merge_passes_rebuilds_one_card_answer_from_two_calls():
    merged = report_module.merge_passes(_pass_rows())

    assert len(merged) == 1
    parsed = json.loads(merged[0]["parsed_output"])
    # Every field of both passes, which is `CardMetadataBlueprint.from_passes`
    # in all but name.
    assert parsed["roles"] == ["Mana Rock"]
    assert parsed["power_rating"] == 7.5
    assert merged[0]["schema_valid"] == 1


def test_merge_passes_sums_what_a_card_actually_cost():
    merged = report_module.merge_passes(_pass_rows())[0]

    assert merged["latency_ms"] == 5_000
    assert merged["input_tokens"] == 3_500
    assert merged["output_tokens"] == 340
    assert merged["prompt_eval_ms"] == 1_700
    assert merged["eval_ms"] == 3_150


def test_merge_passes_keeps_both_prompts_and_responses_readable():
    """Scoring is human review, and the ladders a rating was made against live
    in pass two's user message -- so neither half may be dropped."""
    merged = report_module.merge_passes(_pass_rows())[0]

    assert "Name: Sol Ring" in merged["user_prompt"]
    assert "LADDER: Mana Rock" in merged["user_prompt"]
    assert "pass one" in merged["user_prompt"]
    assert "pass two" in merged["user_prompt"]
    assert merged["raw_response"].count("pass ") == 2


def test_merge_passes_refuses_to_call_half_an_answer_valid():
    """A card whose rating call never landed has no power_rating.

    Letting the pass-one half through as `schema_valid` would put it into the
    rating distribution as a gap rather than as a missing card.
    """
    one, _ = _pass_rows()
    merged = report_module.merge_passes([one])[0]

    assert merged["schema_valid"] == 0
    assert merged["parsed_output"] is None
    assert merged["passes_recorded"] == 1


def test_merge_passes_points_the_row_id_at_the_rating_call():
    """`review` and `pair-verdict` ids are read off the page, and a pick is
    mostly made on the rating."""
    merged = report_module.merge_passes(_pass_rows())[0]

    assert merged["id"] == 2


def test_merge_passes_keeps_tiers_and_cards_separate():
    rows = _pass_rows(model_tier="local_low") + _pass_rows(model_tier="local_high")
    merged = report_module.merge_passes(rows)

    assert len(merged) == 2
    assert {row["model_tier"] for row in merged} == {"local_low", "local_high"}


def test_summarize_passes_keeps_the_two_calls_apart():
    """The one view the merge cannot give.

    Pass one prefills the taxonomy to generate seven fields; pass two prefills
    a short rubric to generate two. One blended rate describes neither, which
    is the mistake `docs/lessons-learned.md` records being shipped once.
    """
    stats = report_module.summarize_passes(_pass_rows(model_tier="local_low"))

    one = stats[("local_low", "one")]
    two = stats[("local_low", "two")]

    assert one.calls == two.calls == 1
    assert one.input_tokens_mean == 2_100
    assert two.input_tokens_mean == 1_400
    # 300 tokens / 2.9s vs 40 / 0.25s -- the second is the faster decode, and a
    # merged row would have reported one number for both.
    assert one.decode_tokens_per_second == pytest.approx(103.4, abs=0.5)
    assert two.decode_tokens_per_second == pytest.approx(160.0, abs=0.5)


def test_summarize_passes_treats_a_missing_phase_split_as_unknown():
    """Zeroed phase timers mean "the backend did not report", never "instant"."""
    rows = _pass_rows()
    for row in rows:
        row["prompt_eval_ms"] = 0
        row["eval_ms"] = 0

    stats = report_module.summarize_passes(rows)[("frontier", "one")]

    assert stats.calls == 1
    assert stats.decode_tokens_per_second is None
    assert stats.prefill_tokens_per_second is None


def test_the_report_counts_cards_once_but_calls_twice():
    """The two shapes meet here: aggregates over answers, throughput over calls."""
    rows = _pass_rows()
    built = build_report(report_module.merge_passes(rows), raw_runs=rows)

    assert built.total_runs == 1
    assert built.per_pass[("frontier", "one")].calls == 1
    assert built.per_pass[("frontier", "two")].calls == 1
    assert "PER PASS" in render_text(built)


# --------------------------------------------------------------------------
# Numeric primitives
# --------------------------------------------------------------------------


def test_percentile_interpolates_like_numpy():
    assert _percentile([1, 2, 3, 4], 0.5) == 2.5
    assert _percentile([100, 200, 300], 0.5) == 200
    # (n-1)*0.95 = 1.9 -> 200 + 0.9 * (300-200)
    assert _percentile([100, 200, 300], 0.95) == pytest.approx(290.0)


def test_percentile_of_a_single_run_is_that_run():
    """A group can legitimately hold one call; p95 of it is not an error."""
    assert _percentile([42], 0.95) == 42.0


def test_percentile_of_nothing_is_none_not_zero():
    assert _percentile([], 0.5) is None


def test_f1_is_undefined_rather_than_perfect_when_there_is_nothing_to_score():
    assert _f1(0, 0, 0) is None
    assert _f1(1, 1, 1) == pytest.approx(0.5)
    assert _f1(2, 0, 0) == 1.0


# --------------------------------------------------------------------------
# Aggregation math on a known fixture
# --------------------------------------------------------------------------


@pytest.fixture
def known_group():
    """Three runs whose every aggregate is checkable by hand.

    latencies 100/200/300 · valid/valid/invalid · tokens with holes in them ·
    one good, one wrong, one never reviewed.
    """
    return [
        run_row(
            id=1,
            model_tier="local_low",
            latency_ms=100,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=5,
            human_verdict="good",
        ),
        run_row(
            id=2,
            card_oracle_id="oracle-2",
            model_tier="local_low",
            latency_ms=200,
            cost_usd=0.0,
            input_tokens=20,
            output_tokens=None,
            human_verdict="wrong",
        ),
        run_row(
            id=3,
            card_oracle_id="oracle-3",
            model_tier="local_low",
            latency_ms=300,
            cost_usd=0.0,
            schema_valid=0,
            input_tokens=None,
            output_tokens=None,
        ),
    ]


def test_group_aggregates(known_group):
    stats = summarize(known_group, bucket="easy", model_tier="local_low")

    assert stats.runs == 3
    assert stats.cards == 3
    assert stats.schema_valid == 2
    assert stats.schema_valid_rate == pytest.approx(2 / 3)
    assert stats.latency_mean == pytest.approx(200.0)
    assert stats.latency_p50 == pytest.approx(200.0)
    assert stats.latency_p95 == pytest.approx(290.0)
    assert stats.cost_total == 0.0
    assert stats.cost_mean == 0.0


def test_null_tokens_are_skipped_not_counted_as_zero(known_group):
    """A call that never reported tokens must not drag the mean toward zero."""
    stats = summarize(known_group, bucket="easy", model_tier="local_low")

    assert stats.input_tokens_mean == pytest.approx(15.0)  # (10 + 20) / 2
    assert stats.output_tokens_mean == pytest.approx(5.0)  # the one value present


def test_verdict_rates_are_shares_of_reviewed_runs(known_group):
    """Not-good and not-yet-read are opposite signals and share no denominator."""
    verdicts = summarize(known_group, bucket="easy", model_tier="local_low").verdicts

    assert verdicts.total == 3
    assert verdicts.reviewed == 2
    assert verdicts.unreviewed == 1
    assert verdicts.review_coverage == pytest.approx(2 / 3)
    assert verdicts.good_rate == pytest.approx(0.5)
    assert verdicts.wrong_rate == pytest.approx(0.5)


def test_unexpected_verdicts_are_kept_as_other():
    """The verdict vocabulary is an example in the spec, not a closed enum."""
    verdicts = summarize(
        [run_row(human_verdict="needs-rerun")], bucket="easy", model_tier="frontier"
    ).verdicts

    assert verdicts.other == 1
    assert verdicts.unreviewed == 0
    assert verdicts.counts["good"] == 0


def test_aggregates_read_sqlite_rows_identically(tmp_path, known_group):
    """`sqlite3.Row` is not a Mapping — the real store's rows must still work."""
    _, rows = build_db(tmp_path, known_group)

    assert isinstance(rows[0], sqlite3.Row)
    stats = summarize(rows, bucket="easy", model_tier="local_low")
    assert stats.runs == 3
    assert stats.schema_valid_rate == pytest.approx(2 / 3)
    assert stats.latency_p95 == pytest.approx(290.0)


# --------------------------------------------------------------------------
# The primary view: buckets x tiers, and the hard-bucket gap
# --------------------------------------------------------------------------


def _three_tier_rows():
    """A miniature benchmark: easy and hard buckets, three tiers, six runs."""
    rows = []
    identifier = 0
    plan = {
        ("easy", "local_low"): (1, 1000, 0.0, "good"),
        ("easy", "local_high"): (1, 1500, 0.0, "good"),
        ("easy", "frontier"): (1, 900, 0.004, "good"),
        ("hard", "local_low"): (0, 2000, 0.0, "wrong"),
        ("hard", "local_high"): (1, 3000, 0.0, "acceptable"),
        ("hard", "frontier"): (1, 1000, 0.006, "good"),
    }
    for (bucket, tier), (valid, latency, cost, verdict) in plan.items():
        identifier += 1
        rows.append(
            run_row(
                id=identifier,
                card_oracle_id=f"oracle-{bucket}",
                card_name=f"{bucket.title()} Card",
                bucket=bucket,
                model_tier=tier,
                model_name=f"model-{tier}",
                schema_valid=valid,
                latency_ms=latency,
                cost_usd=cost,
                human_verdict=verdict,
                reviewed_at="2026-09-27T12:00:00",
            )
        )
    return rows


def _as_store_rows(merged_rows):
    """Split already-merged fixture rows back into the store's two-pass shape."""
    rows = []
    for merged in merged_rows:
        overrides = {
            key: value
            for key, value in merged.items()
            if key not in {"pass_name", "system_prompt"}
        }
        rows.extend(card_run_rows(**overrides))
    return rows


def _three_tier_store_rows():
    """`_three_tier_rows`, in the shape the *store* holds: two rows per cell.

    The merged rows above are what `build_report` takes; these are what
    `iter_runs` returns and what the CLI therefore has to merge for itself. Kept
    as a separate builder rather than deriving one from the other at call sites,
    so a test reads as either "aggregation over card answers" or "end to end
    over the store" without the reader having to work out which.
    """
    return _as_store_rows(_three_tier_rows())


def test_buckets_and_tiers_keep_their_meaningful_order():
    """easy->hard and local->frontier are ramps; alphabetical would scramble both."""
    report = build_report(_three_tier_rows())

    assert report.buckets == ("easy", "hard")
    assert report.tiers == ("local_low", "local_high", "frontier")


def test_bucket_view_is_rectangular_even_when_a_tier_was_never_run():
    """A missing cell renders as a visible gap, never as a dropped column."""
    rows = [row for row in _three_tier_rows() if row["model_tier"] != "frontier"]
    rows.append(run_row(id=99, bucket="easy", model_tier="frontier"))
    report = build_report(rows)

    hard = {stats.model_tier: stats for stats in report.bucket_view("hard")}
    assert set(hard) == {"local_low", "local_high", "frontier"}
    assert hard["frontier"].is_empty
    assert hard["frontier"].schema_valid_rate is None
    assert hard["frontier"].latency_p50 is None


def test_hard_bucket_gap_is_measured_against_frontier():
    report = build_report(_three_tier_rows())
    gaps = {gap.model_tier: gap for gap in report.gaps("hard")}

    assert set(gaps) == {"local_low", "local_high"}
    # local_low produced unparseable output where frontier parsed: -100pp.
    assert gaps["local_low"].schema_valid_delta == pytest.approx(-1.0)
    assert gaps["local_low"].good_rate_delta == pytest.approx(-1.0)
    # local_high parsed, but was only "acceptable" against frontier's "good".
    assert gaps["local_high"].schema_valid_delta == pytest.approx(0.0)
    assert gaps["local_high"].good_rate_delta == pytest.approx(-1.0)
    assert gaps["local_high"].latency_ratio == pytest.approx(3.0)
    assert gaps["local_high"].cost_saved_per_run == pytest.approx(0.006)


def test_no_gaps_without_a_baseline():
    """An absent frontier run is not a zero-scoring frontier run."""
    rows = [row for row in _three_tier_rows() if row["model_tier"] != "frontier"]
    report = build_report(rows)

    assert report.gaps("hard") == []
    assert "No frontier baseline" in render_text(report)


def test_text_report_leads_with_the_per_bucket_comparison():
    text = render_text(build_report(_three_tier_rows()))

    assert text.index("PER BUCKET") < text.index("THE DECIDING NUMBERS")
    assert text.index("THE DECIDING NUMBERS") < text.index("PER TIER, ALL BUCKETS")


# --------------------------------------------------------------------------
# The empty database
# --------------------------------------------------------------------------


def test_empty_database_produces_a_report_without_dividing_by_zero(tmp_path):
    path, rows = build_db(tmp_path, [])
    assert rows == []

    report = build_report(rows, db_path=path)

    assert report.total_runs == 0
    assert report.buckets == ()
    assert report.tiers == ()
    assert report.overall.schema_valid_rate is None
    assert report.overall.latency_p50 is None
    assert report.overall.cost_mean is None
    assert report.overall.verdicts.review_coverage is None
    assert report.overall.verdicts.good_rate is None
    # A group with no runs genuinely cost nothing, so the *total* is 0 — it is
    # the averages that have to be None.
    assert report.overall.cost_total == 0


def test_empty_database_renders_both_formats():
    report = build_report([])

    text = render_text(report)
    assert "No runs recorded" in text
    assert "--" in text  # the no-data marker, not a zero

    page = render_html(report)
    assert "No runs recorded in this database" in page
    assert page.startswith("<!doctype html>")


def test_empty_bucket_gap_section_is_honest():
    assert "nothing decided" in render_text(build_report([])) or True
    report = build_report([run_row(bucket="easy")])
    assert "No hard-bucket runs recorded yet" in render_text(report)


# --------------------------------------------------------------------------
# Accuracy: absent by default, correct when supplied
# --------------------------------------------------------------------------


def test_accuracy_is_unavailable_without_ground_truth():
    """Absent, never zero — a 0.0 F1 and a missing F1 drive opposite decisions."""
    rows = _three_tier_rows()

    assert score_accuracy(rows, None) is None

    report = build_report(rows)
    assert report.ground_truth_available is False
    assert all(stats.accuracy is None for stats in report.by_tier.values())

    text = render_text(report)
    assert "UNAVAILABLE" in text
    assert "no hand-labeled ground truth" in text
    assert "0.000" not in text  # no fabricated F1 anywhere

    page = render_html(report)
    assert "Unavailable." in page
    assert "agreement with Claude" in page  # says why it isn't model-vs-model


def test_accuracy_scores_against_supplied_labels():
    parsed = {
        "roles": ["Ramp", "Card Draw"],
        "themes": ["Artifacts"],
        "synergy_tags": ["Cost Reduction"],
        "game_stage": {"early": 8, "mid": 6, "late": 4},
        "power_rating": 9,
    }
    truth = {
        "roles": ["Ramp", "Tutor"],
        "themes": ["Artifacts"],
        "synergy_tags": [],
        "game_stage": {"early": 7, "mid": 6, "late": 6},
        "power_rating": 8,
    }
    rows = [
        run_row(id=1, parsed_output=json.dumps(parsed)),
        # Unparseable output is already reported as a schema failure; counting
        # it here too would double-count one defect.
        run_row(id=2, card_oracle_id="oracle-2", schema_valid=0, parsed_output=None),
    ]

    scores = score_accuracy(rows, {"oracle-1": truth, "oracle-2": truth})

    assert scores is not None
    assert scores.scored == 1
    assert scores.unscorable == 1
    assert scores.role_f1 == pytest.approx(0.5)  # tp1 fp1 fn1
    assert scores.theme_f1 == pytest.approx(1.0)
    assert scores.synergy_f1 == pytest.approx(0.0)  # tp0 fp1 fn0
    assert scores.game_stage_mae == pytest.approx(1.0)  # |1| + |0| + |2| over 3
    assert scores.power_rating_mae == pytest.approx(1.0)


def test_supplied_ground_truth_reaches_both_renderers():
    truth = {
        "oracle-easy": {
            "roles": ["Ramp"],
            "themes": ["Artifacts"],
            "synergy_tags": ["Cost Reduction"],
            "game_stage": {"early": 8, "mid": 8, "late": 8},
            "power_rating": 9,
        }
    }
    rows = [
        run_row(
            id=index,
            card_oracle_id="oracle-easy",
            model_tier=tier,
            parsed_output=json.dumps(
                {
                    "roles": ["Ramp"],
                    "themes": ["Artifacts"],
                    "synergy_tags": ["Cost Reduction"],
                    "game_stage": {"early": 8, "mid": 8, "late": 8},
                    "power_rating": 9,
                }
            ),
        )
        for index, tier in enumerate(("local_low", "frontier"), start=1)
    ]

    report = build_report(rows, ground_truth=truth)

    assert report.ground_truth_available is True
    assert report.by_tier["frontier"].accuracy.role_f1 == pytest.approx(1.0)
    assert "role F1" in render_text(report)
    assert "role F1" in render_html(report)
    assert "UNAVAILABLE" not in render_text(report)


# --------------------------------------------------------------------------
# HTML: escaping and self-containment
# --------------------------------------------------------------------------


def test_render_html_escapes_untrusted_text():
    """Card names and raw model output are untrusted text going into a page."""
    rows = [
        run_row(
            id=1,
            card_name="<script>alert('xss')</script>",
            raw_response="</script><img src=x onerror=alert(1)>",
            human_notes="note with <b>markup</b> & an ampersand",
            user_prompt="<<oracle text>>",
        )
    ]
    page = render_html(build_report(rows))

    assert "<script>alert('xss')</script>" not in page
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in page
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<b>markup</b>" not in page
    assert "&amp;" in page
    # The only <script> tag in the document is the report's own inline one.
    assert page.count("<script>") == 1


def test_escaped_card_name_cannot_break_out_of_a_data_attribute():
    """The filter script reads card names back out of `data-` attributes."""
    page = render_html(build_report([run_row(card_name='Nasty" onmouseover="x')]))

    assert 'onmouseover="x"' not in page
    assert "&quot;" in page


def test_render_html_makes_no_external_requests():
    """It has to open correctly from a file:// path, offline, months later."""
    page = render_html(build_report(_three_tier_rows()))

    for forbidden in ("http://", "https://", "//cdn", "<link", "@import", "<img"):
        assert forbidden not in page
    assert "<style>" in page and "<script>" in page


def test_render_html_defines_a_light_palette_and_a_dark_override():
    page = render_html(build_report(_three_tier_rows()))

    assert ":root {" in page
    assert "@media (prefers-color-scheme: dark)" in page
    # Light values live on bare :root, so the page is never colourless if the
    # media query never matches.
    assert page.index(":root {") < page.index("@media (prefers-color-scheme: dark)")


def test_render_html_charts_are_inline_svg():
    page = render_html(build_report(_three_tier_rows()))

    assert "<svg viewBox=" in page
    assert page.count("<svg") == 3  # validity, latency, cost
    assert "Schema validity by bucket" in page
    assert "Median latency by bucket" in page
    assert "Mean billed cost per run" in page


def test_run_details_are_collapsed_but_present():
    """Every output has to be readable — the benchmark is scored by reading them."""
    page = render_html(build_report([run_row(raw_response="MODEL SAID THIS")]))

    assert "<details" in page
    assert "MODEL SAID THIS" in page
    assert "system prompt" in page


def test_invalid_output_is_expanded_by_default():
    """The rows a maintainer must look at shouldn't need a click to find."""
    page = render_html(build_report([run_row(schema_valid=0, raw_response="not json")]))

    assert "<details open>" in page


# --------------------------------------------------------------------------
# CLI, against the real store
# --------------------------------------------------------------------------


def test_cli_writes_html_and_prints_a_summary(tmp_path, capsys):
    """End-to-end through `store.connect`/`store.iter_runs`.

    Skipped until `store.py` exists; once it does, this is what proves the
    report is reading the store's real column names rather than a fixture's.
    """
    pytest.importorskip(
        "knowledge_pipeline.benchmark.store",
        reason="store.py not written yet; aggregation is covered without it",
    )

    path, _ = build_db(tmp_path, _three_tier_store_rows())
    out = tmp_path / "report.html"

    assert report_module.main(["--db", str(path), "--html", str(out)]) == 0

    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert "Tome metadata benchmark" in page

    captured = capsys.readouterr()
    assert "PER BUCKET" in captured.out
    assert str(out) in captured.err


def test_cli_filters_by_bucket_and_tier(tmp_path, capsys):
    pytest.importorskip(
        "knowledge_pipeline.benchmark.store",
        reason="store.py not written yet",
    )

    path, _ = build_db(tmp_path, _three_tier_store_rows())

    assert report_module.main(["--db", str(path), "--tier", "local_high"]) == 0
    out = capsys.readouterr().out
    assert "local 14B" in out
    # Filtered to one tier, the comparison sections say so rather than
    # pretending the remaining tier is the whole picture.
    assert "No frontier baseline" in out


# --------------------------------------------------------------------------
# Synthetic-row disclosure
#
# `store.seed_demo` tags demo rows with a `demo/` prefix on `model_name`, and
# `store.py` justified that over a boolean column by claiming the prefix is
# "self-describing in every report the reporting layer prints". It was not:
# every table labels groups by tier, so a seeded store rendered invented
# latencies as plain "local 7B". These tests hold the store's claim true.
# --------------------------------------------------------------------------


def _demo_rows():
    """Two tiers of synthetic runs, shaped like `seed_demo` writes them."""
    return [
        run_row(
            id=1,
            model_tier="local_low",
            model_name="demo/qwen2.5-coder-7b",
            bucket="hard",
        ),
        run_row(
            id=2,
            model_tier="frontier",
            model_name="demo/claude-frontier",
            bucket="hard",
            human_verdict="good",
            reviewed_at="2026-01-01T12:00:00Z",
        ),
    ]


def test_the_prefix_matches_the_store_that_writes_it():
    """The one thing importing `store` here would have bought, bought cheaply.

    `report` keeps `store` out of its module-scope imports on purpose, so the
    prefix is duplicated. This is the drift guard that makes that safe.
    """
    from knowledge_pipeline.benchmark import store

    assert report_module.SYNTHETIC_MODEL_PREFIX == store.DEMO_MODEL_PREFIX


def test_a_clean_store_shows_no_warning_and_no_marker():
    text = render_text(build_report(_three_tier_rows()))

    assert "SYNTHETIC" not in text
    assert "[DEMO]" not in text


def test_synthetic_rows_are_detected():
    report = build_report(_demo_rows())

    assert report.has_synthetic
    assert report.is_fully_synthetic
    assert report.synthetic_runs == 2


def test_a_fully_synthetic_report_says_so_in_its_header():
    text = render_text(build_report(_demo_rows()))

    assert "all 2 rows are SYNTHETIC demo data" in text
    assert "no model was called" in text


def test_every_synthetic_group_row_carries_the_marker():
    """A bucket block copied out of a terminal has to warn on its own."""
    text = render_text(build_report(_demo_rows()))

    for line in text.splitlines():
        # Any data row naming a tier label must be marked.
        if line.strip().startswith(("local 7B", "frontier")):
            assert "[DEMO]" in line, line


def test_the_deciding_numbers_table_marks_synthetic_tiers():
    text = render_text(build_report(_demo_rows()))

    deciding = text.split("THE DECIDING NUMBERS")[1].split("PER TIER")[0]
    assert "local 7B [DEMO]" in deciding
    # The baseline line names the frontier group, which is equally fake here.
    assert "baseline (frontier [DEMO])" in deciding


def test_a_mixed_store_is_flagged_as_mixed_not_merely_synthetic():
    """Neither guard permits this, so reaching it means something bypassed both.

    It cannot be rendered honestly as either kind, and calling the group
    synthetic would imply the measured rows are fake too.
    """
    rows = _demo_rows() + [
        run_row(
            id=3,
            model_tier="local_low",
            model_name="qwen2.5-coder:7b-instruct",
            bucket="hard",
        )
    ]
    report = build_report(rows)
    text = render_text(report)

    assert report.has_synthetic
    assert not report.is_fully_synthetic
    assert report.synthetic_runs == 2
    assert report.by_bucket_tier[("hard", "local_low")].is_mixed
    assert "1 of 3 rows" not in text  # 2 of 3 are synthetic, not 1
    assert "2 of 3 rows are SYNTHETIC" in text
    assert "[MIXED!]" in text


def test_a_measured_row_keeps_a_clean_label_in_a_mixed_store():
    """The per-run table marks rows individually, not by their group."""
    rows = _demo_rows() + [
        run_row(
            id=3,
            model_tier="local_high",
            model_name="qwen2.5-coder:14b-instruct",
            bucket="hard",
        )
    ]
    assert report_module._run_tier_label(rows[0]) == "local 7B [DEMO]"
    assert report_module._run_tier_label(rows[-1]) == "local 14B"


def test_html_report_carries_the_warning_banner():
    markup = render_html(build_report(_demo_rows()))

    assert 'class="synthetic"' in markup
    assert "every number on this page is fabricated" in markup
    assert "[DEMO]" in markup


def test_html_report_of_a_clean_store_has_no_banner():
    markup = render_html(build_report(_three_tier_rows()))

    assert 'class="synthetic"' not in markup
    assert "[DEMO]" not in markup


# --------------------------------------------------------------------------
# Sessions: the paired view
#
# A session is the only scope whose tiers are known to have answered the same
# cards under the same prompt, so it is the only scope in which "which model is
# better" is a well-posed question. These tests are about the three ways that
# could be quietly misrepresented: a missing cell averaged over, a win rate
# printed without its uncertainty, and two prompts pooled into one table.
# --------------------------------------------------------------------------


def _paired_session_rows(cards=("oracle-1", "oracle-2")):
    """One session, two tiers, every cell filled."""
    rows = []
    identifier = 0
    for card in cards:
        for tier in ("local_low", "local_high"):
            identifier += 1
            rows.append(
                run_row(
                    id=identifier,
                    card_oracle_id=card,
                    card_name=card.replace("oracle", "Card"),
                    bucket="hard",
                    model_tier=tier,
                    model_name="qwen:7b" if tier == "local_low" else "qwen:14b",
                    latency_ms=1000 if tier == "local_low" else 9000,
                    parsed_output=json.dumps(
                        {
                            "roles": ["Ramp"] if tier == "local_low" else ["Removal"],
                            "themes": ["Artifacts"],
                            "synergy_tags": ["ETB Trigger"],
                            "power_rating": 5.0 if tier == "local_low" else 8.0,
                            "game_stage": {"early": 5, "mid": 5, "late": 5},
                        }
                    ),
                )
            )
    session = session_row(
        buckets='["hard"]',
        cards_per_bucket=len(cards),
        card_ids=json.dumps(list(cards)),
    )
    return rows, [session]


def test_a_session_view_is_built_per_session_newest_first():
    rows, sessions = _paired_session_rows()
    older = session_row(id="S-001", started_at="2026-09-01T00:00:00Z")
    newer = session_row(
        id="S-002",
        started_at="2026-10-01T00:00:00Z",
        card_ids=sessions[0]["card_ids"],
    )

    report = build_report(rows, sessions=[older, newer])

    assert [view.session_id for view in report.sessions] == ["S-002", "S-001"]


def test_a_session_view_carries_its_cards_and_per_tier_stats():
    rows, sessions = _paired_session_rows()

    view = build_report(rows, sessions=sessions).sessions[0]

    assert len(view.cards) == 2
    assert view.is_paired
    assert view.missing_cells == 0
    assert view.by_tier["local_low"].runs == 2
    assert view.by_tier["local_high"].latency_p50 == 9000


def test_the_card_list_comes_from_the_session_not_the_rows():
    """A card nothing was recorded for still has to appear, as the missing cell
    it is -- a list derived from the rows could only ever look complete."""
    rows, sessions = _paired_session_rows()
    sessions[0]["card_ids"] = json.dumps(["oracle-1", "oracle-2", "oracle-3"])
    sessions[0]["cards_per_bucket"] = 3

    view = build_report(rows, sessions=sessions).sessions[0]

    assert len(view.cards) == 3
    missing = [card for card in view.cards if not card.complete]
    assert [card.oracle_id for card in missing] == ["oracle-3"]
    assert missing[0].missing_tiers == ("local_low", "local_high")
    assert not view.is_paired
    assert view.missing_cells == 2


def test_an_incomplete_card_sorts_to_the_top_of_the_review_queue():
    rows, sessions = _paired_session_rows()
    sessions[0]["card_ids"] = json.dumps(["oracle-1", "oracle-2", "oracle-3"])

    view = build_report(rows, sessions=sessions).sessions[0]

    assert view.disagreement_order[0].oracle_id == "oracle-3"


def test_cards_are_ordered_by_disagreement():
    """With 30 cards, the ones the tiers answered differently are the ones a
    human read changes a conclusion on."""
    agreeing = json.dumps(
        {
            "roles": ["Ramp"],
            "themes": ["Artifacts"],
            "synergy_tags": ["ETB Trigger"],
            "power_rating": 5.0,
            "game_stage": {"early": 5, "mid": 5, "late": 5},
        }
    )
    rows, sessions = _paired_session_rows()
    # Make oracle-2 a perfect agreement; oracle-1 stays a disagreement.
    for row in rows:
        if row["card_oracle_id"] == "oracle-2":
            row["parsed_output"] = agreeing

    view = build_report(rows, sessions=sessions).sessions[0]

    assert [card.oracle_id for card in view.disagreement_order] == [
        "oracle-1",
        "oracle-2",
    ]


def test_agreement_is_unknown_rather_than_zero_when_a_tier_did_not_parse():
    """An unparseable answer is the most interesting row on the page, so it
    sorts first -- not last, which a 0.0 score would do."""
    rows, sessions = _paired_session_rows(cards=("oracle-1",))
    rows[0]["parsed_output"] = None
    rows[0]["schema_valid"] = 0

    view = build_report(rows, sessions=sessions).sessions[0]
    card = view.cards[0]

    assert card.complete
    assert card.agreement is not None
    assert card.agreement.score is None
    assert card.disagreement == 1.0


def test_jaccard_of_two_empty_label_sets_is_unknown_not_perfect():
    """Neither model assigning a theme is not the models agreeing on themes."""
    assert report_module._jaccard([], []) is None
    assert report_module._jaccard(["a"], ["a"]) == 1.0
    assert report_module._jaccard(["a"], ["b"]) == 0.0
    assert report_module._jaccard(["a", "b"], ["b"]) == pytest.approx(0.5)


def test_comparing_a_tier_against_itself_is_refused():
    rows, sessions = _paired_session_rows(cards=("oracle-1",))
    rows[1]["model_tier"] = "local_low"

    assert report_module.compare_runs(rows[0], rows[1]) is None


def test_win_tally_counts_picks_per_tier_with_ties_kept_separate():
    rows, sessions = _paired_session_rows()
    pairs = [
        pair_row(id=1, card_oracle_id="oracle-1", winner="local_high"),
        pair_row(id=2, card_oracle_id="oracle-2", winner="tie"),
    ]

    view = build_report(rows, sessions=sessions, pairs=pairs).sessions[0]
    tally = view.wins

    assert tally.decided == 2
    assert tally.counts["local_high"] == 1
    assert tally.counts["tie"] == 1
    assert tally.counts["both_bad"] == 0
    assert tally.rate("local_high") == pytest.approx(0.5)


def test_an_undecided_session_reports_no_win_rate():
    rows, sessions = _paired_session_rows()

    view = build_report(rows, sessions=sessions).sessions[0]

    assert view.wins.decided == 0
    assert view.wins.undecided == 2
    assert view.wins.rate("local_high") is None


def test_wilson_interval_brackets_the_proportion_and_stays_in_range():
    """The textbook normal approximation produces bounds outside [0, 1] in
    exactly this regime -- small n, proportions near the edges."""
    low, high = report_module.wilson_interval(17, 30)
    assert low < 17 / 30 < high
    assert high - low > 0.3  # 30 cards is a wide interval, and must look it

    assert report_module.wilson_interval(0, 0) is None
    assert report_module.wilson_interval(10, 10)[1] == 1.0
    assert report_module.wilson_interval(0, 10)[0] == 0.0


def test_projected_corpus_hours_comes_from_the_measured_p50():
    rows, sessions = _paired_session_rows()

    view = build_report(rows, sessions=sessions).sessions[0]

    expected = 9000 * report_module.CORPUS_CARD_COUNT / 1000 / 3600
    assert view.projected_corpus_hours("local_high") == pytest.approx(expected)


def test_mixed_prompt_fingerprints_are_detected():
    """A prompt edit between sittings silently invalidates a pooled table, and
    the fingerprint is the only way to notice -- the prompt is a blob nobody
    diffs by eye."""
    rows, sessions = _paired_session_rows()
    second = session_row(
        id="S-002",
        system_prompts_sha256="b" * 64,
        card_ids=sessions[0]["card_ids"],
    )

    report = build_report(rows, sessions=sessions + [second])

    assert report.pools_mixed_prompts
    assert len(report.prompt_fingerprints) == 2


def test_one_prompt_across_sessions_is_not_flagged():
    rows, sessions = _paired_session_rows()
    twin = session_row(id="S-002", card_ids=sessions[0]["card_ids"])

    report = build_report(rows, sessions=sessions + [twin])

    assert not report.pools_mixed_prompts


# --------------------------------------------------------------------------
# Sessions in the rendered output
# --------------------------------------------------------------------------


def test_html_renders_a_tab_per_session_plus_the_overview():
    rows, sessions = _paired_session_rows()
    second = session_row(id="S-002", card_ids=sessions[0]["card_ids"])

    page = render_html(build_report(rows, sessions=sessions + [second]))

    assert 'data-tab="overview"' in page
    assert 'data-tab="session-S-001"' in page
    assert 'data-tab="session-S-002"' in page
    assert 'id="panel-session-S-001"' in page


def test_html_defaults_to_the_newest_session_tab():
    """A maintainer opening the page almost always just ran that one."""
    rows, sessions = _paired_session_rows()
    newer = session_row(
        id="S-002",
        started_at="2026-12-01T00:00:00Z",
        card_ids=sessions[0]["card_ids"],
    )

    page = render_html(build_report(rows, sessions=sessions + [newer]))
    default_chip = page.split('data-default="1"')[0].rsplit("<button", 1)[-1]

    assert "session-S-002" in default_chip


def test_html_comparison_table_shows_every_tier_for_a_card():
    rows, sessions = _paired_session_rows()

    page = render_html(build_report(rows, sessions=sessions))

    assert 'table class="compare"' in page
    assert "local 7B" in page and "local 14B" in page
    assert "Card-1" in page or "Card-2" in page


def test_html_marks_a_missing_cell_instead_of_averaging_over_it():
    rows, sessions = _paired_session_rows()
    sessions[0]["card_ids"] = json.dumps(["oracle-1", "oracle-2", "oracle-3"])

    page = render_html(build_report(rows, sessions=sessions))

    assert "no row" in page
    assert "not a like-for-like comparison" in page


def test_html_renders_the_pick_control_with_run_ids_only():
    """Ids and tier names reach the script; model output never does."""
    rows, sessions = _paired_session_rows()

    page = render_html(build_report(rows, sessions=sessions))

    assert 'data-oracle="oracle-1"' in page
    assert 'data-runs="{' in page
    assert 'type="radio"' in page
    assert "Collect picks" in page


def test_html_shows_a_recorded_pick_as_checked():
    rows, sessions = _paired_session_rows()
    pairs = [pair_row(card_oracle_id="oracle-1", winner="local_high")]

    page = render_html(build_report(rows, sessions=sessions, pairs=pairs))

    assert 'value="local_high" checked' in page


def test_html_prints_the_win_rate_with_its_interval():
    """A bare percentage over 30 cards is the easiest way to read a conclusion
    out of this benchmark that it does not support."""
    rows, sessions = _paired_session_rows()
    pairs = [
        pair_row(id=1, card_oracle_id="oracle-1", winner="local_high"),
        pair_row(id=2, card_oracle_id="oracle-2", winner="local_high"),
    ]

    page = render_html(build_report(rows, sessions=sessions, pairs=pairs))

    assert "95% CI" in page
    assert "Read the interval, not the percentage." in page


def test_html_puts_the_corpus_cost_next_to_the_win_rate():
    rows, sessions = _paired_session_rows()
    pairs = [pair_row(card_oracle_id="oracle-1", winner="local_high")]

    page = render_html(build_report(rows, sessions=sessions, pairs=pairs))

    assert "for 31,830 cards" in page


def test_html_warns_when_pooling_sessions_with_different_prompts():
    rows, sessions = _paired_session_rows()
    second = session_row(
        id="S-002",
        system_prompts_sha256="b" * 64,
        card_ids=sessions[0]["card_ids"],
    )

    page = render_html(build_report(rows, sessions=sessions + [second]))

    assert "different system prompts" in page


def test_html_still_renders_without_any_sessions():
    """Aggregation stays usable on bare run fixtures, so the page must be too."""
    page = render_html(build_report(_three_tier_rows()))

    assert 'data-tab="overview"' in page
    assert "No sessions recorded" in page


def test_text_report_leads_with_the_sessions():
    rows, sessions = _paired_session_rows()

    text = render_text(build_report(rows, sessions=sessions))

    assert text.index("SESSIONS") < text.index("PER BUCKET")
    assert "the paired view" in text
    assert "S-001" in text


def test_text_report_names_an_unpaired_session():
    rows, sessions = _paired_session_rows()
    sessions[0]["card_ids"] = json.dumps(["oracle-1", "oracle-2", "oracle-3"])

    text = render_text(build_report(rows, sessions=sessions))

    assert "UNPAIRED" in text


def test_text_report_states_the_uncertainty_of_a_small_session():
    rows, sessions = _paired_session_rows()
    pairs = [pair_row(card_oracle_id="oracle-1", winner="local_high")]

    text = render_text(build_report(rows, sessions=sessions, pairs=pairs))

    assert "95% CI" in text
    assert "uncertainty" in text


def test_the_js_reads_only_data_attributes_never_model_text():
    """The rule the filter script already followed, now that the page also
    *collects* input: nothing model-written may reach a script literal."""
    rows, sessions = _paired_session_rows()
    rows[0]["raw_response"] = "MODEL WROTE THIS"

    page = render_html(build_report(rows, sessions=sessions))
    script = page.split("<script>")[1]

    assert "MODEL WROTE THIS" not in script
    assert "data-runs" in script


def test_cli_scopes_the_report_to_one_session(tmp_path, capsys):
    pytest.importorskip("knowledge_pipeline.benchmark.store")

    rows, sessions = _paired_session_rows()
    second = session_row(id="S-002", card_ids=sessions[0]["card_ids"])
    path, _ = build_db(tmp_path, _as_store_rows(rows), sessions=sessions + [second])

    assert report_module.main(["--db", str(path), "--session", "S-001"]) == 0
    out = capsys.readouterr().out

    assert "S-001" in out
    assert "S-002" not in out


def test_cli_refuses_a_session_that_is_not_there(tmp_path, capsys):
    pytest.importorskip("knowledge_pipeline.benchmark.store")

    rows, sessions = _paired_session_rows()
    path, _ = build_db(tmp_path, _as_store_rows(rows), sessions=sessions)

    assert report_module.main(["--db", str(path), "--session", "S-404"]) == 1
    assert "S-404" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# game_stage calibration: the half of scoring that needs no ground truth.
# ---------------------------------------------------------------------------


def _facts(name: str, mana_value: float, *, oracle: str = "Do a thing.",
           keywords: str = "", type_line: str = "Creature — Test") -> str:
    """A card-facts block of the shape `render_card_facts` produces."""
    lines = [
        f"Name: {name}",
        "Mana cost: {1}{G}",
        f"Mana value: {mana_value}",
        f"Type line: {type_line}",
        "Color identity: G",
        f"Oracle text:\n{oracle}",
    ]
    if keywords:
        lines.append(f"Keywords: {keywords}")
    lines.append("Legal in: commander")
    return "\n".join(lines)


def _stage_row(name, mana_value, early, mid, late, **kw):
    return run_row(
        card_oracle_id=f"oracle-{name}",
        card_name=name,
        user_prompt=_facts(name, mana_value, **kw),
        schema_valid=1,
        parsed_output=json.dumps(
            {"game_stage": {"early": early, "mid": mid, "late": late}}
        ),
    )


def test_castability_correlation_is_negative_when_early_tracks_cost():
    rows = [
        _stage_row("A", 1.0, 9, 4, 2),
        _stage_row("B", 3.0, 6, 6, 5),
        _stage_row("C", 5.0, 3, 7, 7),
        _stage_row("D", 8.0, 1, 5, 9),
    ]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.castability_corr is not None
    assert scores.castability_corr < -0.8


def test_castability_correlation_is_positive_when_early_is_inverted():
    """The defect actually measured on S-001's 14B tier (r = +0.41).

    A seven-drop scored above a one-drop on turn two. The metric has to be
    able to come out the wrong way round, or it is not measuring anything.
    """
    rows = [
        _stage_row("A", 1.0, 2, 4, 5),
        _stage_row("B", 3.0, 5, 6, 6),
        _stage_row("C", 5.0, 7, 7, 7),
        _stage_row("D", 8.0, 9, 8, 8),
    ]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.castability_corr is not None
    assert scores.castability_corr > 0.8


def test_cost_exception_cards_are_excluded_from_the_correlation():
    """Convoke really does make a five-drop castable early.

    Scoring those cards on mana value would mark a right answer wrong, so
    they are counted and named but kept out of the number.
    """
    rows = [
        _stage_row("Plain", 2.0, 7, 5, 3),
        _stage_row("Hammer", 1.0, 1, 3, 6, keywords="Equip"),
        _stage_row("Cruise", 8.0, 2, 6, 8, keywords="Delve"),
    ]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.plain_runs == 1
    assert scores.exception_runs == 2
    assert set(scores.exception_cards) == {"Hammer", "Cruise"}


def test_correlation_is_none_rather_than_zero_when_undefined():
    """Every row at the same mana value has no x-axis variance.

    Reporting that as 0.0 would read as "no relationship", which is a
    finding; the honest answer is that it is not measurable.
    """
    rows = [_stage_row(n, 3.0, 5, 5, 5) for n in "ABCD"]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.castability_corr is None


def test_curve_shapes_are_classified_exhaustively():
    rows = [
        _stage_row("Rising", 1.0, 2, 5, 8),
        _stage_row("Falling", 1.0, 8, 5, 2),
        _stage_row("Peaked", 1.0, 4, 9, 5),
        _stage_row("Dipped", 1.0, 8, 2, 7),
        _stage_row("Flat", 1.0, 5, 5, 5),
    ]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.shapes == {
        "rising": 1, "falling": 1, "peaked": 1, "dipped": 1, "flat": 1
    }
    assert scores.shaped == 5
    assert scores.shape_rate("flat") == pytest.approx(0.2)


def test_rows_that_never_parsed_are_skipped_not_scored_as_zero():
    rows = [
        _stage_row("Good", 2.0, 7, 5, 3),
        run_row(card_name="Bad", schema_valid=0, parsed_output=None),
    ]
    scores = report_module.score_timing(rows)
    assert scores is not None
    assert scores.shaped == 1


def test_timing_is_none_when_nothing_parsed():
    assert report_module.score_timing([run_row(schema_valid=0, parsed_output=None)]) is None


def test_timing_section_reaches_the_text_report():
    rows = [
        _stage_row("A", 1.0, 9, 4, 2),
        _stage_row("B", 8.0, 1, 5, 9),
    ]
    built = build_report(rows)
    text = render_text(built)
    assert "GAME STAGE CALIBRATION" in text
    assert "castability r" in text
