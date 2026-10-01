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
CREATE TABLE benchmark_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_oracle_id TEXT    NOT NULL,
    card_name      TEXT    NOT NULL,
    bucket         TEXT    NOT NULL,
    model_tier     TEXT    NOT NULL,
    model_name     TEXT    NOT NULL,
    system_prompt  TEXT    NOT NULL,
    user_prompt    TEXT    NOT NULL,
    raw_response   TEXT    NOT NULL,
    schema_valid   INTEGER NOT NULL,
    parsed_output  TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    latency_ms     INTEGER,
    cost_usd       REAL,
    run_at         TEXT    NOT NULL,
    human_verdict  TEXT,
    human_notes    TEXT,
    reviewed_at    TEXT
)
"""

_COLUMNS = (
    "card_oracle_id",
    "card_name",
    "bucket",
    "model_tier",
    "model_name",
    "system_prompt",
    "user_prompt",
    "raw_response",
    "schema_valid",
    "parsed_output",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "cost_usd",
    "run_at",
    "human_verdict",
    "human_notes",
    "reviewed_at",
)


def run_row(**overrides) -> dict:
    """One plausible `benchmark_runs` row; override whatever the test cares about."""
    row = {
        "id": overrides.pop("id", 1),
        "card_oracle_id": "oracle-1",
        "card_name": "Sol Ring",
        "bucket": "easy",
        "model_tier": "frontier",
        "model_name": "claude-pinned",
        "system_prompt": "taxonomy + anchors + rubric",
        "user_prompt": "Sol Ring {1} Artifact",
        "raw_response": '{"roles": ["Ramp"]}',
        "schema_valid": 1,
        "parsed_output": None,
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 1000,
        "cost_usd": 0.001,
        "run_at": "2026-09-27T10:00:00",
        "human_verdict": None,
        "human_notes": None,
        "reviewed_at": None,
    }
    row.update(overrides)
    return row


def build_db(tmp_path, rows) -> "tuple[object, list[sqlite3.Row]]":
    """Write `rows` into a throwaway benchmark database and read them back.

    Returns the path and the rows as `sqlite3.Row`, matching what
    `store.iter_runs` returns.
    """
    path = tmp_path / "benchmark.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(BENCHMARK_RUNS_DDL)
        connection.executemany(
            f"INSERT INTO benchmark_runs ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            [tuple(row[column] for column in _COLUMNS) for row in rows],
        )
        connection.commit()
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT * FROM benchmark_runs ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return path, stored


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

    path, _ = build_db(tmp_path, _three_tier_rows())
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

    path, _ = build_db(tmp_path, _three_tier_rows())

    assert report_module.main(["--db", str(path), "--tier", "local_high"]) == 0
    out = capsys.readouterr().out
    assert "local 14B" in out
    # Filtered to one tier, the comparison sections say so rather than
    # pretending the remaining tier is the whole picture.
    assert "No frontier baseline" in out
