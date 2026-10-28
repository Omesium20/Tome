"""The benchmark runner: what it records, and the four things it refuses to do.

No test here touches a live Ollama server or the real `benchmark.db` — the
store fixture points at `tmp_path`, following `test_benchmark_store.py`'s rule,
and the model is a fake implementing `ObservableMetadataBackend`.

The interesting cases are the refusals, because each one is a way to end up
with a benchmark that looks fine and means nothing:

- a schema-invalid response must be *recorded*, not raised away, or the
  schema-validity rate counts only successes;
- a call that could never be made must *not* be recorded, or a server outage
  lands in that same rate;
- measured rows must never mix with `seed-demo`'s synthetic ones;
- a prompt that filled its context window must stop the run, because the
  anchor ladders are the part that got truncated.
"""

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from database.knowledge.models import Card
from knowledge_pipeline.benchmark import runner, store
from knowledge_pipeline.benchmark.runner import BenchmarkRunError
from knowledge_pipeline.benchmark.sample import CardSample, SampleCard
from knowledge_pipeline.metadata_generator.model_backend import (
    GenerationAttempt,
    MetadataGenerationError,
)
from knowledge_pipeline.metadata_generator.schema import (
    CardMetadataBlueprint,
    GameStageProfile,
    MetadataPassOne,
    MetadataPassTwo,
    Role,
    SynergyTag,
    Theme,
)

VALID_BLUEPRINT = CardMetadataBlueprint(
    mechanics="Costs {1} to cast. Tap it to add {C}{C}.",
    summary="Adds two colorless mana for one.",
    primary_role=Role.MANA_ROCK,
    roles=[Role.MANA_ROCK],
    themes=[Theme.ARTIFACTS],
    game_stage=GameStageProfile(early=9, mid=6, late=3),
    power_rating=7.5,
    strengths=["Explosive starts"],
    weaknesses=["Does nothing on its own"],
    synergy_tags=[SynergyTag.ARTIFACT_MATTERS],
)

# The same card, split the way production's two calls split it. The harness
# asks for one of these per call, never the whole blueprint, so the fixtures
# have to exist in both halves or a pass-two wiring bug would be invisible.
VALID_PASS_ONE = MetadataPassOne(
    **VALID_BLUEPRINT.model_dump(include=set(MetadataPassOne.model_fields))
)
VALID_PASS_TWO = MetadataPassTwo(
    **VALID_BLUEPRINT.model_dump(include=set(MetadataPassTwo.model_fields))
)


def make_card(oracle_id: str, name: str) -> Card:
    return Card(
        oracle_id=oracle_id,
        scryfall_id=f"print-{oracle_id}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        oracle_text="{T}: Add {C}{C}.",
        colors=[],
        color_identity=[],
        type_line="Artifact",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        keywords=[],
        image_url=None,
        layout="normal",
        legalities={"commander": "legal"},
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    )


class FakeBackend:
    """An `ObservableMetadataBackend` whose every call is scripted.

    `attempts` is consumed in order; a `MetadataGenerationError` instance in
    the list is raised rather than returned, which is how the "the call could
    not be made" path gets exercised without a socket.
    """

    name = "fake"

    def __init__(self, *, model: str = "fake/model-7b", attempts=None, default=None):
        self.model = model
        self._attempts = list(attempts or [])
        # Left as None on purpose: an unscripted call then answers whichever
        # output model it was asked for (see `generate_observed`). A single
        # fixed default would have answered every pass-two call with pass one's
        # shape, which is how a broken two-pass wiring passes its tests.
        self._default = default
        self.calls: list[tuple[str, str, object]] = []
        self.unloaded = 0

    def generate_observed(
        self, *, system: str, prompt: str, output_model=None
    ) -> GenerationAttempt:
        self.calls.append((system, prompt, output_model))
        nxt = self._attempts.pop(0) if self._attempts else None
        if isinstance(nxt, Exception):
            raise nxt
        if nxt is not None:
            return nxt
        if self._default is not None:
            return self._default
        # Unscripted calls answer whatever shape they were asked for. The
        # harness makes two calls per card with two different output models, so
        # a fake that always returned a whole blueprint would let a bug in the
        # pass-two wiring pass every test here.
        return make_attempt(parsed=default_parsed(output_model))

    def generate(self, *, system: str, prompt: str, output_model=None):
        attempt = self.generate_observed(
            system=system, prompt=prompt, output_model=output_model
        )
        if attempt.parsed is None:
            raise MetadataGenerationError(attempt.error or "invalid")
        return attempt.parsed

    def unload(self) -> None:
        self.unloaded += 1

    @property
    def recorded_calls(self) -> int:
        """Calls excluding the warm-up, which is never recorded."""
        return max(len(self.calls) - 1, 0)


def default_parsed(output_model):
    """A valid instance of whichever pass the caller asked for.

    `None` for an unrecognized model, which a fake backend then reports as a
    schema failure -- the honest outcome, and better than inventing an object
    of the wrong shape.
    """
    if output_model is MetadataPassTwo:
        return VALID_PASS_TWO
    if output_model is MetadataPassOne:
        return VALID_PASS_ONE
    if output_model is CardMetadataBlueprint:
        return VALID_BLUEPRINT
    return None


def make_attempt(
    *,
    parsed=VALID_PASS_ONE,
    raw: str | None = None,
    # Sized for pass one's prompt (~2,100 tokens), not the retired single-call
    # one (~14,100). The default has to sit well inside `DEFAULT_NUM_CTX` or
    # every test would trip the truncation guard -- and the guard's own test
    # passes a deliberately large value instead of relying on this.
    input_tokens: int = 2_100,
    output_tokens: int = 320,
    latency_ms: int = 4_200,
    error: str | None = None,
    prompt_eval_ms: int = 300,
    eval_ms: int = 3_800,
    load_ms: int = 0,
) -> GenerationAttempt:
    """Defaults model a warm, GPU-resident call with a cached prompt prefix.

    `prompt_eval_ms` and `eval_ms` matter to the residency guard, which judges
    decode rate alone — so they are explicit here rather than derived from
    `latency_ms`. The three phases deliberately leave ~100ms of `latency_ms`
    unaccounted, which is what a healthy call looks like to `timing_anomaly`:
    enough to prove the check runs, far short of tripping it.
    """
    return GenerationAttempt(
        raw_response=(
            raw
            if raw is not None
            else (parsed.model_dump_json() if parsed is not None else "{")
        ),
        parsed=parsed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=0.0,
        error=error,
        prompt_eval_ms=prompt_eval_ms,
        eval_ms=eval_ms,
        load_ms=load_ms,
    )


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    """A throwaway store. Never the real benchmark.db."""
    connection = store.connect(tmp_path / "benchmark.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def seeded_session(knowledge_session: Session) -> Session:
    for index in range(4):
        knowledge_session.add(make_card(f"oid-{index}", f"Card {index}"))
    knowledge_session.commit()
    return knowledge_session


def make_sample(count: int = 2, *, bucket: str = "easy") -> CardSample:
    return CardSample(
        cards=[
            SampleCard(oracle_id=f"oid-{i}", name=f"Card {i}", bucket=bucket)
            for i in range(count)
        ],
        reviewed=False,
        generated_at="2026-10-02T00:00:00Z",
        seed=1,
    )


TEST_SYSTEM_PROMPT = "TAXONOMY + ANCHORS + RUBRIC (test)"

# Both of production's system blocks, as a plan holds them. Short strings: the
# runner only ever measures their length and fingerprints them, and the real
# ones are rendered by `prompt.py`, which has its own tests.
TEST_SYSTEM_PROMPTS = {
    "one": TEST_SYSTEM_PROMPT,
    "two": "RATING RUBRIC (test)",
}
TEST_SCHEMAS = {"one": {"title": "PassOne"}, "two": {"title": "PassTwo"}}


def make_plan(sample: CardSample, **overrides) -> runner.SessionPlan:
    """A `SessionPlan` over every card in `sample`, with test-shaped config.

    Built directly rather than through `plan_session` so a test can state its
    exact card set; `plan_session`'s own draw is covered separately in
    `TestPlanning`.
    """
    defaults = dict(
        cards=tuple(sample.cards),
        buckets=("easy",),
        cards_per_bucket=len(sample.cards),
        tiers=("local_low",),
        models={"local_low": "fake/model-7b", "local_high": "fake/model-14b"},
        num_ctx=12_288,
        draw_seed=7,
        temperature=0.0,
        model_seed=20261001,
        system_prompts=dict(TEST_SYSTEM_PROMPTS),
        output_schemas=dict(TEST_SCHEMAS),
        sample_path="sample.json",
        sample_seed=sample.seed,
        sample_reviewed=sample.reviewed,
    )
    defaults.update(overrides)
    return runner.SessionPlan(**defaults)


def run(**overrides):
    """`run_benchmark` with the fixture-shaped defaults filled in.

    Accepts `sample=`/`tiers=`/`num_ctx=`/`cards=` and turns them into a plan,
    so a test states what it is about rather than restating the plan shape.
    Pass `plan=` directly to control it fully.
    """
    plan = overrides.pop("plan", None)
    if plan is None:
        sample = overrides.pop("sample")
        plan_kwargs = {}
        for key in ("tiers", "num_ctx", "resume_session_id", "system_prompt"):
            if key in overrides:
                plan_kwargs[key] = overrides.pop(key)
        cards = overrides.pop("cards", None)
        if cards is not None:
            plan_kwargs["cards"] = tuple(sample.cards[:cards])
            plan_kwargs["cards_per_bucket"] = cards
        plan = make_plan(sample, **plan_kwargs)
    return runner.run_benchmark(plan=plan, **overrides)


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------


class TestRecording:
    def test_records_one_row_per_card_and_pass(self, conn, seeded_session):
        """Two rows per card, because production makes two calls per card.

        The unit of the store is a *call*, not a card: pass one and pass two
        send different prompts, decode against different grammars and can fail
        independently, so one row holding both would have to average or discard
        something. `report.merge_passes` is where they become one answer again.
        """
        backend = FakeBackend()
        report = run(
            sample=make_sample(2),
            backends={"local_low": backend},
            connection=conn,
            session=seeded_session,
        )

        rows = store.iter_runs(conn)
        assert len(rows) == 4
        assert [row["pass_name"] for row in rows] == ["one", "two", "one", "two"]
        assert report.calls_made == 4
        assert report.outcomes[0].calls_by_pass == {"one": 2, "two": 2}
        assert report.outcomes[0].schema_valid == 4
        assert report.outcomes[0].schema_invalid == 0

    def test_the_warm_up_call_is_not_recorded(self, conn, seeded_session):
        """Otherwise model-load time becomes card #1's latency."""
        backend = FakeBackend()
        run(
            sample=make_sample(2),
            backends={"local_low": backend},
            connection=conn,
            session=seeded_session,
        )
        assert len(backend.calls) == 5  # warm-up + 2 cards x 2 passes
        assert len(store.iter_runs(conn)) == 4

    def test_row_carries_the_telemetry_the_store_requires(self, conn, seeded_session):
        run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(model="fake/model-14b")},
            connection=conn,
            session=seeded_session,
        )
        row = store.iter_runs(conn)[0]

        assert row["model_tier"] == "local_low"
        assert row["model_name"] == "fake/model-14b"
        assert row["bucket"] == "easy"
        assert row["card_name"] == "Card 0"
        assert row["input_tokens"] == 2_100
        assert row["output_tokens"] == 320
        assert row["latency_ms"] == 4_200
        # Local inference has no per-call billing; 0 is the honest value, and
        # latency is what separates the two local tiers.
        assert row["cost_usd"] == 0.0
        assert row["schema_valid"] == 1
        assert row["system_prompt"]
        assert "Name: Card 0" in row["user_prompt"]

    def test_prompts_are_the_production_prompts(self, conn, seeded_session):
        """A benchmark on a different prompt than production measures nothing.

        `plan_session` renders them rather than taking them, so this checks the
        default path -- the only one a real run uses. Both blocks are checked
        and each row gets its own: for three sessions the harness measured a
        single combined prompt production had stopped using, which is the
        failure this test is now shaped to catch
        (`docs/benchmarking-and-testing.md`).
        """
        from knowledge_pipeline.metadata_generator.prompt import (
            build_pass_one_system_prompt,
            build_pass_two_system_prompt,
        )

        plan = runner.plan_session(
            make_sample(1), buckets=("easy",), tiers=("local_low",), cards_per_bucket=1
        )
        run(
            plan=plan,
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        by_pass = {
            row["pass_name"]: row for row in store.iter_runs(conn)
        }
        assert by_pass["one"]["system_prompt"] == build_pass_one_system_prompt()
        assert by_pass["two"]["system_prompt"] == build_pass_two_system_prompt()
        assert by_pass["one"]["system_prompts_sha256"] == store.fingerprint_payload(
            {
                "one": build_pass_one_system_prompt(),
                "two": build_pass_two_system_prompt(),
            }
        )

    def test_the_rating_call_is_shown_the_ladders_for_its_own_tags(
        self, conn, seeded_session
    ):
        """Pass two's user message is the whole point of splitting the call.

        The ladders in it are selected from the roles and themes pass one just
        assigned, so this asserts the wiring between the two calls rather than
        the rendering, which `test_prompt_passes.py` covers.
        """
        backend = FakeBackend()
        run(
            sample=make_sample(1),
            backends={"local_low": backend},
            connection=conn,
            session=seeded_session,
        )

        rating = [row for row in store.iter_runs(conn) if row["pass_name"] == "two"][0]
        # VALID_PASS_ONE assigns Mana Rock / Artifacts, so those ladders -- and
        # not the other sixty-odd -- are what the rating call is calibrated on.
        assert "Mana Rock" in rating["user_prompt"]
        assert "power_rating=" in rating["user_prompt"]
        assert "Name: Card 0" in rating["user_prompt"]

    def test_invalid_response_is_recorded_not_discarded(self, conn, seeded_session):
        """The schema-validity rate can only count what gets written down."""
        backend = FakeBackend(
            attempts=[
                make_attempt(),  # warm-up
                make_attempt(
                    parsed=None,
                    raw='{"roles": ["Nonsense Role"]}',
                    error="does not match MetadataPassOne",
                ),
            ]
        )
        report = run(
            sample=make_sample(1),
            backends={"local_low": backend},
            connection=conn,
            session=seeded_session,
        )

        rows = store.iter_runs(conn)
        # One row, not two: a pass one that did not validate has no output for
        # the rating call to take as input, so there is no pass two to record.
        # The cell stays missing and the session is partial, which is honest --
        # a card with no power_rating cannot be compared on one.
        assert len(rows) == 1
        assert rows[0]["pass_name"] == "one"
        assert rows[0]["schema_valid"] == 0
        assert rows[0]["parsed_output"] is None  # the store's CHECK demands this
        assert rows[0]["raw_response"] == '{"roles": ["Nonsense Role"]}'
        assert report.outcomes[0].schema_invalid == 1
        assert report.outcomes[0].schema_valid == 0
        assert report.outcomes[0].pass_two_unreachable == 1
        assert report.status == "partial"

    def test_parsed_output_is_json_for_a_valid_response(self, conn, seeded_session):
        import json

        run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        by_pass = {row["pass_name"]: row for row in store.iter_runs(conn)}
        one = json.loads(by_pass["one"]["parsed_output"])
        two = json.loads(by_pass["two"]["parsed_output"])

        # Each row holds its own pass's fields and no more. A pass-one row
        # carrying a power_rating would mean the harness asked one call for the
        # whole blueprint again.
        assert one["roles"] == ["Mana Rock"]
        assert one["game_stage"] == {"early": 9.0, "mid": 6.0, "late": 3.0}
        assert "power_rating" not in one
        assert two["power_rating"] == 7.5
        assert "roles" not in two

    def test_the_session_bounds_the_cards_and_bounds_them_for_every_tier(
        self, conn, seeded_session
    ):
        """What replaced `--limit`, and the reason it had to.

        `--limit` capped each tier's *remaining* work, so two tiers run in
        separate sittings landed on different cards. A session's card set is
        decided once, and both tiers iterate that same list.
        """
        report = run(
            sample=make_sample(4),
            cards=2,
            tiers=("local_low", "local_high"),
            backends={
                "local_low": FakeBackend(model="fake/7b"),
                "local_high": FakeBackend(model="fake/14b"),
            },
            connection=conn,
            session=seeded_session,
        )

        rows = store.iter_runs(conn)
        assert len(rows) == 8  # 2 cards x 2 tiers x 2 passes
        low = {r["card_oracle_id"] for r in rows if r["model_tier"] == "local_low"}
        high = {r["card_oracle_id"] for r in rows if r["model_tier"] == "local_high"}
        assert low == high == {"oid-0", "oid-1"}
        assert report.status == "complete"

    def test_phase_timings_are_recorded_not_dropped(self, conn, seeded_session):
        """Without them `latency_ms` can never be re-judged: it is wall clock,
        and only the phase split distinguishes slow inference from a suspended
        host after the fact."""
        run(
            sample=make_sample(1),
            backends={
                "local_low": FakeBackend(
                    default=make_attempt(
                        load_ms=50, prompt_eval_ms=300, eval_ms=3_800
                    )
                )
            },
            connection=conn,
            session=seeded_session,
        )
        row = store.iter_runs(conn)[0]

        assert row["load_ms"] == 50
        assert row["prompt_eval_ms"] == 300
        assert row["eval_ms"] == 3_800


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------


class TestTiers:
    def test_tiers_run_in_order_and_each_is_unloaded(self, conn, seeded_session):
        """Sequential, with VRAM freed between: 7B and 14B don't share 12GB."""
        low, high = FakeBackend(model="fake/7b"), FakeBackend(model="fake/14b")
        report = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={"local_low": low, "local_high": high},
            connection=conn,
            session=seeded_session,
        )

        assert [o.tier for o in report.outcomes] == ["local_low", "local_high"]
        assert low.unloaded == 1
        assert high.unloaded == 1
        assert len(store.iter_runs(conn)) == 8  # 2 cards x 2 tiers x 2 passes

    def test_each_tier_gets_its_own_rows(self, conn, seeded_session):
        run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={
                "local_low": FakeBackend(model="fake/7b"),
                "local_high": FakeBackend(model="fake/14b"),
            },
            connection=conn,
            session=seeded_session,
        )
        assert len(store.iter_runs(conn, model_tier="local_low")) == 4
        assert len(store.iter_runs(conn, model_tier="local_high")) == 4
        # And each tier answered both passes for both cards, which is the
        # property `complete` is computed from.
        for tier in ("local_low", "local_high"):
            for pass_name in ("one", "two"):
                assert (
                    len(
                        store.iter_runs(
                            conn, model_tier=tier, pass_name=pass_name
                        )
                    )
                    == 2
                )

    def test_a_missing_backend_is_refused_before_any_call(self, conn, seeded_session):
        with pytest.raises(BenchmarkRunError, match="No backend supplied"):
            run(
                sample=make_sample(1),
                tiers=("local_low", "local_high"),
                backends={"local_low": FakeBackend()},
                connection=conn,
                session=seeded_session,
            )
        assert len(store.iter_runs(conn)) == 0

    def test_build_local_backends_refuses_the_frontier_tier(self):
        """Not wired. Say so rather than silently recording nothing."""
        with pytest.raises(BenchmarkRunError, match="not wired"):
            runner.build_local_backends(["frontier"])

    def test_build_local_backends_uses_the_pulled_ollama_tags(self):
        backends = runner.build_local_backends(["local_low", "local_high"])
        assert backends["local_low"].model == "qwen2.5:7b"
        assert (
            backends["local_high"].model
            == "hf.co/bartowski/Qwen2.5-14B-Instruct-GGUF:Q4_K_M"
        )
        assert backends["local_low"].num_ctx == runner.DEFAULT_NUM_CTX

    def test_model_overrides_are_honored(self):
        backends = runner.build_local_backends(
            ["local_low"], models={"local_low": "other:tag"}, num_ctx=9000
        )
        assert backends["local_low"].model == "other:tag"
        assert backends["local_low"].num_ctx == 9000


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_refuses_a_store_holding_demo_rows(self, conn, seeded_session):
        """`seed_demo` refuses the reverse mixture; this is the same corruption."""
        store.seed_demo(conn, cards=2)

        with pytest.raises(BenchmarkRunError, match="synthetic demo row"):
            run(
                sample=make_sample(1),
                backends={"local_low": FakeBackend()},
                connection=conn,
                session=seeded_session,
            )

    def test_demo_guard_fires_before_any_model_call(self, conn, seeded_session):
        store.seed_demo(conn, cards=2)
        backend = FakeBackend()

        with pytest.raises(BenchmarkRunError):
            run(
                sample=make_sample(1),
                backends={"local_low": backend},
                connection=conn,
                session=seeded_session,
            )
        assert backend.calls == []

    def test_a_clean_store_is_not_refused(self, conn, seeded_session):
        assert runner.demo_row_count(conn) == 0
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        assert report.calls_made == 2  # one card, both passes

    def test_a_full_context_window_aborts_the_whole_run(self, conn, seeded_session):
        """A prompt that fills its window has probably been truncated.

        The anchor ladders are the last section of the system block, so they
        are what got cut -- and every power_rating would come back uncalibrated
        with nothing in the output to show it.

        This raises rather than recording a per-tier abort, and the difference
        is deliberate: a tier failing mid-run is one tier's problem, but a
        truncated prompt means the *configuration* is wrong, so no row from any
        tier of this run would be trustworthy.
        """
        backend = FakeBackend(
            attempts=[
                make_attempt(),  # warm-up, not checked
                make_attempt(input_tokens=12_000),  # 12000/12288 = 0.976
            ]
        )
        with pytest.raises(BenchmarkRunError, match="truncated"):
            run(
                sample=make_sample(2),
                backends={"local_low": backend},
                connection=conn,
                session=seeded_session,
                num_ctx=12_288,
            )

        # The offending card is not recorded: its rating would be calibrated
        # against ladders the model never saw.
        assert len(store.iter_runs(conn)) == 0

    def test_a_truncated_run_still_frees_the_model(self, conn, seeded_session):
        """The abort path must not leave 9GB pinned in VRAM."""
        backend = FakeBackend(
            attempts=[make_attempt(), make_attempt(input_tokens=12_000)]
        )
        with pytest.raises(BenchmarkRunError):
            run(
                sample=make_sample(2),
                backends={"local_low": backend},
                connection=conn,
                session=seeded_session,
                num_ctx=12_288,
            )
        assert backend.unloaded == 1

    def test_a_comfortable_context_window_does_not_abort(self, conn, seeded_session):
        report = run(
            sample=make_sample(1),
            backends={
                "local_low": FakeBackend(
                    attempts=[
                        make_attempt(),
                        make_attempt(parsed=VALID_PASS_ONE, input_tokens=10_100),
                        make_attempt(parsed=VALID_PASS_TWO, input_tokens=1_400),
                    ]
                )
            },
            connection=conn,
            session=seeded_session,
            num_ctx=12_288,
        )
        assert report.outcomes[0].aborted_reason is None
        # The pass-two call's own count overwrites it, since the check runs
        # once per pass -- the window has to be proved for both.
        assert report.outcomes[0].first_call_input_tokens == 1_400

    def test_an_unmakeable_call_is_not_recorded_as_a_schema_failure(
        self, conn, seeded_session
    ):
        """A server outage must not land in the schema-validity rate."""
        backend = FakeBackend(
            attempts=[
                make_attempt(),  # warm-up succeeds
                MetadataGenerationError("connection refused"),
            ]
        )
        report = run(
            sample=make_sample(2),
            backends={"local_low": backend},
            connection=conn,
            session=seeded_session,
        )

        outcome = report.outcomes[0]
        assert outcome.failed == 1
        assert outcome.schema_invalid == 0
        assert outcome.aborted_reason is not None
        assert len(store.iter_runs(conn)) == 0

    def test_a_failed_tier_stops_later_tiers(self, conn, seeded_session):
        """A tier that couldn't finish isn't comparable against the next one."""
        low = FakeBackend(
            model="fake/7b",
            attempts=[make_attempt(), MetadataGenerationError("server died")],
        )
        high = FakeBackend(model="fake/14b")

        report = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={"local_low": low, "local_high": high},
            connection=conn,
            session=seeded_session,
        )

        assert [o.tier for o in report.outcomes] == ["local_low"]
        assert high.calls == []
        # Freed anyway, so the next attempt has the VRAM.
        assert low.unloaded == 1

    def test_cpu_speed_decoding_is_flagged(self, conn, seeded_session):
        """Genuine CPU inference: ~3 tok/s *decoding*, not merely slow overall."""
        slow = make_attempt(output_tokens=212, eval_ms=70_000)  # 3.0 tok/s decode
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(attempts=[slow, make_attempt()])},
            connection=conn,
            session=seeded_session,
        )

        outcome = report.outcomes[0]
        assert outcome.vram_warning is not None
        assert "tokens/second" in outcome.vram_warning
        assert outcome.tokens_per_second == pytest.approx(3.0, abs=0.2)
        # A warning, never a refusal: the answers are still correct, and a
        # memory-constrained tier reported as such is a finding worth having.
        assert report.calls_made == 2

    def test_gpu_speed_decoding_is_not_flagged(self, conn, seeded_session):
        """The real measured 7B figure: 216 tokens in 3.66s of decode."""
        fast = make_attempt(output_tokens=216, eval_ms=3_660)  # 59 tok/s
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(attempts=[fast, make_attempt()])},
            connection=conn,
            session=seeded_session,
        )
        assert report.outcomes[0].vram_warning is None
        assert report.outcomes[0].tokens_per_second == pytest.approx(59.0, abs=0.5)

    def test_a_slow_cold_call_is_not_mistaken_for_cpu_inference(
        self, conn, seeded_session
    ):
        """The regression this guard was rewritten for.

        These are the real measured 14B numbers on a *cold* call: 9.3s of model
        load, 35.2s prefilling a 10,245-token prompt, and 209 tokens generated
        in 7.0s. Total 51.5s. Dividing output by total time gives 4.1 "tok/s"
        and the earlier version of this check concluded the model was running
        on the CPU -- while the server log showed all 49 layers on the GPU and a
        true decode rate of 30 tok/s, about half the 7B's as doubling the
        parameters predicts. A big prompt amortized over a small answer is not
        evidence of anything about residency.
        """
        cold = make_attempt(
            input_tokens=10_245,
            output_tokens=209,
            latency_ms=51_520,
            prompt_eval_ms=35_200,
            eval_ms=6_970,
        )
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(attempts=[cold, make_attempt()])},
            connection=conn,
            session=seeded_session,
        )

        outcome = report.outcomes[0]
        assert outcome.tokens_per_second == pytest.approx(30.0, abs=0.5)
        assert outcome.prefill_tokens_per_second == pytest.approx(291, abs=5)
        assert outcome.vram_warning is None, (
            "a cold call with a 10k prompt must not be read as CPU inference"
        )
        # The blended rate that caused the wrong conclusion, for contrast.
        assert cold.output_tokens / (cold.latency_ms / 1000) < 5

    def test_throughput_is_measured_on_the_warm_up_not_a_scored_card(
        self, conn, seeded_session
    ):
        """The warm-up is already being paid for; reuse it rather than add a call."""
        slow_warmup = make_attempt(output_tokens=100, eval_ms=50_000)
        fast_card = make_attempt(output_tokens=200, eval_ms=2_000)
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(attempts=[slow_warmup, fast_card])},
            connection=conn,
            session=seeded_session,
        )
        # 2.0 tok/s from the warm-up, not 100 tok/s from the recorded card.
        assert report.outcomes[0].tokens_per_second == pytest.approx(2.0, abs=0.1)

    def test_a_backend_reporting_no_phase_split_is_not_flagged(
        self, conn, seeded_session
    ):
        """Absence of data is not evidence of a problem."""
        blind = make_attempt(prompt_eval_ms=0, eval_ms=0)
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend(attempts=[blind, make_attempt()])},
            connection=conn,
            session=seeded_session,
        )
        assert report.outcomes[0].tokens_per_second is None
        assert report.outcomes[0].vram_warning is None

    def test_a_card_missing_from_the_corpus_is_refused(self, conn, seeded_session):
        sample = CardSample(
            cards=[SampleCard(oracle_id="ghost", name="Ghost", bucket="easy")]
        )
        with pytest.raises(BenchmarkRunError, match="not in the corpus"):
            run(
                sample=sample,
                backends={"local_low": FakeBackend()},
                connection=conn,
                session=seeded_session,
            )


# ---------------------------------------------------------------------------
# resuming
# ---------------------------------------------------------------------------


class TestResume:
    """Resume is per *session* now, and that is the whole point.

    It used to skip any (card, tier) pair recorded anywhere in the store, which
    meant a card already run under some other sitting was quietly dropped from
    this one. The two tiers then covered different card sets and nothing
    noticed. Scoping the skip to the session makes that unreachable.
    """

    def test_a_fresh_run_is_a_new_session_and_re_runs_the_cards(
        self, conn, seeded_session
    ):
        sample = make_sample(2)
        run(
            sample=sample,
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        report = run(
            sample=sample,
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )

        assert store.session_count(conn) == 2
        assert report.calls_made == 4  # 2 cards x 2 passes
        assert len(store.iter_runs(conn)) == 8
        assert len(store.iter_runs(conn, session_id=report.session_id)) == 4

    def test_resuming_a_session_finishes_only_its_missing_cells(
        self, conn, seeded_session
    ):
        """A tier that died halfway leaves holes; resume fills exactly those."""
        low = FakeBackend(
            model="fake/7b",
            attempts=[
                make_attempt(),  # warm-up
                make_attempt(parsed=VALID_PASS_ONE),  # card 0, pass one
                make_attempt(parsed=VALID_PASS_TWO),  # card 0, pass two
                MetadataGenerationError("died"),  # card 1, pass one
            ],
        )
        first = run(
            sample=make_sample(3),
            tiers=("local_low",),
            backends={"local_low": low},
            connection=conn,
            session=seeded_session,
        )
        assert first.status == "aborted"
        assert len(store.iter_runs(conn)) == 2  # one finished card, both passes

        resumed = run(
            sample=make_sample(3),
            tiers=("local_low",),
            resume_session_id=first.session_id,
            backends={"local_low": FakeBackend(model="fake/7b")},
            connection=conn,
            session=seeded_session,
        )

        assert resumed.session_id == first.session_id
        assert resumed.calls_made == 4  # the two unfinished cards, both passes
        assert resumed.status == "complete"
        assert store.session_count(conn) == 1
        assert len(store.iter_runs(conn, session_id=first.session_id)) == 6

    def test_resume_is_per_tier_within_the_session(self, conn, seeded_session):
        """The same card still needs running on a tier it has no row for."""
        first = run(
            sample=make_sample(2),
            tiers=("local_low",),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )

        resumed = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            resume_session_id=first.session_id,
            backends={
                "local_low": FakeBackend(),
                "local_high": FakeBackend(model="fake/14b"),
            },
            connection=conn,
            session=seeded_session,
        )

        assert resumed.calls_made == 4  # local_high only: 2 cards x 2 passes
        assert len(store.iter_runs(conn, model_tier="local_high")) == 4
        assert len(store.iter_runs(conn, model_tier="local_low")) == 4

    def test_a_card_already_run_in_another_session_is_still_run_here(
        self, conn, seeded_session
    ):
        """The drift that produced an uncomparable store, pinned shut."""
        sample = make_sample(2)
        run(
            sample=sample,
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )

        second = run(
            sample=sample,
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )

        cards = {
            row["card_oracle_id"]
            for row in store.iter_runs(conn, session_id=second.session_id)
        }
        assert cards == {"oid-0", "oid-1"}

    def test_existing_cells_reads_only_the_key_columns(self, conn, seeded_session):
        report = run(
            sample=make_sample(2),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        cells = runner.existing_cells(conn, report.session_id)
        # (card, tier, pass): the pass is in the key because a card whose
        # rating call was interrupted still has work left, and a two-column key
        # would report it finished.
        assert cells == {
            ("oid-0", "local_low", "one"),
            ("oid-0", "local_low", "two"),
            ("oid-1", "local_low", "one"),
            ("oid-1", "local_low", "two"),
        }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


class TestRunCliValidation:
    """What `run` refuses outright vs. what it only warns about.

    The split matters: refusing an undersized sample would make a trimmed one
    unrunnable, which is a reasonable thing to want when smoke-testing a prompt
    change. A duplicate oracle_id is a different kind of problem -- it corrupts
    the rows themselves -- so that still refuses.
    """

    @staticmethod
    def _write(tmp_path, sample: CardSample):
        from knowledge_pipeline.benchmark import sample as sample_mod

        path = tmp_path / "sample.json"
        sample_mod.write_sample(sample, path)
        return path

    def _invoke(self, monkeypatch, tmp_path, sample: CardSample, *, extra=()):
        """Run the CLI with the model layer stubbed out, capturing the call."""
        from knowledge_pipeline.benchmark import __main__ as cli
        from knowledge_pipeline.benchmark import runner as runner_mod

        called: dict = {}

        def fake_build(tiers, **kwargs):
            return {tier: FakeBackend() for tier in tiers}

        def fake_run(**kwargs):
            called.update(kwargs)
            return runner_mod.RunnerReport(
                db_path=tmp_path / "benchmark.db",
                sample_size=len(kwargs["plan"].cards),
                sample_reviewed=False,
            )

        monkeypatch.setattr(runner_mod, "build_local_backends", fake_build)
        monkeypatch.setattr(runner_mod, "run_benchmark", fake_run)

        path = self._write(tmp_path, sample)
        code = cli.main(
            [
                "--db",
                str(tmp_path / "benchmark.db"),
                "run",
                str(path),
                "--tier",
                "local_low",
                "--bucket",
                "easy",
                "--cards",
                "1",
                *extra,
            ]
        )
        return code, called

    def test_an_undersized_sample_runs(self, monkeypatch, tmp_path):
        code, called = self._invoke(monkeypatch, tmp_path, make_sample(2))
        assert code == 0
        assert len(called["plan"].cards) == 1  # --cards 1, drawn from the two

    def test_run_refuses_without_a_bucket(self, monkeypatch, tmp_path, capsys):
        """The flag that replaced the one which silently measured only easy."""
        from knowledge_pipeline.benchmark import __main__ as cli

        path = self._write(tmp_path, make_sample(2))
        code = cli.main(
            ["--db", str(tmp_path / "benchmark.db"), "run", str(path)]
        )

        assert code == 2
        assert "--bucket" in capsys.readouterr().err

    def test_resume_refuses_alongside_a_fresh_draw(self, monkeypatch, tmp_path, capsys):
        """--bucket/--cards with --resume would describe a different
        comparison than the session being finished."""
        code, called = self._invoke(
            monkeypatch, tmp_path, make_sample(2), extra=("--resume", "S-001")
        )

        assert code == 2
        assert called == {}
        assert "--resume" in capsys.readouterr().err

    def test_a_duplicate_oracle_id_is_refused(self, monkeypatch, tmp_path):
        sample = make_sample(2)
        sample.cards.append(sample.cards[0])
        code, called = self._invoke(monkeypatch, tmp_path, sample)
        assert code == 2
        assert called == {}  # never reached the runner

    def test_an_anchor_card_is_refused(self, monkeypatch, tmp_path):
        from knowledge_pipeline.benchmark import sample as sample_mod

        anchor_name = sorted(sample_mod.anchor_card_names())[0]
        sample = make_sample(1)
        sample.cards.append(
            SampleCard(oracle_id="anchor-oid", name=anchor_name, bucket="easy")
        )
        code, called = self._invoke(monkeypatch, tmp_path, sample)
        assert code == 2
        assert called == {}


class TestDefaultSamplePath:
    def test_cli_and_sample_module_agree_on_the_default_path(self):
        """`__main__.py` duplicates this constant rather than importing it,
        so `init`/`review` don't pay for sqlalchemy. Nothing enforces the two
        stay equal except this test.
        """
        from knowledge_pipeline.benchmark import __main__ as cli
        from knowledge_pipeline.benchmark import sample as sample_mod

        assert cli.DEFAULT_SAMPLE_PATH == sample_mod.DEFAULT_SAMPLE_PATH


class TestRunnerReport:
    def test_summary_flags_unreviewed_buckets(self, conn, seeded_session):
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        assert "unreviewed" in report.summary()

    def test_summary_flags_schema_validity_as_enforced(self, conn, seeded_session):
        """Constrained decoding makes the rate ~100% by construction."""
        report = run(
            sample=make_sample(1),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        assert "ENFORCED" in report.summary()

    def test_summary_reports_an_abort(self, conn, seeded_session):
        report = run(
            sample=make_sample(2),
            backends={
                "local_low": FakeBackend(
                    attempts=[make_attempt(), MetadataGenerationError("boom")]
                )
            },
            connection=conn,
            session=seeded_session,
        )
        assert "ABORTED" in report.summary()

    def test_mean_latency(self, conn, seeded_session):
        report = run(
            sample=make_sample(1),
            backends={
                "local_low": FakeBackend(
                    attempts=[
                        make_attempt(),  # warm-up, never recorded
                        make_attempt(parsed=VALID_PASS_ONE, latency_ms=2_000),
                        make_attempt(parsed=VALID_PASS_TWO, latency_ms=4_000),
                    ]
                )
            },
            connection=conn,
            session=seeded_session,
        )
        outcome = report.outcomes[0]

        # The flat mean is over calls, both passes together -- a card's cost.
        assert outcome.mean_latency_ms == 3_000
        # And each pass keeps its own, because the two do different work and
        # one number over both describes neither.
        assert outcome.mean_latency_ms_for("one") == 2_000
        assert outcome.mean_latency_ms_for("two") == 4_000


# --------------------------------------------------------------------------
# Timing guard
#
# `latency_ms` is Ollama's `total_duration` -- wall clock. A host suspension
# mid-call is billed to inference: one real run recorded 220 tokens in
# 49,866,300 ms, which left that tier's mean at 3,365,675 ms and its p95 at
# 15,004,868 ms while the median stayed plausible. These tests pin both the
# detection and the one-sidedness of it.
# --------------------------------------------------------------------------


class TestTimingAnomaly:
    """`runner.timing_anomaly` in isolation, before the loop that uses it."""

    def test_a_healthy_warm_call_is_not_an_anomaly(self):
        assert runner.timing_anomaly(make_attempt()) is None

    def test_a_cold_call_with_a_real_model_load_is_not_an_anomaly(self):
        """9.3s of model load is legitimate, and `load_ms` is what says so.

        Without the phase being reported, this call's load would read as
        unexplained wall time and a sound measurement would be thrown away.
        """
        cold = make_attempt(
            input_tokens=10_245,
            output_tokens=209,
            latency_ms=51_520,
            load_ms=9_300,
            prompt_eval_ms=35_200,
            eval_ms=6_970,
        )

        assert runner.timing_anomaly(cold) is None

    def test_the_observed_suspension_is_caught(self):
        """The exact call that motivated the guard, from benchmark.db."""
        suspended = make_attempt(
            input_tokens=10_235,
            output_tokens=220,
            latency_ms=49_866_300,
            prompt_eval_ms=900,
            eval_ms=49_865_371,
        )

        reason = runner.timing_anomaly(suspended)
        assert reason is not None
        assert "0.0044 tok/s" in reason
        assert "nothing was computing" in reason

    def test_a_stall_outside_generation_is_caught_by_unaccounted_time(self):
        """Every phase timer healthy, only the total wrong.

        The decode-rate check cannot see this one: 200 tokens in 4s is a
        perfectly good rate. Only the total gives it away.
        """
        stalled = make_attempt(
            output_tokens=200,
            latency_ms=3_600_000,
            load_ms=0,
            prompt_eval_ms=300,
            eval_ms=4_000,
        )

        assert stalled.generation_tokens_per_second == pytest.approx(50.0)
        reason = runner.timing_anomaly(stalled)
        assert reason is not None
        assert "belongs to no phase" in reason

    def test_slow_but_possible_inference_is_kept(self):
        """CPU inference is a finding to report, not a row to delete.

        2 tok/s is what a 14B on a CPU actually does. The residency guard
        warns about it; this guard must not discard it, or the evidence for
        that warning would be gone.
        """
        cpu_bound = make_attempt(
            output_tokens=200, latency_ms=100_400, prompt_eval_ms=300, eval_ms=100_000
        )

        assert cpu_bound.generation_tokens_per_second == pytest.approx(2.0)
        assert runner.timing_anomaly(cpu_bound) is None

    def test_an_impossibly_fast_call_is_never_an_anomaly(self):
        """Prefix caching is supposed to make calls fast.

        The guard is one-sided on purpose: rejecting a suspiciously quick call
        would reject the good case this benchmark depends on.
        """
        cached = make_attempt(latency_ms=400, prompt_eval_ms=1, eval_ms=380)

        assert runner.timing_anomaly(cached) is None

    def test_a_backend_reporting_no_phase_split_is_never_discarded(self):
        """`AnthropicMetadataBackend` leaves the phases at 0.

        With nothing to check against, the guard must abstain rather than read
        a missing split as a stall.
        """
        no_split = make_attempt(latency_ms=2_000, prompt_eval_ms=0, eval_ms=0)

        assert no_split.unaccounted_ms is None
        assert runner.timing_anomaly(no_split) is None


def run_three_cards(conn, session, backend, *, resume_session_id=None):
    """Three cards on one tier -- enough for a discard to sit between keeps."""
    return run(
        sample=make_sample(3),
        backends={"local_low": backend},
        connection=conn,
        session=session,
        **({"resume_session_id": resume_session_id} if resume_session_id else {}),
    )


class TestTimingGuardInTheLoop:
    def test_a_suspended_call_is_not_recorded(self, conn, seeded_session):
        suspended = make_attempt(
            output_tokens=220, latency_ms=49_866_300, prompt_eval_ms=900,
            eval_ms=49_865_371,
        )
        backend = FakeBackend(attempts=[make_attempt(), suspended])

        report = run_three_cards(conn, seeded_session, backend)
        outcome = report.outcomes[0]

        # The suspended call was card 0's pass one, so that card contributes no
        # rows at all -- there is nothing to rate. The other two cards run
        # normally, two rows each.
        assert outcome.discarded_timing == 1
        assert outcome.calls_made == 4
        assert store.run_count(conn) == 4
        # The poisoned latency never reached an aggregate.
        assert outcome.latency_ms_total < 100_000

    def test_a_discarded_card_is_retried_when_the_session_resumes(
        self, conn, seeded_session
    ):
        """Discard and retry are the same mechanism: resume skips only rows.

        This is why the guard can drop a sound output without losing it -- and
        why the session it belonged to comes back `partial` rather than
        `complete` in the meantime.
        """
        suspended = make_attempt(
            output_tokens=220, latency_ms=49_866_300, prompt_eval_ms=900,
            eval_ms=49_865_371,
        )
        first = FakeBackend(attempts=[make_attempt(), suspended])
        opened = run_three_cards(conn, seeded_session, first)
        assert store.run_count(conn) == 4  # two whole cards; the discarded one none
        assert opened.status == "partial"
        assert opened.missing_cells == 2  # both of that card's cells

        report = run_three_cards(
            conn, seeded_session, FakeBackend(), resume_session_id=opened.session_id
        )
        outcome = report.outcomes[0]

        assert outcome.skipped_existing == 2
        assert outcome.calls_made == 2  # the discarded card, both passes
        assert store.run_count(conn) == 6
        assert report.status == "complete"

    def test_the_summary_names_the_discard_and_tells_you_to_re_run(
        self, conn, seeded_session
    ):
        suspended = make_attempt(
            output_tokens=220, latency_ms=49_866_300, prompt_eval_ms=900,
            eval_ms=49_865_371,
        )
        # attempts[0] is eaten by the unrecorded warm-up call, so the
        # suspension has to sit behind it to land on a real card.
        backend = FakeBackend(
            attempts=[make_attempt(), suspended, make_attempt(), make_attempt()]
        )

        summary = run_three_cards(conn, seeded_session, backend).summary()

        assert "discarded=1" in summary
        assert "--resume" in summary

    def test_a_clean_run_says_nothing_about_discards(self, conn, seeded_session):
        summary = run_three_cards(conn, seeded_session, FakeBackend()).summary()

        assert "discarded" not in summary

    def test_a_stalled_warm_up_does_not_become_a_cpu_accusation(
        self, conn, seeded_session
    ):
        """The failure mode this guard exists to prevent, one layer up.

        A suspension during the warm-up collapses the decode rate to ~0, and
        the residency check would read that as "not running on the GPU" --
        the same wrong conclusion that motivated splitting the phases in the
        first place, reached by a different route.
        """
        suspended = make_attempt(
            output_tokens=220, latency_ms=49_866_300, prompt_eval_ms=900,
            eval_ms=49_865_371,
        )
        backend = FakeBackend(attempts=[suspended])

        outcome = run_three_cards(conn, seeded_session, backend).outcomes[0]

        assert outcome.vram_warning is None
        assert outcome.tokens_per_second is None
        # The three real cards still ran and were recorded, both passes each.
        assert outcome.calls_made == 6
        assert store.run_count(conn) == 6


# ---------------------------------------------------------------------------
# planning: the draw, and the refusals that protect it
#
# `plan_session` is where a session's card set is decided, and the two defects
# that made the old store uncomparable both lived at this step: no bucket was
# ever chosen, and the cards were taken off the front of the sample file.
# ---------------------------------------------------------------------------


def big_sample(per_bucket: int = 8) -> CardSample:
    """A sample with all three buckets populated and strata to spread over."""
    cards = []
    colors = ("W", "U", "B", "R", "G")
    types = ("Creature", "Instant", "Land")
    index = 0
    for bucket in ("easy", "medium", "hard"):
        for position in range(per_bucket):
            cards.append(
                SampleCard(
                    oracle_id=f"oid-{bucket}-{position}",
                    name=f"{bucket.title()} Card {position}",
                    bucket=bucket,
                    signals={
                        "color_bucket": colors[index % len(colors)],
                        "mana_value_band": str(index % 4),
                        "base_type": types[index % len(types)],
                    },
                )
            )
            index += 1
    return CardSample(
        cards=cards, reviewed=False, generated_at="2026-10-12T00:00:00Z", seed=1
    )


class TestPlanning:
    def test_a_plan_needs_a_bucket(self):
        with pytest.raises(BenchmarkRunError, match="at least one difficulty bucket"):
            runner.plan_session(big_sample(), buckets=(), tiers=("local_low",))

    def test_an_unknown_bucket_is_refused(self):
        with pytest.raises(BenchmarkRunError, match="Unknown bucket"):
            runner.plan_session(
                big_sample(), buckets=("trivial",), tiers=("local_low",)
            )

    def test_a_plan_draws_only_from_the_named_bucket(self):
        plan = runner.plan_session(
            big_sample(),
            buckets=("hard",),
            tiers=("local_low",),
            cards_per_bucket=4,
        )

        assert len(plan.cards) == 4
        assert {card.bucket for card in plan.cards} == {"hard"}

    def test_repeated_buckets_each_contribute_their_own_cards(self):
        plan = runner.plan_session(
            big_sample(),
            buckets=("easy", "hard"),
            tiers=("local_low",),
            cards_per_bucket=3,
        )

        assert len(plan.cards) == 6
        assert sum(1 for c in plan.cards if c.bucket == "easy") == 3
        assert sum(1 for c in plan.cards if c.bucket == "hard") == 3

    def test_the_draw_is_not_the_front_of_the_file(self):
        """The specific failure `--limit` had: `head -N` of an easy-first file.

        A stratified draw also has to actually vary with its seed, or every
        session would compare the same cards and a lucky set would look like a
        conclusion.
        """
        sample = big_sample()
        first = runner.plan_session(
            sample, buckets=("hard",), tiers=("local_low",), cards_per_bucket=4
        )
        second = runner.plan_session(
            sample,
            buckets=("hard",),
            tiers=("local_low",),
            cards_per_bucket=4,
            draw_seed=99,
        )

        head = [card.oracle_id for card in sample.by_bucket("hard")[:4]]
        assert [c.oracle_id for c in first.cards] != head
        assert {c.oracle_id for c in first.cards} != {
            c.oracle_id for c in second.cards
        }

    def test_the_draw_is_reproducible_from_its_seed(self):
        sample = big_sample()
        kwargs = dict(buckets=("medium",), tiers=("local_low",), cards_per_bucket=5)
        again = runner.plan_session(sample, draw_seed=4242, **kwargs)
        once = runner.plan_session(sample, draw_seed=4242, **kwargs)

        assert [c.oracle_id for c in once.cards] == [
            c.oracle_id for c in again.cards
        ]

    def test_asking_for_more_cards_than_the_bucket_holds_is_refused(self):
        """Silently running fewer would make two sessions look comparable."""
        with pytest.raises(BenchmarkRunError, match="holds only"):
            runner.plan_session(
                big_sample(per_bucket=3),
                buckets=("easy",),
                tiers=("local_low",),
                cards_per_bucket=10,
            )

    def test_a_plan_records_the_decoding_knobs_it_will_run_under(self):
        plan = runner.plan_session(
            big_sample(),
            buckets=("easy",),
            tiers=("local_low",),
            cards_per_bucket=2,
            num_ctx=9_000,
            temperature=0.2,
            model_seed=5,
        )

        assert (plan.num_ctx, plan.temperature, plan.model_seed) == (9_000, 0.2, 5)


class TestSessionRecording:
    def test_the_session_row_is_written_before_the_first_call(
        self, conn, seeded_session
    ):
        """The card set is the session's one irreplaceable decision."""
        seen: list[int] = []

        class WatchingBackend(FakeBackend):
            def generate_observed(self, *, system, prompt, output_model=None):
                seen.append(store.session_count(conn))
                return super().generate_observed(
                    system=system, prompt=prompt, output_model=output_model
                )

        run(
            sample=make_sample(2),
            backends={"local_low": WatchingBackend()},
            connection=conn,
            session=seeded_session,
        )

        assert seen and seen[0] == 1  # already there for the warm-up call

    def test_the_session_records_what_makes_it_comparable(self, conn, seeded_session):
        report = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={
                "local_low": FakeBackend(model="fake/7b"),
                "local_high": FakeBackend(model="fake/14b"),
            },
            connection=conn,
            session=seeded_session,
        )
        row = store.get_session(conn, report.session_id)

        assert store.session_card_ids(row) == ("oid-0", "oid-1")
        assert store.session_tiers(row) == ("local_low", "local_high")
        # Both halves of the comparability key: the prompts *and* the output
        # schemas, since under constrained decoding the schema is the grammar.
        assert row["system_prompts_sha256"] == store.fingerprint_payload(
            dict(TEST_SYSTEM_PROMPTS)
        )
        assert row["output_schemas_sha256"] == store.fingerprint_payload(
            dict(TEST_SCHEMAS)
        )
        assert row["num_ctx"] == 12_288
        assert row["status"] == "complete"

    def test_a_complete_grid_closes_the_session_complete(self, conn, seeded_session):
        report = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={
                "local_low": FakeBackend(),
                "local_high": FakeBackend(model="fake/14b"),
            },
            connection=conn,
            session=seeded_session,
        )

        assert report.status == "complete"
        assert report.missing_cells == 0
        assert store.get_session(conn, report.session_id)["status"] == "complete"

    def test_a_tier_that_never_ran_leaves_the_session_partial(
        self, conn, seeded_session
    ):
        """One tier missing means the tiers answered different card sets, which
        is the one thing a win rate must never be computed over."""
        low = FakeBackend(
            model="fake/7b",
            attempts=[make_attempt(), MetadataGenerationError("server died")],
        )
        report = run(
            sample=make_sample(2),
            tiers=("local_low", "local_high"),
            backends={"local_low": low, "local_high": FakeBackend(model="fake/14b")},
            connection=conn,
            session=seeded_session,
        )

        assert report.status == "aborted"
        # 2 cards x 2 tiers x 2 passes = 8 cells, and the server died on the
        # very first card's first pass, so none of them landed.
        assert report.missing_cells == 8
        assert "PARTIAL" in report.summary() or "cell(s)" in report.summary()

    def test_resuming_refuses_a_changed_system_prompt(self, conn, seeded_session):
        """Finishing it would compare answers to two different questions: the
        taxonomy, rubric and anchor ladders all live in that block."""
        first = run(
            sample=make_sample(2),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        row = store.get_session(conn, first.session_id)

        with pytest.raises(BenchmarkRunError, match="system prompts have changed"):
            runner.plan_from_session(
                make_sample(2),
                row,
                system_prompts={"one": "A DIFFERENT PROMPT", "two": "x"},
            )

    def test_resuming_refuses_a_sample_missing_the_sessions_cards(
        self, conn, seeded_session
    ):
        first = run(
            sample=make_sample(2),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        row = store.get_session(conn, first.session_id)

        with pytest.raises(BenchmarkRunError, match="no longer holds"):
            runner.plan_from_session(
                make_sample(1),
                row,
                system_prompts=dict(TEST_SYSTEM_PROMPTS),
                schemas=dict(TEST_SCHEMAS),
            )

    def test_plan_from_session_rebuilds_the_same_card_set(self, conn, seeded_session):
        first = run(
            sample=make_sample(3),
            backends={"local_low": FakeBackend()},
            connection=conn,
            session=seeded_session,
        )
        row = store.get_session(conn, first.session_id)

        plan = runner.plan_from_session(
            make_sample(3),
            row,
            system_prompts=dict(TEST_SYSTEM_PROMPTS),
            schemas=dict(TEST_SCHEMAS),
        )

        assert plan.resume_session_id == first.session_id
        assert plan.card_ids == store.session_card_ids(row)
        assert plan.tiers == ("local_low",)


class TestConstantsStayInSync:
    def test_cli_and_runner_agree_on_the_session_defaults(self):
        """`__main__` duplicates them so building the parser stays import-light.
        Nothing enforces the two stay equal except this test."""
        from knowledge_pipeline.benchmark import __main__ as cli

        assert cli.DEFAULT_CARDS_PER_BUCKET == runner.DEFAULT_CARDS_PER_BUCKET
        assert cli.DEFAULT_DRAW_SEED == runner.DEFAULT_DRAW_SEED

    def test_the_report_duplicates_the_no_winner_vocabulary_correctly(self):
        """`report.py` imports no store, so it restates these two values."""
        from knowledge_pipeline.benchmark import report as report_mod

        assert report_mod.VERDICT_NO_WINNER == store.NO_WINNER
        assert report_mod.SYNTHETIC_MODEL_PREFIX == store.DEMO_MODEL_PREFIX
