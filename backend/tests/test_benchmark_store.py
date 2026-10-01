"""The standalone benchmark run log: schema, round-trip, filters, demo data.

Every test opens its own store under `tmp_path`. None of them may touch
`knowledge_pipeline/benchmark/benchmark.db` — that file is a maintainer's
real benchmark results, and a test that wrote to it would destroy data the
run it records cost real money and hours to produce. Nothing here uses the
`knowledge_session`/`local_session` fixtures either: the point of this store
is that it is attached to neither plane.
"""

import json
import sqlite3
from dataclasses import replace

import pytest

from knowledge_pipeline.benchmark import __main__ as cli, store
from knowledge_pipeline.benchmark.store import (
    BenchmarkRun,
    SeedRefusedError,
    UnknownRunError,
)


@pytest.fixture
def conn(tmp_path):
    """A benchmark store in a throwaway directory."""
    connection = store.connect(tmp_path / "benchmark.db")
    try:
        yield connection
    finally:
        connection.close()


def make_run(**overrides) -> BenchmarkRun:
    run = BenchmarkRun(
        card_oracle_id="oracle-1",
        card_name="Sol Ring",
        bucket="easy",
        model_tier="frontier",
        model_name="claude-test-model",
        system_prompt="TAXONOMY + ANCHORS + RUBRIC",
        user_prompt="Name: Sol Ring\nMana cost: {1}",
        raw_response='{"roles": ["Ramp"]}',
        schema_valid=True,
        parsed_output={"roles": ["Ramp"], "power_rating": 9.0},
        input_tokens=1200,
        output_tokens=180,
        latency_ms=1450,
        cost_usd=0.0052,
    )
    return replace(run, **overrides)


# ---------------------------------------------------------------- schema ---


def test_connect_creates_the_file_and_the_table(tmp_path):
    """A fresh path just works — no separate `init` step to forget."""
    path = tmp_path / "nested" / "benchmark.db"
    connection = store.connect(path)
    try:
        assert path.exists()
        assert store.run_count(connection) == 0
    finally:
        connection.close()


def test_initialize_is_idempotent(conn):
    """`connect` runs it every time, so a second call must be a no-op — not
    an error, and not a table recreated out from under existing rows."""
    run_id = store.record_run(conn, make_run())

    store.initialize(conn)
    store.initialize(conn)

    assert store.run_count(conn) == 1
    assert store.iter_runs(conn)[0]["id"] == run_id


def test_rows_come_back_indexable_by_column_name(conn):
    store.record_run(conn, make_run())
    row = store.iter_runs(conn)[0]

    assert isinstance(row, sqlite3.Row)
    assert row["card_name"] == "Sol Ring"


# ------------------------------------------------------------ round-trip ---


def test_record_run_round_trips_every_field(conn):
    run = make_run()
    run_id = store.record_run(conn, run)
    row = store.iter_runs(conn)[0]

    assert row["id"] == run_id
    assert row["card_oracle_id"] == run.card_oracle_id
    assert row["card_name"] == run.card_name
    assert row["bucket"] == run.bucket
    assert row["model_tier"] == run.model_tier
    assert row["model_name"] == run.model_name
    assert row["system_prompt"] == run.system_prompt
    assert row["user_prompt"] == run.user_prompt
    assert row["raw_response"] == run.raw_response
    assert row["input_tokens"] == run.input_tokens
    assert row["output_tokens"] == run.output_tokens
    assert row["latency_ms"] == run.latency_ms
    assert row["cost_usd"] == pytest.approx(run.cost_usd)


def test_schema_valid_is_stored_as_an_integer(conn):
    """SQLite has no bool. Storing 0/1 explicitly is what lets the CHECK
    constraint mean anything."""
    store.record_run(conn, make_run(schema_valid=True))
    store.record_run(conn, make_run(schema_valid=False, parsed_output=None))

    assert [row["schema_valid"] for row in store.iter_runs(conn)] == [1, 0]


def test_parsed_output_comes_back_as_json_text_not_a_dict(conn):
    """The contract the reporting layer codes against: raw `sqlite3.Row`,
    `parsed_output` still encoded. Decoding is the caller's `json.loads`."""
    store.record_run(conn, make_run())
    raw = store.iter_runs(conn)[0]["parsed_output"]

    assert isinstance(raw, str)
    assert json.loads(raw) == {"roles": ["Ramp"], "power_rating": 9.0}


def test_parsed_output_is_null_for_an_invalid_response(conn):
    store.record_run(
        conn,
        make_run(schema_valid=False, parsed_output=None, raw_response="Sure! Here's..."),
    )

    assert store.iter_runs(conn)[0]["parsed_output"] is None


def test_run_at_is_stamped_and_verdict_columns_start_empty(conn):
    store.record_run(conn, make_run())
    row = store.iter_runs(conn)[0]

    # ISO-8601 UTC so the column sorts chronologically as text.
    assert row["run_at"].endswith("Z")
    assert row["human_verdict"] is None
    assert row["human_notes"] is None
    assert row["reviewed_at"] is None


def test_ids_are_assigned_by_the_store_and_increase(conn):
    first = store.record_run(conn, make_run(card_oracle_id="a"))
    second = store.record_run(conn, make_run(card_oracle_id="b"))

    assert second > first


# ---------------------------------------------------- closed vocabularies ---


def test_check_constraint_rejects_an_unknown_bucket(conn):
    """The `Literal` hints vanish at runtime; the database is what actually
    stops a fourth bucket appearing and skewing every per-bucket aggregate."""
    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(conn, make_run(bucket="impossible"))

    assert store.run_count(conn) == 0


def test_check_constraint_rejects_an_unknown_model_tier(conn):
    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(conn, make_run(model_tier="frontier_model"))

    assert store.run_count(conn) == 0


def test_check_constraint_rejects_a_parse_on_an_invalid_response(conn):
    """A non-null parse on `schema_valid = 0` would mean the harness recorded
    a guess as if the model had produced it."""
    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(conn, make_run(schema_valid=False, parsed_output={"roles": []}))


def test_check_constraint_rejects_an_unknown_verdict_written_directly(conn):
    """`set_verdict` guards this too, but a report script poking the table by
    hand shouldn't be able to invent a fourth verdict."""
    run_id = store.record_run(conn, make_run())

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE benchmark_runs SET human_verdict = ? WHERE id = ?",
            ("excellent", run_id),
        )


# --------------------------------------------------------------- verdicts ---


def test_set_verdict_stamps_reviewed_at(conn):
    run_id = store.record_run(conn, make_run())

    store.set_verdict(conn, run_id, "acceptable", "Missed the better role pick.")
    row = store.iter_runs(conn)[0]

    assert row["human_verdict"] == "acceptable"
    assert row["human_notes"] == "Missed the better role pick."
    # Written in the same statement as the verdict, so the two can't disagree.
    assert row["reviewed_at"] is not None
    assert row["reviewed_at"].endswith("Z")


def test_set_verdict_notes_are_optional(conn):
    run_id = store.record_run(conn, make_run())
    store.set_verdict(conn, run_id, "good")

    row = store.iter_runs(conn)[0]
    assert row["human_verdict"] == "good"
    assert row["human_notes"] is None


def test_set_verdict_overwrites_an_earlier_read(conn):
    """A verdict is a current opinion, not an append-only log."""
    run_id = store.record_run(conn, make_run())
    store.set_verdict(conn, run_id, "good", "first pass")
    store.set_verdict(conn, run_id, "wrong", "seen against the other tiers")

    row = store.iter_runs(conn)[0]
    assert row["human_verdict"] == "wrong"
    assert row["human_notes"] == "seen against the other tiers"


def test_set_verdict_raises_on_an_unknown_run_id(conn):
    """Ids are read off a report and retyped by hand, so a wrong one is
    plausible — and a silent no-op would lose the verdict invisibly."""
    with pytest.raises(UnknownRunError):
        store.set_verdict(conn, 9999, "good")


def test_set_verdict_rejects_a_verdict_outside_the_vocabulary(conn):
    run_id = store.record_run(conn, make_run())

    with pytest.raises(ValueError):
        store.set_verdict(conn, run_id, "excellent")

    assert store.iter_runs(conn)[0]["human_verdict"] is None


# ---------------------------------------------------------------- filters ---


@pytest.fixture
def populated(conn):
    """Nine runs: three cards, one per bucket, each across all three tiers."""
    for index, bucket in enumerate(store.BUCKETS):
        for tier in store.MODEL_TIERS:
            store.record_run(
                conn,
                make_run(
                    card_oracle_id=f"oracle-{index}",
                    card_name=f"Card {index}",
                    bucket=bucket,
                    model_tier=tier,
                ),
            )
    return conn


def test_iter_runs_returns_everything_by_default(populated):
    assert len(store.iter_runs(populated)) == 9


def test_iter_runs_filters_by_bucket(populated):
    rows = store.iter_runs(populated, bucket="hard")

    assert len(rows) == 3
    assert {row["bucket"] for row in rows} == {"hard"}


def test_iter_runs_filters_by_model_tier(populated):
    rows = store.iter_runs(populated, model_tier="local_low")

    assert len(rows) == 3
    assert {row["model_tier"] for row in rows} == {"local_low"}


def test_iter_runs_filters_by_card(populated):
    """One card across every tier — the shape a per-card tier comparison needs."""
    rows = store.iter_runs(populated, card_oracle_id="oracle-1")

    assert len(rows) == 3
    assert {row["model_tier"] for row in rows} == set(store.MODEL_TIERS)


def test_iter_runs_combines_filters(populated):
    rows = store.iter_runs(populated, bucket="medium", model_tier="frontier")

    assert len(rows) == 1
    assert rows[0]["bucket"] == "medium"
    assert rows[0]["model_tier"] == "frontier"


def test_iter_runs_reviewed_filter_has_three_states(populated):
    first_id = store.iter_runs(populated)[0]["id"]
    store.set_verdict(populated, first_id, "good")

    assert len(store.iter_runs(populated, reviewed=True)) == 1
    assert len(store.iter_runs(populated, reviewed=False)) == 8
    assert len(store.iter_runs(populated, reviewed=None)) == 9


def test_iter_runs_orders_oldest_first(populated):
    ids = [row["id"] for row in store.iter_runs(populated)]
    assert ids == sorted(ids)


def test_iter_runs_returns_an_empty_list_when_nothing_matches(conn):
    assert store.iter_runs(conn, bucket="hard") == []


# -------------------------------------------------------------- demo data ---


def test_seed_demo_inserts_three_rows_per_card(conn):
    inserted = store.seed_demo(conn, cards=12)

    assert inserted == 36
    assert store.run_count(conn) == 36


def test_seed_demo_covers_every_bucket_and_tier(conn):
    """The reporting layer groups by both, so both have to be populated for
    the demo data to exercise it at all."""
    store.seed_demo(conn, cards=12)
    rows = store.iter_runs(conn)

    assert {row["bucket"] for row in rows} == set(store.BUCKETS)
    assert {row["model_tier"] for row in rows} == set(store.MODEL_TIERS)


def test_seed_demo_rows_are_obviously_fake(conn):
    store.seed_demo(conn, cards=3)
    rows = store.iter_runs(conn)

    assert all(row["card_name"].startswith("Demo Card ") for row in rows)
    assert all(row["model_name"].startswith("demo/") for row in rows)


def test_seed_demo_is_deterministic(conn, tmp_path):
    """Same seed, same rows — so a report reviewed yesterday looks the same
    today."""
    store.seed_demo(conn, cards=6)
    first = [tuple(row)[1:] for row in store.iter_runs(conn)]

    other = store.connect(tmp_path / "other.db")
    try:
        store.seed_demo(other, cards=6)
        second = [tuple(row)[1:] for row in store.iter_runs(other)]
    finally:
        other.close()

    assert first == second


def test_seed_demo_replaces_rather_than_accumulates(conn):
    """Re-seeding must not double the data, or "deterministic" would only
    hold on a fresh file."""
    store.seed_demo(conn, cards=4)
    store.seed_demo(conn, cards=4)

    assert store.run_count(conn) == 12


def test_seed_demo_leaves_some_rows_unreviewed_and_some_reviewed(conn):
    """Both sides of `iter_runs(reviewed=...)` need something in them."""
    store.seed_demo(conn, cards=12)

    assert store.iter_runs(conn, reviewed=True)
    assert store.iter_runs(conn, reviewed=False)


def test_seed_demo_produces_some_invalid_schema_rows(conn):
    """A flat 100% schema-validity rate would hide the exact metric the
    benchmark cares most about being able to report."""
    store.seed_demo(conn, cards=12)
    validity = {row["schema_valid"] for row in store.iter_runs(conn)}

    assert validity == {0, 1}


def test_seed_demo_local_tiers_are_free_and_frontier_is_not(conn):
    """`cost_usd` is 0 for the local tiers by definition — latency is the
    number that matters for them (`benchmarking-and-testing.md#run-tracking`)."""
    store.seed_demo(conn, cards=6)

    local = store.iter_runs(conn, model_tier="local_high")
    frontier = store.iter_runs(conn, model_tier="frontier")

    assert all(row["cost_usd"] == 0 for row in local)
    assert all(row["cost_usd"] > 0 for row in frontier)


def test_seed_demo_refuses_when_real_runs_are_present(conn):
    """Synthetic rows mixed into measured data would corrupt every aggregate
    the benchmark's conclusions rest on."""
    store.record_run(conn, make_run())

    with pytest.raises(SeedRefusedError):
        store.seed_demo(conn, cards=4)

    # Nothing inserted, and the real run is untouched.
    assert store.run_count(conn) == 1
    assert store.iter_runs(conn)[0]["model_name"] == "claude-test-model"


def test_seed_demo_rejects_a_nonsense_card_count(conn):
    with pytest.raises(ValueError):
        store.seed_demo(conn, cards=0)


# -------------------------------------------------------------------- CLI ---
#
# Thin coverage: the CLI is a shell over the functions above, so only the
# behaviour that isn't exercised by them is tested here -- chiefly that
# `review` refuses rather than silently spinning when nobody is there to
# answer it, the same guard `scryfall_importer`'s --reset has.


@pytest.fixture
def cli_db(tmp_path):
    """A store path for the CLI's `--db`, never the real benchmark.db."""
    return tmp_path / "cli-benchmark.db"


def test_cli_init_reports_the_path_and_count(cli_db, capsys):
    assert cli.main(["--db", str(cli_db), "init"]) == 0

    out = capsys.readouterr().out
    assert str(cli_db) in out
    assert "0" in out


def test_cli_seed_demo_then_init_shows_the_rows(cli_db, capsys):
    assert cli.main(["--db", str(cli_db), "seed-demo", "--cards", "4"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(cli_db), "init"]) == 0
    assert "12" in capsys.readouterr().out


def test_cli_seed_demo_refuses_over_real_runs(cli_db, capsys):
    connection = store.connect(cli_db)
    try:
        store.record_run(connection, make_run())
    finally:
        connection.close()

    assert cli.main(["--db", str(cli_db), "seed-demo"]) == 1
    assert "Refusing" in capsys.readouterr().err


def test_cli_review_refuses_without_a_terminal(monkeypatch, cli_db, capsys):
    """Docker, CI, cron: a prompt read at EOF is a prompt nobody answered,
    and here that would walk every run recording nothing."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    cli.main(["--db", str(cli_db), "seed-demo", "--cards", "3"])
    capsys.readouterr()

    assert cli.main(["--db", str(cli_db), "review"]) == 1
    err = capsys.readouterr().err
    assert "needs a terminal" in err
    assert "set-verdict" in err  # says what to run instead


def test_cli_review_records_a_verdict(monkeypatch, cli_db, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    connection = store.connect(cli_db)
    try:
        run_id = store.record_run(connection, make_run())
    finally:
        connection.close()

    answers = iter(["good", "Clean call.", "q"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    assert cli.main(["--db", str(cli_db), "review"]) == 0

    connection = store.connect(cli_db)
    try:
        row = store.iter_runs(connection)[0]
    finally:
        connection.close()

    assert row["id"] == run_id
    assert row["human_verdict"] == "good"
    assert row["human_notes"] == "Clean call."


def test_cli_set_verdict_is_the_non_interactive_path(cli_db, capsys):
    connection = store.connect(cli_db)
    try:
        run_id = store.record_run(connection, make_run())
    finally:
        connection.close()

    assert (
        cli.main(
            [
                "--db",
                str(cli_db),
                "set-verdict",
                "--id",
                str(run_id),
                "--verdict",
                "wrong",
                "--notes",
                "Invented a role.",
            ]
        )
        == 0
    )

    connection = store.connect(cli_db)
    try:
        row = store.iter_runs(connection)[0]
    finally:
        connection.close()

    assert row["human_verdict"] == "wrong"
    assert row["human_notes"] == "Invented a role."


def test_cli_set_verdict_rejects_an_unknown_run(cli_db, capsys):
    assert cli.main(["--db", str(cli_db), "set-verdict", "--id", "42", "--verdict", "good"]) == 1
    assert "No benchmark run with id 42" in capsys.readouterr().err


def test_cli_set_verdict_rejects_a_verdict_outside_the_vocabulary(cli_db, capsys):
    """argparse `choices`, so it fails before the store is ever opened."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--db", str(cli_db), "set-verdict", "--id", "1", "--verdict", "excellent"])

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
