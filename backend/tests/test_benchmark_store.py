"""The standalone benchmark store: schema, sessions, pairs, filters, demo data.

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
    BenchmarkSession,
    PairVerdict,
    SeedRefusedError,
    StaleStoreError,
    UnknownRunError,
    UnknownSessionError,
)

# Every run belongs to a session, so the fixtures open one first. `S-001` is
# what `make_run` points at unless a test says otherwise.
DEFAULT_SESSION_ID = "S-001"


def make_session(**overrides) -> BenchmarkSession:
    session = BenchmarkSession(
        buckets=("easy",),
        cards_per_bucket=2,
        card_ids=("oracle-1", "oracle-2"),
        tiers=("local_low", "local_high"),
        models={"local_low": "qwen:7b", "local_high": "qwen:14b"},
        draw_seed=7,
        num_ctx=12288,
        temperature=0.0,
        model_seed=20261001,
        # Both of production's system blocks, and the two output schemas they
        # decode against: the comparability key covers all four, because a
        # changed grammar makes two sessions incomparable just as surely as a
        # changed prompt does.
        system_prompts={
            "one": "TAXONOMY + GAME STAGE CALIBRATION",
            "two": "RATING RUBRIC",
        },
        output_schemas={"one": {"title": "PassOne"}, "two": {"title": "PassTwo"}},
        label="fixture",
        sample_path="sample.json",
        sample_seed=20261001,
    )
    return replace(session, **overrides)


@pytest.fixture
def conn(tmp_path):
    """A benchmark store in a throwaway directory, holding one open session."""
    connection = store.connect(tmp_path / "benchmark.db")
    try:
        store.create_session(
            connection, make_session(), session_id=DEFAULT_SESSION_ID
        )
        yield connection
    finally:
        connection.close()


def make_run(**overrides) -> BenchmarkRun:
    run = BenchmarkRun(
        session_id=DEFAULT_SESSION_ID,
        card_oracle_id="oracle-1",
        card_name="Sol Ring",
        bucket="easy",
        model_tier="frontier",
        model_name="claude-test-model",
        # Defaults to pass one, the call every card makes; a test about the
        # rating call passes `pass_name="two"` explicitly.
        pass_name="one",
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


def test_connect_creates_the_file_and_the_tables(tmp_path):
    """A fresh path just works — no separate `init` step to forget."""
    path = tmp_path / "nested" / "benchmark.db"
    connection = store.connect(path)
    try:
        assert path.exists()
        assert store.run_count(connection) == 0
        assert store.session_count(connection) == 0
        assert store.pair_count(connection) == 0
    finally:
        connection.close()


def test_connect_stamps_the_schema_version(tmp_path):
    connection = store.connect(tmp_path / "benchmark.db")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == store.SCHEMA_VERSION
    finally:
        connection.close()


def test_a_store_from_an_older_schema_is_refused_not_half_upgraded(tmp_path):
    """The one guard that keeps "delete the file and re-run" honest.

    Every statement in the DDL is IF NOT EXISTS, so opening a version-1 store
    would leave its session-less `benchmark_runs` in place and then fail much
    later on a column that does not exist. The refusal has to name the file,
    because deleting it is the whole fix.
    """
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("CREATE TABLE benchmark_runs (id INTEGER PRIMARY KEY)")
        legacy.execute("PRAGMA user_version = 1")
        legacy.commit()
    finally:
        legacy.close()

    with pytest.raises(StaleStoreError) as caught:
        store.connect(path)

    assert "legacy.db" in str(caught.value)
    assert "delete" in str(caught.value).lower()


def test_a_store_from_a_newer_schema_is_refused_too(tmp_path):
    """The other direction: don't write version-2 rows into version-3 tables."""
    path = tmp_path / "future.db"
    connection = store.connect(path)
    connection.execute("PRAGMA user_version = %d" % (store.SCHEMA_VERSION + 1))
    connection.commit()
    connection.close()

    with pytest.raises(StaleStoreError) as caught:
        store.connect(path)

    assert "newer" in str(caught.value)


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
    assert row["session_id"] == DEFAULT_SESSION_ID
    assert row["user_prompt"] == run.user_prompt
    assert row["raw_response"] == run.raw_response
    assert row["input_tokens"] == run.input_tokens
    assert row["output_tokens"] == run.output_tokens
    assert row["latency_ms"] == run.latency_ms
    assert row["load_ms"] == run.load_ms
    assert row["prompt_eval_ms"] == run.prompt_eval_ms
    assert row["eval_ms"] == run.eval_ms
    assert row["cost_usd"] == pytest.approx(run.cost_usd)


def test_the_system_prompt_is_stored_once_on_the_session_not_per_run(conn):
    """It used to be a ~36KB column on every row -- 1.8MB for 45 rows.

    It now lives on the session and is joined back in under the same name, so
    readers are unaffected and the tracked file stays reviewable. With two
    passes there are two blocks, and the join gives each row the one its own
    pass was sent -- not a map the reader has to index.
    """
    store.record_run(conn, make_run(pass_name="one"))
    store.record_run(conn, make_run(pass_name="two"))

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(benchmark_runs)").fetchall()
    }
    assert "system_prompt" not in columns
    assert "system_prompts" not in columns

    by_pass = {row["pass_name"]: row for row in store.iter_runs(conn)}
    assert by_pass["one"]["system_prompt"] == "TAXONOMY + GAME STAGE CALIBRATION"
    assert by_pass["two"]["system_prompt"] == "RATING RUBRIC"


def test_phase_timings_default_to_zero_meaning_unknown(conn):
    """A backend reporting no split leaves them 0, which readers must treat as
    unknown rather than as instant."""
    store.record_run(conn, make_run())
    row = store.iter_runs(conn)[0]

    assert (row["load_ms"], row["prompt_eval_ms"], row["eval_ms"]) == (0, 0, 0)


def test_schema_valid_is_stored_as_an_integer(conn):
    """SQLite has no bool. Storing 0/1 explicitly is what lets the CHECK
    constraint mean anything."""
    store.record_run(conn, make_run(schema_valid=True))
    store.record_run(
        conn,
        make_run(
            schema_valid=False, parsed_output=None, card_oracle_id="oracle-2"
        ),
    )

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


def test_seed_demo_inserts_six_rows_per_card(conn):
    """Three tiers x two passes, which is the shape a real session has."""
    inserted = store.seed_demo(conn, cards=12)

    assert inserted == 72
    assert store.run_count(conn) == 72


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

    # 4 cards across three bucket sessions is one card each:
    # 3 sessions x 3 tiers x 2 passes.
    assert store.run_count(conn) == 18
    # Three demo sessions, plus the fixture's own.
    assert store.session_count(conn) == 4


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
    assert cli.main(["--db", str(cli_db), "seed-demo", "--cards", "12"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(cli_db), "init"]) == 0
    assert "72" in capsys.readouterr().out


def test_cli_seed_demo_refuses_over_real_runs(cli_db, capsys):
    connection = store.connect(cli_db)
    try:
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
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
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
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
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
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


# --------------------------------------------------------------- sessions ---
#
# The structure the rest of the harness hangs off: a session is the only scope
# whose tiers are known to have answered the same cards, so these tests are
# about the facts it has to keep straight rather than about convenience.


def test_create_session_round_trips_its_provenance(conn):
    session = make_session(
        buckets=("hard",),
        cards_per_bucket=3,
        card_ids=("a", "b", "c"),
        label="smoke",
    )
    session_id = store.create_session(conn, session, session_id="S-002")
    row = store.get_session(conn, session_id)

    assert row["status"] == "running"
    assert row["finished_at"] is None
    assert store.session_buckets(row) == ("hard",)
    assert store.session_card_ids(row) == ("a", "b", "c")
    assert store.session_tiers(row) == ("local_low", "local_high")
    assert store.session_models(row)["local_high"] == "qwen:14b"
    assert row["num_ctx"] == 12288
    assert row["temperature"] == pytest.approx(0.0)
    assert row["model_seed"] == 20261001
    assert row["draw_seed"] == 7
    assert row["label"] == "smoke"


def test_the_card_set_is_frozen_before_any_call(conn):
    """`card_ids` is the session's one irreplaceable decision.

    Written at creation, not derived from the rows afterwards -- a list built
    from recorded runs could only ever describe a complete session, which is
    exactly the reassurance a partial one must not be able to give.
    """
    store.create_session(
        conn, make_session(card_ids=("x", "y")), session_id="S-002"
    )

    row = store.get_session(conn, "S-002")
    assert store.session_card_ids(row) == ("x", "y")
    assert store.iter_runs(conn, session_id="S-002") == []


def test_the_prompt_fingerprint_is_recorded_and_stable(conn):
    session = make_session()
    store.create_session(conn, session, session_id="S-002")
    row = store.get_session(conn, "S-002")

    assert row["system_prompts_sha256"] == store.fingerprint_payload(
        dict(session.system_prompts)
    )
    # A change to *either* block moves it, which is the point of fingerprinting
    # the map rather than one string.
    revised = dict(session.system_prompts) | {"two": "RATING RUBRIC (revised)"}
    assert row["system_prompts_sha256"] != store.fingerprint_payload(revised)

    # And the grammars are fingerprinted separately, so a reader can tell which
    # of the two changed. The store went without this once, and two sessions
    # with an identical prompt and a changed `required` list were treated as
    # comparable (`docs/lessons-learned.md`).
    assert row["output_schemas_sha256"] == store.fingerprint_payload(
        dict(session.output_schemas)
    )
    assert row["output_schemas_sha256"] != row["system_prompts_sha256"]


def test_session_ids_are_sequential_and_skip_demo_sessions(conn):
    assert store.next_session_id(conn) == "S-002"  # the fixture holds S-001

    store.create_session(conn, make_session(), session_id="S-D09")
    assert store.next_session_id(conn) == "S-002"

    store.create_session(conn, make_session())
    assert store.next_session_id(conn) == "S-003"


def test_finish_session_stamps_the_status_and_time(conn):
    store.finish_session(conn, DEFAULT_SESSION_ID, status="complete")
    row = store.get_session(conn, DEFAULT_SESSION_ID)

    assert row["status"] == "complete"
    assert row["finished_at"] is not None


def test_finish_session_rejects_a_status_outside_the_vocabulary(conn):
    with pytest.raises(ValueError) as caught:
        store.finish_session(conn, DEFAULT_SESSION_ID, status="finished")

    assert "complete" in str(caught.value)


def test_finish_session_raises_on_an_unknown_session(conn):
    with pytest.raises(UnknownSessionError):
        store.finish_session(conn, "S-404", status="complete")


def test_a_run_against_an_unknown_session_is_refused(conn):
    """Named error, not an IntegrityError about a constraint.

    Runs with no session would be rows no tab in the report could ever show,
    so this is a refusal rather than a warning.
    """
    with pytest.raises(UnknownSessionError) as caught:
        store.record_run(conn, make_run(session_id="S-404"))

    assert "S-404" in str(caught.value)


def test_one_cell_of_the_grid_cannot_be_recorded_twice(conn):
    """A duplicate (session, card, tier) would double-weight that cell."""
    store.record_run(conn, make_run(model_tier="local_low"))

    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(conn, make_run(model_tier="local_low"))


def test_missing_cells_reports_the_holes_in_the_grid(conn):
    """What decides `complete` vs. `partial`.

    The grid is three-dimensional now -- card x tier x pass -- because a card
    whose rating call was interrupted has a real hole in it, and a two-column
    key would report that card finished.
    """
    store.record_run(
        conn,
        make_run(card_oracle_id="oracle-1", model_tier="local_low", pass_name="one"),
    )

    missing = store.missing_cells(
        conn,
        DEFAULT_SESSION_ID,
        card_ids=("oracle-1", "oracle-2"),
        tiers=("local_low", "local_high"),
    )

    assert set(missing) == {
        ("oracle-1", "local_low", "two"),
        ("oracle-1", "local_high", "one"),
        ("oracle-1", "local_high", "two"),
        ("oracle-2", "local_low", "one"),
        ("oracle-2", "local_low", "two"),
        ("oracle-2", "local_high", "one"),
        ("oracle-2", "local_high", "two"),
    }


def test_missing_cells_is_empty_for_a_full_grid(conn):
    for card_id in ("oracle-1", "oracle-2"):
        for tier in ("local_low", "local_high"):
            for pass_name in store.PASSES:
                store.record_run(
                    conn,
                    make_run(
                        card_oracle_id=card_id,
                        model_tier=tier,
                        pass_name=pass_name,
                    ),
                )

    assert (
        store.missing_cells(
            conn,
            DEFAULT_SESSION_ID,
            card_ids=("oracle-1", "oracle-2"),
            tiers=("local_low", "local_high"),
        )
        == []
    )


def test_iter_runs_filters_by_session(conn):
    store.create_session(conn, make_session(), session_id="S-002")
    store.record_run(conn, make_run())
    store.record_run(conn, make_run(session_id="S-002"))

    assert len(store.iter_runs(conn, session_id=DEFAULT_SESSION_ID)) == 1
    assert len(store.iter_runs(conn, session_id="S-002")) == 1
    assert len(store.iter_runs(conn)) == 2


def test_recorded_cells_scopes_to_one_session(conn):
    """Resume must never skip a card because *another* session ran it.

    That cross-session drift is the defect sessions exist to prevent, so the
    query driving resume is scoped rather than global.
    """
    store.create_session(conn, make_session(), session_id="S-002")
    store.record_run(conn, make_run(model_tier="local_low"))

    assert store.recorded_cells(conn, DEFAULT_SESSION_ID) == {
        ("oracle-1", "local_low", "one")
    }
    assert store.recorded_cells(conn, "S-002") == set()


# ----------------------------------------------------------- pair verdicts ---


def make_pair(**overrides) -> PairVerdict:
    pair = PairVerdict(
        session_id=DEFAULT_SESSION_ID,
        card_oracle_id="oracle-1",
        card_name="Sol Ring",
        runs={"local_low": 1, "local_high": 2},
        winner="local_high",
        notes="14B read the triggered ability correctly.",
    )
    return replace(pair, **overrides)


def test_record_pair_round_trips(conn):
    store.record_pair(conn, make_pair())
    row = store.iter_pairs(conn)[0]

    assert row["session_id"] == DEFAULT_SESSION_ID
    assert row["card_oracle_id"] == "oracle-1"
    assert row["winner"] == "local_high"
    assert json.loads(row["runs"]) == {"local_low": 1, "local_high": 2}
    assert row["decided_at"] is not None


def test_record_pair_points_at_the_runs_it_was_formed_against(conn):
    """A re-run writes new row ids, which makes a stale verdict visibly stale
    rather than silently reattached to answers nobody judged."""
    store.record_pair(conn, make_pair(runs={"local_low": 7, "local_high": 8}))

    assert json.loads(store.iter_pairs(conn)[0]["runs"]) == {
        "local_low": 7,
        "local_high": 8,
    }


def test_re_deciding_a_card_replaces_the_earlier_pick(conn):
    """A judgment is a current opinion, like `set_verdict`s -- not a second
    opinion silently double-counted in the win rate."""
    store.record_pair(conn, make_pair(winner="local_low"))
    store.record_pair(conn, make_pair(winner="local_high", notes="changed my mind"))

    pairs = store.iter_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["winner"] == "local_high"
    assert pairs[0]["notes"] == "changed my mind"


def test_tie_and_both_bad_are_both_recordable_and_distinct(conn):
    store.record_pair(conn, make_pair(winner="tie"))
    store.record_pair(conn, make_pair(card_oracle_id="oracle-2", winner="both_bad"))

    assert {row["winner"] for row in store.iter_pairs(conn)} == {"tie", "both_bad"}


def test_record_pair_rejects_an_outcome_outside_the_vocabulary(conn):
    with pytest.raises(ValueError) as caught:
        store.record_pair(conn, make_pair(winner="local_medium"))

    assert "tie" in str(caught.value)


def test_record_pair_raises_on_an_unknown_session(conn):
    with pytest.raises(UnknownSessionError):
        store.record_pair(conn, make_pair(session_id="S-404"))


def test_iter_pairs_filters_by_session(conn):
    store.create_session(conn, make_session(), session_id="S-002")
    store.record_pair(conn, make_pair())
    store.record_pair(conn, make_pair(session_id="S-002"))

    assert len(store.iter_pairs(conn, session_id="S-002")) == 1
    assert store.pair_count(conn, "S-002") == 1
    assert store.pair_count(conn) == 2


def test_deleting_a_session_cascades_to_its_runs_and_pairs(conn):
    """What lets `seed_demo` replace demo data by deleting demo sessions."""
    store.record_run(conn, make_run())
    store.record_pair(conn, make_pair())

    conn.execute(
        "DELETE FROM benchmark_sessions WHERE id = ?", (DEFAULT_SESSION_ID,)
    )
    conn.commit()

    assert store.run_count(conn) == 0
    assert store.pair_count(conn) == 0


# ------------------------------------------------------ demo data, sessions ---


def test_seed_demo_creates_one_paired_session_per_bucket(conn):
    """The demo store has to exercise the tab bar and the paired view, which a
    single pooled blob of rows did not."""
    store.seed_demo(conn, cards=12)

    demo = [
        row
        for row in store.iter_sessions(conn)
        if str(row["id"]).startswith(store.DEMO_SESSION_PREFIX)
    ]
    assert len(demo) == len(store.BUCKETS)
    for row in demo:
        session_id = str(row["id"])
        assert row["status"] == "complete"
        assert (
            store.missing_cells(
                conn,
                session_id,
                card_ids=store.session_card_ids(row),
                tiers=store.session_tiers(row),
            )
            == []
        )


def test_seed_demo_records_some_picks(conn):
    store.seed_demo(conn, cards=12)

    assert store.pair_count(conn) > 0
    assert all(
        row["winner"] in store.PAIR_OUTCOMES for row in store.iter_pairs(conn)
    )


def test_seed_demo_spreads_verdicts_across_tiers(conn):
    """A stride of 3 over 3 tiers put every demo verdict on `frontier`, which
    left both local tiers at 0% review coverage -- a per-tier shape no real
    review would have."""
    store.seed_demo(conn, cards=12)

    reviewed_tiers = {
        row["model_tier"] for row in store.iter_runs(conn, reviewed=True)
    }
    assert len(reviewed_tiers) > 1


# -------------------------------------------------------- CLI: new surfaces ---


def test_cli_sessions_lists_them_with_the_pairing_column(cli_db, capsys):
    assert cli.main(["--db", str(cli_db), "seed-demo", "--cards", "12"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(cli_db), "sessions"]) == 0
    out = capsys.readouterr().out
    assert "S-D01" in out
    assert "paired" in out


def test_cli_sessions_on_an_empty_store_says_how_to_make_one(cli_db, capsys):
    assert cli.main(["--db", str(cli_db), "sessions"]) == 0
    assert "--bucket" in capsys.readouterr().out


def test_cli_pair_verdict_records_a_single_pick(cli_db, capsys):
    connection = store.connect(cli_db)
    try:
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
        store.record_run(connection, make_run(model_tier="local_low"))
        store.record_run(connection, make_run(model_tier="local_high"))
    finally:
        connection.close()

    assert (
        cli.main(
            [
                "--db",
                str(cli_db),
                "pair-verdict",
                "--session",
                DEFAULT_SESSION_ID,
                "--card",
                "oracle-1",
                "--winner",
                "local_high",
            ]
        )
        == 0
    )

    connection = store.connect(cli_db)
    try:
        pair = store.iter_pairs(connection)[0]
        assert pair["winner"] == "local_high"
        # The pick points at both runs it was formed against.
        assert set(json.loads(pair["runs"])) == {"local_low", "local_high"}
    finally:
        connection.close()


def test_cli_pair_verdict_reads_the_report_pages_json(cli_db, tmp_path, capsys):
    connection = store.connect(cli_db)
    try:
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
    finally:
        connection.close()

    picks = tmp_path / "picks.json"
    picks.write_text(
        json.dumps(
            {
                "session_id": DEFAULT_SESSION_ID,
                "picks": [
                    {
                        "card_oracle_id": "oracle-1",
                        "card_name": "Sol Ring",
                        "winner": "local_high",
                        "runs": {"local_low": 1, "local_high": 2},
                        "notes": "clearer summary",
                    },
                    {
                        "card_oracle_id": "oracle-2",
                        "card_name": "Arcane Signet",
                        "winner": "tie",
                        "runs": {},
                        "notes": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["--db", str(cli_db), "pair-verdict", "--from", str(picks)]) == 0
    assert "Recorded 2" in capsys.readouterr().out


def test_cli_pair_verdict_skips_a_bad_pick_and_keeps_the_rest(cli_db, tmp_path, capsys):
    """The file arrives by copy-paste, so a partly-mangled one is likelier than
    a malformed page. Record what is sound, name what is not."""
    connection = store.connect(cli_db)
    try:
        store.create_session(connection, make_session(), session_id=DEFAULT_SESSION_ID)
    finally:
        connection.close()

    picks = tmp_path / "picks.json"
    picks.write_text(
        json.dumps(
            {
                "session_id": DEFAULT_SESSION_ID,
                "picks": [
                    {"card_oracle_id": "oracle-1", "winner": "local_low"},
                    {"card_oracle_id": "oracle-2", "winner": "not_a_tier"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["--db", str(cli_db), "pair-verdict", "--from", str(picks)]) == 0
    captured = capsys.readouterr()
    assert "Recorded 1" in captured.out
    assert "not_a_tier" in captured.err


def test_cli_pair_verdict_refuses_a_session_that_is_not_there(cli_db, tmp_path, capsys):
    picks = tmp_path / "picks.json"
    picks.write_text(
        json.dumps({"session_id": "S-404", "picks": []}), encoding="utf-8"
    )

    assert cli.main(["--db", str(cli_db), "pair-verdict", "--from", str(picks)]) == 1
    assert "S-404" in capsys.readouterr().err


def test_cli_pair_verdict_needs_a_whole_pick_or_a_file(cli_db, capsys):
    assert (
        cli.main(["--db", str(cli_db), "pair-verdict", "--session", "S-001"]) == 2
    )
    assert "--card" in capsys.readouterr().err


def test_cli_refuses_a_stale_store_without_a_traceback(tmp_path, capsys):
    """A maintainer needs the one sentence that names the file, not a stack."""
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("CREATE TABLE benchmark_runs (id INTEGER PRIMARY KEY)")
        legacy.execute("PRAGMA user_version = 1")
        legacy.commit()
    finally:
        legacy.close()

    assert cli.main(["--db", str(path), "init"]) == 1
    error = capsys.readouterr().err
    assert "legacy.db" in error
    assert "Traceback" not in error
