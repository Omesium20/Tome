"""Runs one paired session: the same cards, through every tier, in turn.

This is the benchmark itself — the piece `docs/benchmarking-and-testing.md`
specified and `store.py` was built to receive. It draws a card set from a
sample (`sample.py`), renders the same prompts `metadata_generator` would
(`prompt.build_pass_one_system_prompt` / `build_pass_two_system_prompt` /
`render_card_facts` / `render_rating_request`, unchanged, because a benchmark
that measures a different prompt than production uses measures nothing), calls
one tier at a time, and writes one `benchmark_runs` row per (card, tier, pass)
under one `benchmark_sessions` row.

**Two calls per card, because production makes two.** Pass one answers what the
card is with no ladders in the prompt; pass two rates it against only the
ladders pass one's tags selected, and `derive_themes` is merged in between
exactly as `pipeline.generate_metadata` does it. This module made a single
combined call for its first three sessions, and the cost of that was not
theoretical: the one-pass prompt still carries all 66 ladders and none of
`game_stage`'s turn ranges or reference profiles, so those sessions reproduced
the flat `power_rating` band the split exists to remove and scored `game_stage`
against a definition the model was never given (`docs/lessons-learned.md`).

**A session is the unit, and that is the whole design.** The only question
this benchmark answers is a comparison, so the card set is drawn *once*,
written to the session before the first call, and iterated identically by
every tier. The previous version of this module ran each tier over "the sample,
minus whatever that tier already had, capped at `--limit`", which sounds
equivalent and is not: across three sittings it put the 7B on 15 cards and the
14B on 30, all of them easy, and produced a store from which no comparison
could be computed. `docs/lessons-learned.md` records it. The flag that did it
is gone, and the structure that allowed it — a run log with no concept of a
run — is gone with it.

**Local tiers only, for now.** `local_low` and `local_high` run through
`OllamaMetadataBackend`. `frontier` is deliberately not wired: it would need
`AnthropicMetadataBackend` to grow `generate_observed`, and until the hand-
labeled ground truth exists there is nothing to compare its answers against
that would justify billed calls. Nothing here assumes two tiers, so frontier
becomes a third column rather than a rewrite.

Five properties this module exists to protect, each of which was a plausible
way to produce a confidently wrong benchmark:

**Every tier sees the same cards.** Resume is scoped to the session, so
continuing an interrupted run cannot drift onto a different card set, and the
session's status says `partial` rather than `complete` if any cell of the grid
is missing.

**Tiers run one after another, never together.** Either model alone fits a 12GB
card with room to spare (the 14B takes ~10.3GB including a 12k KV cache); both
at once do not. Since local tiers have no per-call cost, `latency_ms` is the
*only* number separating them, so a model forced partly onto the CPU would not
merely slow the benchmark down — it would change what the benchmark measures.
Each tier is explicitly unloaded before the next one starts.

**The context window is checked twice.** Once before the run (does the rendered
prompt fit `num_ctx`?) and once after the first real call (did the model
actually evaluate as many tokens as we sent?). A truncated prompt loses the
anchor ladders and produces uncalibrated ratings with nothing in the output to
show for it; see `ollama_backend.py`.

**Synthetic and measured rows never mix.** `store.seed_demo` refuses to add
demo rows to a store holding real ones. This refuses the reverse, which is the
same corruption from the other side.

**A stopped run is a resumable run.** `record_run` commits per row, so
interrupting is safe, and `--resume SESSION` finishes the cells the session is
still missing. A long tier is hours, and a harness that has to restart from
zero is a harness that gets run with a smaller sample instead.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from database.knowledge.models import Card
from database.knowledge.session import new_session

from ..metadata_generator.model_backend import (
    GenerationAttempt,
    MetadataGenerationError,
    ObservableMetadataBackend,
)
from ..metadata_generator.ollama_backend import (
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    OllamaMetadataBackend,
    estimate_prompt_tokens,
)
from ..derived_tags import derive_themes
from ..metadata_generator.prompt import (
    build_pass_one_system_prompt,
    build_pass_two_system_prompt,
    render_card_facts,
    render_rating_request,
)
from ..metadata_generator.schema import (
    MAX_TAGS_PER_CARD,
    GameStageProfile,
    MetadataPassOne,
    MetadataPassTwo,
    Role,
    Theme,
)
from . import store
from .sample import CardSample, SampleCard, cards_for_sample, draw_session_cards
from .store import (
    PASSES,
    BenchmarkRun,
    BenchmarkSession,
    ModelTier,
    PassName,
    SessionStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"

# The Ollama tags actually pulled. `model_name` in the store records whichever
# of these was really called, because that column is meant to answer "what
# produced this row", not "what did the design document intend" -- note the two
# tags come from different registries, which is why they look nothing alike.
#
# These are the **general** instruction-tuned Qwen2.5 models, not the
# `qwen2.5-coder` variants the first session (S-001) ran. The coder line is
# Qwen2.5 continued-pretrained on ~5.5T tokens of code, and that capacity is
# bought *from* general and world knowledge -- which is the whole task here.
# S-001's review turned up the symptom directly: `Echo Storm` was themed
# `Storm`, a mechanic it does not have, because the word is in its name, and
# neither tier knew why `Elvish Reclaimer` or `Fist of Suns` see play. Both
# replacements hold the old pair's parameter count (7.6B / 14.8B), quantization
# (Q4_K_M) and trained context (32,768) so the swap is the only variable, and
# both share the Qwen2 tokenizer, so `ollama_backend.CHARS_PER_TOKEN` holds.
DEFAULT_MODELS: dict[str, str] = {
    "local_low": "qwen2.5:7b",
    "local_high": "hf.co/bartowski/Qwen2.5-14B-Instruct-GGUF:Q4_K_M",
}

LOCAL_TIERS: tuple[ModelTier, ...] = ("local_low", "local_high")

# Sized for the larger of the two passes, with room to spare.
#
# This came down from 16,384 when the harness went two-pass, and the reason is
# worth keeping: 16,384 was sized for a ~14,100-token single-call prompt
# carrying all 66 anchor ladders, and that prompt is gone. Pass one is ~2,100
# tokens of taxonomy and calibration; pass two's worst case -- its short rubric
# plus the widest ladder selection the schema allows -- is ~1,400. With
# `num_predict` at 1,024 the larger arm needs well under 4,000, so 8,192 is
# better than double what either pass can ask for.
#
# Leaving it at 16,384 would not have been free. The KV cache is VRAM, and at
# 16k the 14B sat at 11.5GB of 12.4GB, which is the condition under which the
# WDDM driver evicted ~1GB mid-session and dropped its decode rate from 30 to
# 2.7 tok/s -- a session whose latency column had to be thrown away
# (`docs/lessons-learned.md`). Not the models' full 32,768 for the same reason,
# only more so.
#
# The preflight still checks both arms against this number rather than trusting
# the arithmetic above, and `_check_context_use` still checks what each pass
# actually evaluated.
DEFAULT_NUM_CTX = 8192

DEFAULT_TIMEOUT_SECONDS = 300.0

# How many cards a session draws from each bucket it was given.
#
# 30, not 100, because a session is meant to be *runnable in one sitting*: at
# the 14B's measured ~30s per card, 30 cards per tier is about half an hour of
# inference for a two-tier session, and 100 is most of a day. The trade is
# statistical and the report states it out loud — 30 paired cards place a win
# rate within roughly +/-18 points, which detects a large difference between
# the tiers and not a small one. Run the full 100 per bucket when a candidate
# conclusion needs to survive that interval.
DEFAULT_CARDS_PER_BUCKET = 30

# The default card draw, so two sessions over the same bucket and count compare
# the same cards unless a maintainer deliberately asks for a different set.
#
# That default is the useful one: the common reason to re-run is a changed
# prompt or a new model release, and holding the cards fixed is what makes the
# two sessions comparable at all. Pass a different `--draw-seed` for a fresh
# draw -- which is the right move before concluding anything, since a fixed
# 30-card set can flatter one model by accident.
DEFAULT_DRAW_SEED = 20261012

# A prompt that fills its window is indistinguishable from one that was clipped
# to fit, so treat "nearly full" as "probably truncated" and stop.
_CONTEXT_ALARM_RATIO = 0.95


class BenchmarkRunError(RuntimeError):
    """The run cannot proceed, and continuing would record misleading rows."""


@dataclass(frozen=True)
class SessionPlan:
    """Everything a session is, resolved before anything is called.

    Exists as a separate object from the session row so `--dry-run` can print
    exactly what *would* be recorded without opening a session, and so the two
    paths that produce one — a fresh draw and a resume of a stored session —
    converge on one shape before any model is touched.
    """

    cards: tuple[SampleCard, ...]
    buckets: tuple[str, ...]
    cards_per_bucket: int
    tiers: tuple[ModelTier, ...]
    models: Mapping[str, str]
    num_ctx: int
    draw_seed: int
    temperature: float
    model_seed: int | None
    system_prompts: Mapping[str, str]
    output_schemas: Mapping[str, Any]
    label: str | None = None
    sample_path: str | None = None
    sample_seed: int | None = None
    sample_reviewed: bool = False
    resume_session_id: str | None = None

    @property
    def card_ids(self) -> tuple[str, ...]:
        return tuple(card.oracle_id for card in self.cards)

    @property
    def total_calls(self) -> int:
        """Calls a complete session makes: one per (card, tier, pass).

        Two per card per tier, so a 30-card two-tier session is 120 calls
        rather than 60. Pass two is the cheap one — a short rubric plus a few
        ladders, answering two fields — so the wall clock does not double.
        """
        return len(self.cards) * len(self.tiers) * len(PASSES)

    def system_prompt(self, pass_name: PassName) -> str:
        """One pass's system block. Raises `KeyError` on an unknown pass."""
        return self.system_prompts[pass_name]

    def describe(self) -> str:
        """The config header a maintainer reads before committing hours."""
        lines = [
            f"Session:  {self.resume_session_id or '(new)'}"
            + (f"  label={self.label}" if self.label else ""),
            f"Buckets:  {', '.join(self.buckets)}  "
            f"({self.cards_per_bucket} card(s) each -> {len(self.cards)} total)",
            f"Tiers:    {' then '.join(self.tiers)}  "
            f"-> {self.total_calls} call(s)",
        ]
        for tier in self.tiers:
            lines.append(f"  {tier}: {self.models.get(tier, '(no model)')}")
        lines.append(
            f"Decoding: num_ctx={self.num_ctx:,}  temperature={self.temperature}  "
            f"seed={self.model_seed}"
        )
        lines.append(
            f"Draw:     seed={self.draw_seed}  from {self.sample_path or 'a sample'}"
            f" (sample seed {self.sample_seed})"
        )
        lines.append(
            "Prompts:  "
            + "  ".join(
                f"pass {name} {len(text):,}ch"
                for name, text in sorted(self.system_prompts.items())
            )
            + f"  sha256 {store.fingerprint_payload(dict(self.system_prompts))[:12]}"
        )
        lines.append(
            "Grammars: sha256 "
            f"{store.fingerprint_payload(dict(self.output_schemas))[:12]}"
            "  (the output schemas; they decide the grammar, so they are part "
            "of what makes two sessions comparable)"
        )
        if not self.sample_reviewed:
            lines.append(
                "  ! Sample buckets are unreviewed heuristics; per-bucket "
                "results are provisional."
            )
        return "\n".join(lines)


@dataclass
class TierOutcome:
    """What one tier's pass over the session's cards did."""

    tier: ModelTier
    model_name: str
    calls_made: int = 0
    schema_valid: int = 0
    schema_invalid: int = 0
    skipped_existing: int = 0
    failed: int = 0
    # Calls whose output was fine but whose timing was not a measurement, so
    # the row was not written. Nothing was lost: an unrecorded (card, tier)
    # pair is exactly what a resume re-runs, so a discard is a retry deferred
    # to the next run. See `timing_anomaly`.
    discarded_timing: int = 0
    latency_ms_total: int = 0
    # Cards whose pass one failed to validate, so pass two was never sent --
    # the second call takes the first's output as its input, and there is
    # nothing to rate. Counted separately from `schema_invalid` because one
    # bad pass one costs the session a whole card, not one row.
    pass_two_unreachable: int = 0
    # Per-pass call counts and wall clock, kept apart on purpose. A pass-one
    # call prefills ~2k tokens of taxonomy and generates seven fields; a
    # pass-two call prefills a short rubric plus a few ladders and generates
    # two. Averaging them produces a number describing neither, which is the
    # same mistake blending prefill into decode made (`timing_anomaly`).
    calls_by_pass: dict[str, int] = field(default_factory=dict)
    latency_ms_by_pass: dict[str, int] = field(default_factory=dict)
    vram_warning: str | None = None
    first_call_input_tokens: int | None = None
    tokens_per_second: float | None = None
    prefill_tokens_per_second: float | None = None
    aborted_reason: str | None = None

    @property
    def mean_latency_ms(self) -> float:
        """Mean over every call, both passes. See `mean_latency_ms_for`."""
        return self.latency_ms_total / self.calls_made if self.calls_made else 0.0

    def mean_latency_ms_for(self, pass_name: str) -> float:
        """Mean wall clock for one pass's calls. 0.0 if it made none."""
        calls = self.calls_by_pass.get(pass_name, 0)
        if not calls:
            return 0.0
        return self.latency_ms_by_pass.get(pass_name, 0) / calls

    def record_call(self, pass_name: str, latency_ms: int) -> None:
        """Count one recorded call, into both the total and its own pass."""
        self.calls_made += 1
        self.latency_ms_total += latency_ms
        self.calls_by_pass[pass_name] = self.calls_by_pass.get(pass_name, 0) + 1
        self.latency_ms_by_pass[pass_name] = (
            self.latency_ms_by_pass.get(pass_name, 0) + latency_ms
        )


@dataclass
class RunnerReport:
    """What a whole session did. Shaped after `pipeline.MetadataReport`."""

    db_path: Path
    sample_size: int
    sample_reviewed: bool
    session_id: str | None = None
    status: SessionStatus = "running"
    missing_cells: int = 0
    outcomes: list[TierOutcome] = field(default_factory=list)
    interrupted: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def calls_made(self) -> int:
        return sum(o.calls_made for o in self.outcomes)

    def summary(self) -> str:
        lines = [
            ("Run interrupted" if self.interrupted else "Run complete")
            + f": session {self.session_id or '(none)'} [{self.status}], "
            + f"{self.calls_made:,} call(s) in {self.elapsed_seconds:.1f}s"
            + f" -> {self.db_path}"
        ]
        for outcome in self.outcomes:
            lines.append(
                f"  {outcome.tier:10} {outcome.model_name:28} "
                f"calls={outcome.calls_made:,} valid={outcome.schema_valid:,} "
                f"invalid={outcome.schema_invalid:,} failed={outcome.failed:,} "
                f"skipped={outcome.skipped_existing:,} "
                + (
                    f"discarded={outcome.discarded_timing:,} "
                    if outcome.discarded_timing
                    else ""
                )
                + f"mean={outcome.mean_latency_ms / 1000:.1f}s"
                + (
                    "".join(
                        f" p{name}={outcome.mean_latency_ms_for(name) / 1000:.1f}s"
                        for name in PASSES
                        if outcome.calls_by_pass.get(name)
                    )
                )
                + (
                    f" gen={outcome.tokens_per_second:.1f}tok/s"
                    if outcome.tokens_per_second
                    else ""
                )
                + (
                    f" prefill={outcome.prefill_tokens_per_second:.0f}tok/s"
                    if outcome.prefill_tokens_per_second
                    else ""
                )
            )
            if outcome.discarded_timing:
                lines.append(
                    f"    ! {outcome.discarded_timing:,} call(s) discarded: wall "
                    "clock passed without inference running (a suspended host), "
                    "so their latency was not a measurement. The model output "
                    "was sound but was not kept, because one such row leaves "
                    "the tier's mean and p95 meaningless while its median looks "
                    "fine. Re-run with --resume to retry those cards."
                )
            if outcome.pass_two_unreachable:
                lines.append(
                    f"    ! {outcome.pass_two_unreachable:,} card(s) got no "
                    "pass two: their pass one did not validate, and the rating "
                    "call takes pass one's output as its input. Those cards "
                    "have no power_rating, so the session is PARTIAL rather "
                    "than complete -- which is the honest label, since a card "
                    "with no rating cannot be compared on one."
                )
            if outcome.vram_warning:
                lines.append(f"    ! {outcome.vram_warning}")
            if outcome.aborted_reason:
                lines.append(f"    ! ABORTED: {outcome.aborted_reason}")
        if self.missing_cells:
            lines.append(
                f"  ! {self.missing_cells:,} (card, tier, pass) cell(s) of this "
                "session have no row, so it is PARTIAL: the tiers did not "
                "answer the same cards and no win rate over it is a "
                "like-for-like comparison. Finish it with "
                f"`run --resume {self.session_id}`."
            )
        if not self.sample_reviewed:
            lines.append(
                "  ! Sample buckets are unreviewed heuristics; per-bucket results "
                "are provisional."
            )
        lines.append(
            "  ! schema_valid is ENFORCED by constrained decoding, not measured: "
            "it does not show whether these models honor the closed vocabulary "
            "unaided."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


def build_system_prompts() -> dict[str, str]:
    """The two system blocks, exactly as `pipeline.generate_metadata` builds them.

    Both are fixed strings reused for every card — the per-card anchor ladders
    ride in pass two's *user* message — which is what lets one fingerprint
    stand for a whole session, and what keeps both blocks prompt-cacheable in
    production.
    """
    return {
        "one": build_pass_one_system_prompt(),
        "two": build_pass_two_system_prompt(),
    }


def output_schemas() -> dict[str, Any]:
    """The JSON Schemas the two passes decode against.

    Recorded on the session beside the prompts, because under constrained
    decoding the schema *is* the grammar: a run is determined by both, and a
    store that fingerprints only the prompt will accept two sessions as
    comparable when they are not. That is not a hypothetical — S-002 and S-003
    of the previous store shared a prompt byte for byte and differed only in
    whether the tag fields were `required`, which decided whether either tier
    emitted `roles` at all (`docs/lessons-learned.md`).
    """
    return {
        "one": MetadataPassOne.model_json_schema(),
        "two": MetadataPassTwo.model_json_schema(),
    }


def plan_session(
    sample: CardSample,
    *,
    buckets: Sequence[str],
    tiers: Sequence[ModelTier],
    cards_per_bucket: int = DEFAULT_CARDS_PER_BUCKET,
    models: Mapping[str, str] | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    draw_seed: int = DEFAULT_DRAW_SEED,
    temperature: float = DEFAULT_TEMPERATURE,
    model_seed: int | None = DEFAULT_SEED,
    label: str | None = None,
    sample_path: Path | str | None = None,
    system_prompts: Mapping[str, str] | None = None,
    schemas: Mapping[str, Any] | None = None,
) -> SessionPlan:
    """Draw a session's card set and resolve its config. Calls nothing.

    `buckets` is required and has no default, which is the point: a run that
    did not say which difficulty it was measuring is how every row in the
    previous store ended up `easy` without anyone choosing that. The draw is
    stratified within each bucket (`sample.draw_session_cards`) and
    deterministic given `draw_seed`.

    Raises:
        BenchmarkRunError: no buckets, an unknown bucket name, no tiers, or a
            bucket that cannot supply `cards_per_bucket` cards.
    """
    if not buckets:
        raise BenchmarkRunError(
            "A session must name at least one difficulty bucket. There is no "
            "default: the bucket is what the session measures, and a run that "
            "did not choose one silently measures whichever cards happened to "
            "come first in the sample file."
        )
    if not tiers:
        raise BenchmarkRunError("A session must run at least one tier.")

    unknown = [bucket for bucket in buckets if bucket not in store.BUCKETS]
    if unknown:
        raise BenchmarkRunError(
            f"Unknown bucket(s) {', '.join(unknown)}. Choose from: "
            f"{', '.join(store.BUCKETS)}."
        )

    rng = random.Random(draw_seed)
    cards: list[SampleCard] = []
    for bucket in buckets:
        try:
            cards.extend(
                draw_session_cards(
                    sample, bucket=bucket, count=cards_per_bucket, rng=rng
                )
            )
        except ValueError as exc:
            raise BenchmarkRunError(str(exc)) from exc

    return SessionPlan(
        cards=tuple(cards),
        buckets=tuple(buckets),
        cards_per_bucket=cards_per_bucket,
        tiers=tuple(tiers),
        models=dict(models or DEFAULT_MODELS),
        num_ctx=num_ctx,
        draw_seed=draw_seed,
        temperature=temperature,
        model_seed=model_seed,
        system_prompts=(
            dict(system_prompts)
            if system_prompts is not None
            else build_system_prompts()
        ),
        output_schemas=dict(schemas) if schemas is not None else output_schemas(),
        label=label,
        sample_path=str(sample_path) if sample_path is not None else None,
        sample_seed=sample.seed,
        sample_reviewed=sample.reviewed,
    )


def plan_from_session(
    sample: CardSample,
    session_row: Mapping[str, object],
    *,
    system_prompts: Mapping[str, str] | None = None,
    schemas: Mapping[str, Any] | None = None,
) -> SessionPlan:
    """Rebuild a plan from a stored session, for `--resume`.

    The card set comes from the session's frozen `card_ids`, never from a fresh
    draw — resuming must continue the same comparison, not start a similar one.
    Names and buckets are looked up in the sample, which is why a resume needs
    the same sample file the session was drawn from.

    Raises:
        BenchmarkRunError: the sample no longer contains one of the session's
            cards, or the stored prompt no longer matches the rendered one.
    """
    stored_ids = store.session_card_ids(session_row)
    by_id = {card.oracle_id: card for card in sample.cards}
    missing = [card_id for card_id in stored_ids if card_id not in by_id]
    if missing:
        raise BenchmarkRunError(
            f"Session {session_row['id']!r} was drawn from a sample that no "
            f"longer holds {len(missing)} of its card(s), starting with "
            f"{missing[0]}. Resuming would run a different card set than the "
            "already-recorded tier did. Point --sample at the file the session "
            "was drawn from, or start a new session."
        )

    rendered = (
        dict(system_prompts) if system_prompts is not None else build_system_prompts()
    )
    if store.fingerprint_payload(rendered) != str(
        session_row["system_prompts_sha256"]
    ):
        raise BenchmarkRunError(
            f"The system prompts have changed since session {session_row['id']!r} "
            "started (their recorded SHA-256 does not match the rendered ones). "
            "Finishing it now would compare one tier's answers against another "
            "tier's answers to a different prompt -- the taxonomy, rubric and "
            "game_stage calibration all live in those blocks.\n"
            "Fix: start a new session, which will record the new prompts."
        )

    rendered_schemas = dict(schemas) if schemas is not None else output_schemas()
    if store.fingerprint_payload(rendered_schemas) != str(
        session_row["output_schemas_sha256"]
    ):
        raise BenchmarkRunError(
            f"The output schemas have changed since session {session_row['id']!r} "
            "started. Under constrained decoding the schema is the grammar, so "
            "resuming would append rows the model could not have produced "
            "earlier in the same session -- with nothing in the store marking "
            "the difference.\n"
            "This check exists because the store went without it once: two "
            "sessions shared a prompt byte for byte while a `required` list "
            "changed underneath them, which was the difference between a tier "
            "emitting roles on every card and on none "
            "(`docs/lessons-learned.md`).\n"
            "Fix: start a new session, which will record the new schemas."
        )

    return SessionPlan(
        cards=tuple(by_id[card_id] for card_id in stored_ids),
        buckets=store.session_buckets(session_row),
        cards_per_bucket=int(session_row["cards_per_bucket"]),  # type: ignore[arg-type]
        tiers=store.session_tiers(session_row),  # type: ignore[arg-type]
        models=store.session_models(session_row),
        num_ctx=int(session_row["num_ctx"]),  # type: ignore[arg-type]
        draw_seed=int(session_row["draw_seed"]),  # type: ignore[arg-type]
        temperature=float(session_row["temperature"]),  # type: ignore[arg-type]
        model_seed=(
            int(session_row["model_seed"])  # type: ignore[arg-type]
            if session_row["model_seed"] is not None
            else None
        ),
        system_prompts=rendered,
        output_schemas=rendered_schemas,
        label=session_row["label"],  # type: ignore[arg-type]
        sample_path=session_row["sample_path"],  # type: ignore[arg-type]
        sample_seed=sample.seed,
        sample_reviewed=sample.reviewed,
        resume_session_id=str(session_row["id"]),
    )


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def demo_row_count(connection: sqlite3.Connection) -> int:
    """How many synthetic rows the store holds."""
    cursor = connection.execute(
        "SELECT COUNT(*) FROM benchmark_runs WHERE model_name LIKE ?",
        (f"{store.DEMO_MODEL_PREFIX}%",),
    )
    return int(cursor.fetchone()[0])


def _refuse_demo_rows(connection: sqlite3.Connection, db_path: Path) -> None:
    count = demo_row_count(connection)
    if not count:
        return
    raise BenchmarkRunError(
        f"{db_path} holds {count:,} synthetic demo row(s). Measured runs must not "
        "mix with them: every per-bucket mean, latency percentile and verdict "
        "split in `report` would be computed over both, and the 'demo/' prefix "
        "that makes them obvious in a listing does nothing for an average.\n"
        "`seed-demo` refuses the same mixture from the other direction.\n"
        "Fix: run against a fresh store with --db PATH, or delete this file and "
        "re-run `init`."
    )


def existing_cells(
    connection: sqlite3.Connection, session_id: str | None = None
) -> set[tuple[str, str, str]]:
    """The (oracle_id, tier, pass) cells recorded, optionally for one session.

    A direct query rather than `store.iter_runs`, which returns whole rows —
    and every row joins its session's system prompt, so resuming would read
    far more than a question about three columns needs.

    Resume passes a `session_id`. Without one this spans the whole store, which
    is only useful for diagnostics: the same card may legitimately appear in
    several sessions, and skipping it because *another* session already ran it
    is exactly the cross-session drift sessions exist to prevent.
    """
    if session_id is not None:
        return store.recorded_cells(connection, session_id)
    cursor = connection.execute(
        "SELECT card_oracle_id, model_tier, pass_name FROM benchmark_runs"
    )
    return {(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# backend construction
# ---------------------------------------------------------------------------


def build_local_backends(
    tiers: Sequence[ModelTier],
    *,
    host: str = DEFAULT_HOST,
    models: Mapping[str, str] | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int | None = DEFAULT_SEED,
) -> dict[ModelTier, ObservableMetadataBackend]:
    """One `OllamaMetadataBackend` per requested local tier.

    `temperature` and `seed` are passed through rather than left to the
    backend's defaults so that the values recorded on the session are the
    values the backends were actually built with — one source, checkable after
    the fact, instead of two constants that agree until one of them moves.

    Config arrives as arguments and defaults, never from settings. The
    production generator still routes every card to the frontier backend, so a
    `KnowledgeSettings` field for Ollama would configure nothing the pipeline
    reads; and `docs/model-providers.md`'s `MODEL_*` vars are the *client*
    plane's, which this must not borrow. When the benchmark picks a tier and
    production routes locally, that change adds the setting.
    """
    resolved = dict(DEFAULT_MODELS)
    if models:
        resolved.update(models)

    backends: dict[ModelTier, ObservableMetadataBackend] = {}
    for tier in tiers:
        if tier not in LOCAL_TIERS:
            raise BenchmarkRunError(
                f"tier {tier!r} has no local backend. This harness runs "
                f"{' and '.join(LOCAL_TIERS)} through Ollama; the frontier tier is "
                "not wired yet (see runner.py's module docstring)."
            )
        backends[tier] = OllamaMetadataBackend(
            model=resolved[tier],
            num_ctx=num_ctx,
            host=host,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            seed=seed,
        )
    return backends


# ---------------------------------------------------------------------------
# per-tier execution
# ---------------------------------------------------------------------------

_WARMUP_CARD_FACTS = """\
Name: Sol Ring
Mana cost: {1}
Mana value: 1
Type line: Artifact
Color identity: (colorless)
Oracle text:
{T}: Add {C}{C}.
Legal in: commander
"""


def _preflight_tier(
    backend: ObservableMetadataBackend,
    *,
    plan: SessionPlan,
    outcome: TierOutcome,
) -> None:
    """Check reachability, the model tag, and the context window before calling.

    Only `OllamaMetadataBackend` offers these checks; a fake backend in a test
    does not, so they're feature-detected rather than required by the protocol.

    **Checked against the larger arm, which is no longer the larger system
    block.** Pass two's system prompt is the short one — under 2,000 characters
    of rubric — but its *user* message carries the anchor ladders pass one's
    tags selected, which is where most of its tokens are. So the window is
    checked against pass one's block and against a worst-case pass two: the
    widest ladder selection the schema permits. Sizing on the system blocks
    alone would pass here and truncate the ladders out of every rating call,
    which is the exact failure this check exists for and the one that leaves
    nothing in the output to show for it.
    """
    preflight = getattr(backend, "preflight", None)
    if preflight is None:
        return

    arms = {
        "one": plan.system_prompt("one"),
        "two": plan.system_prompt("two") + _worst_case_rating_request(),
    }
    for pass_name, prompt in sorted(arms.items()):
        found = preflight(system_prompt=prompt)
        problems = found.problems()
        if problems:
            raise BenchmarkRunError(
                f"{outcome.tier} ({outcome.model_name}) is not ready for pass "
                f"{pass_name}:\n  - " + "\n  - ".join(problems)
            )
        logger.info(
            "%s ready for pass %s: %s, num_ctx=%d (prompt needs ~%d), "
            "model context %s",
            outcome.tier,
            pass_name,
            outcome.model_name,
            found.num_ctx,
            found.required_tokens,
            found.model_context_length,
        )


def _worst_case_rating_request() -> str:
    """The largest pass-two user message the schema can produce.

    `render_rating_request` embeds a ladder per role and theme pass one
    assigned, capped by `anchors.ladders_for`, so the biggest possible message
    comes from a pass one that filled both lists to their `max_length`. Built
    from a synthetic `MetadataPassOne` rather than estimated with a multiplier,
    because the thing being sized is the real renderer: a guess here is a guess
    about whether the anchor block survives, and that is the one thing the
    window check is for.
    """
    roles = list(Role)[:MAX_TAGS_PER_CARD]
    themes = list(Theme)[:MAX_TAGS_PER_CARD]
    widest = MetadataPassOne(
        mechanics="x",
        synergy_tags=[],
        roles=roles,
        primary_role=roles[0],
        themes=themes,
        strengths=["x"],
        weaknesses=["x"],
        game_stage=GameStageProfile(early=5, mid=5, late=5),
    )
    return render_rating_request(card_facts=_WARMUP_CARD_FACTS, pass_one=widest)


def _warm_up(
    backend: ObservableMetadataBackend,
    *,
    system_prompt: str,
    outcome: TierOutcome,
) -> GenerationAttempt:
    """One unrecorded call, so model-load time doesn't land on card #1.

    Loading 9GB of weights takes seconds to tens of seconds. Charged to the
    first card it would make that card an outlier in every latency percentile
    the report computes, and p95 over a session is sensitive to exactly one
    such value.

    The attempt is returned rather than discarded because it is also the
    cheapest honest measurement of whether the model is really running on the
    GPU — see `_check_residency`.
    """
    logger.info(
        "Warming up %s (%s) -- this call is not recorded.",
        outcome.tier,
        outcome.model_name,
    )
    try:
        # Pass one's shape, because the warm-up sends pass one's prompt: the
        # point is to pay the model load before card #1, and the load is the
        # same weights either way.
        return backend.generate_observed(
            system=system_prompt,
            prompt=_WARMUP_CARD_FACTS,
            output_model=MetadataPassOne,
        )
    except MetadataGenerationError as exc:
        raise BenchmarkRunError(
            f"{outcome.tier} ({outcome.model_name}) failed its warm-up call: {exc}"
        ) from exc


# Decode throughput below which a quantized 7B-14B model is probably not
# executing on a GPU. Measured on this project's hardware for calibration:
# 7B generates at ~59 tok/s and 14B at ~30 tok/s, both fully GPU-resident.
# CPU inference for models this size lands around 2-5 tok/s, so the threshold
# sits well clear of both.
#
# **This is compared against the generation rate only**, never a blended one.
# See `GenerationAttempt.generation_tokens_per_second`.
_MIN_PLAUSIBLE_GPU_TOKENS_PER_SECOND = 10.0

# Below this decode rate, the clock was not measuring inference at all.
#
# Deliberately far under `_MIN_PLAUSIBLE_GPU_TOKENS_PER_SECOND` because the two
# answer different questions. That one asks "is this model on the GPU?" and a
# wrong answer is a warning a maintainer reads. This one asks "is this number a
# measurement?" and a wrong answer silently deletes a model's good output, so it
# only fires where no hardware could be responsible: pure CPU inference on a 14B
# still manages 2-5 tok/s, and the observed suspension came in at 0.0044.
_MIN_PHYSICAL_DECODE_RATE = 0.5

# Wall clock belonging to no phase, past which the call is not a measurement.
#
# A healthy call leaves tens of milliseconds unaccounted; a full minute cannot
# be explained by HTTP, JSON or tokenizer setup. Generous on purpose -- the cost
# of firing late is one bad row, and the cost of firing early is discarding a
# sound one.
_MAX_UNACCOUNTED_MS = 60_000


def timing_anomaly(attempt: GenerationAttempt) -> str | None:
    """Why this call's timing is not a measurement, or `None` if it is fine.

    `latency_ms` comes from Ollama's `total_duration`, which is wall clock. If
    the host suspends mid-call, the clock keeps running and the suspension is
    billed to inference: this project recorded a single call at 49,866,300 ms
    (13.8 hours) for 220 tokens, which left the tier's mean at 3,365,675 ms and
    its p95 at 15,004,868 ms. The median survived, so the corruption was
    survivable but invisible -- precisely the shape of bug worth a guard.

    Two checks, because a suspension can land in either place:

    * **Decode rate** catches a stall *during* generation. `eval_duration` is
      also wall clock, so the stall inflates that phase too and the ratio
      collapses -- llama.cpp itself reported `0.00 tokens per second`.
    * **Unaccounted time** catches a stall *outside* generation, where every
      phase timer looks healthy and only the total is wrong. Nothing would
      detect that from the rate alone.

    Both are deliberately one-sided: they reject impossibly *slow* calls, never
    impossibly fast ones. A fast call is what prefix caching is supposed to
    produce, and a guard that distrusted it would reject the good case.

    Returns a reason suitable for a log line, so the caller never has to
    reconstruct which check fired.

    Note that the veto can only be applied at write time for the *discard*
    decision -- but since `load_ms`/`prompt_eval_ms`/`eval_ms` are now stored
    on the row, the same judgment is at least re-derivable afterwards from the
    store, which it was not when only `latency_ms` was kept.
    """
    rate = attempt.generation_tokens_per_second
    if rate is not None and rate < _MIN_PHYSICAL_DECODE_RATE:
        return (
            f"decoded {attempt.output_tokens:,} token(s) in "
            f"{attempt.eval_ms / 1000:,.1f}s ({rate:.4f} tok/s), which is "
            "slower than any hardware produces -- the clock advanced while "
            "nothing was computing (a suspended host or descheduled process)"
        )

    unaccounted = attempt.unaccounted_ms
    if unaccounted is not None and unaccounted > _MAX_UNACCOUNTED_MS:
        return (
            f"{unaccounted / 1000:,.1f}s of its {attempt.latency_ms / 1000:,.1f}s "
            f"belongs to no phase (load {attempt.load_ms / 1000:,.1f}s + prefill "
            f"{attempt.prompt_eval_ms / 1000:,.1f}s + generation "
            f"{attempt.eval_ms / 1000:,.1f}s), so wall clock passed without "
            "inference running"
        )

    return None


def _check_residency(
    backend: ObservableMetadataBackend,
    outcome: TierOutcome,
    warm_up: GenerationAttempt,
) -> None:
    """Warn when a tier is probably not running on the GPU.

    Recorded on the outcome rather than only logged, so it reaches the run
    summary. A model executing on the CPU still answers correctly — it just
    answers slower, and for local tiers `cost_usd` is always 0, so `latency_ms`
    is the entire basis on which the two are compared. A tier that silently ran
    on the CPU does not make the benchmark slow; it makes it measure something
    else.

    **Judged on decode rate alone, and this is the whole subtlety.** An earlier
    version of this check divided output tokens by *total* call time, which
    bundles one-off model load and a ~10,200-token prompt prefill in with the
    ~200 tokens of generation. On the 14B that produced 3.9 "tok/s" and a
    confident conclusion that the model was running on the CPU. It was not:
    the server log showed all 49 layers offloaded, ~10.3GB resident in 12GB of
    VRAM, and a true decode rate of 30 tok/s — healthy, and about half the 7B's
    59 as doubling the parameters predicts. The metric invented the problem.
    `docs/lessons-learned.md` records it.

    So the rate here comes from `eval_duration` only. Prefill is reported
    alongside it because it is genuinely slower on the larger model and is what
    dominates a *cold* call, but it is not evidence about residency.

    **A warm-up whose timing is not a measurement is not evidence either.** If
    the host suspended during that one call, the decode rate collapses toward
    zero and this check would announce CPU inference with total confidence —
    the same wrong conclusion as before, reached by a different route. So
    `timing_anomaly` is consulted first, and a tier whose warm-up was stalled
    gets no residency verdict at all rather than a false one.
    """
    stalled = timing_anomaly(warm_up)
    if stalled is not None:
        # Deliberately leaves `tokens_per_second` unset: a rate derived from a
        # suspended call is worse than no rate, because it would be printed in
        # the run summary beside real ones.
        logger.warning(
            "%s: skipping the residency check -- the warm-up call %s. Its "
            "throughput says nothing about where the weights are.",
            outcome.model_name,
            stalled,
        )
        return

    rate = warm_up.generation_tokens_per_second
    outcome.tokens_per_second = rate
    outcome.prefill_tokens_per_second = warm_up.prefill_tokens_per_second

    if rate is None:
        return

    detail = f"{rate:.1f} tok/s generating"
    if outcome.prefill_tokens_per_second:
        detail += f", {outcome.prefill_tokens_per_second:.0f} tok/s prefill"

    if rate < _MIN_PLAUSIBLE_GPU_TOKENS_PER_SECOND:
        # Only now is `/api/ps` worth quoting -- as a detail on a problem
        # already established, not as the thing that establishes it.
        reported = _reported_residency(backend, outcome.model_name)
        outcome.vram_warning = (
            f"{outcome.model_name} decoded at only {rate:.1f} tokens/second on the "
            "warm-up call. For a quantized model this size that suggests it is not "
            "running on the GPU, so its latency_ms would describe where the weights "
            "ended up rather than the model"
            + (f" (Ollama reports {reported})" if reported else "")
            + ". Check the Ollama server log for `offloaded N/M layers to GPU`: if "
            "N < M, free VRAM or lower --num-ctx. If all layers are offloaded, the "
            "weights are fine and something else is throttling. Either way, do not "
            "compare this tier's latency against another's until it is resolved."
        )
        logger.warning("%s", outcome.vram_warning)
    else:
        logger.info("%s: %s.", outcome.model_name, detail)


def _reported_residency(
    backend: ObservableMetadataBackend, model_name: str
) -> str | None:
    """What `/api/ps` claims about a model's VRAM use, if it says anything.

    Deliberately only called to add colour to a problem detected by
    measurement. On AMD/Windows this can report `size_vram == size` for a model
    partly in GTT (shared system memory reached over PCIe), so it is not
    trustworthy on its own in either direction.
    """
    resident = getattr(backend, "resident_models", None)
    if resident is None:
        return None
    for entry in resident():
        if entry.get("model") != model_name:
            continue
        size, in_vram = entry.get("size"), entry.get("size_vram")
        if size and in_vram is not None:
            return f"{in_vram / 1e9:.1f}GB of {size / 1e9:.1f}GB in VRAM"
        return None
    return None


def _check_context_use(
    attempt: GenerationAttempt,
    outcome: TierOutcome,
    num_ctx: int,
    *,
    pass_name: PassName = "one",
) -> None:
    """Abort if the first call evaluated suspiciously close to the whole window.

    This is the second half of the truncation guard. The first half asked
    whether the prompt *should* fit; this asks what the model actually read. A
    clipped prompt reports a token count pinned near `num_ctx`, and in a rating
    call the anchor ladders are what got cut.

    Run once per pass rather than once per tier, because the two passes send
    prompts of very different sizes and only pass two's grows with the card:
    its ladder selection comes from pass one's tags, so a card with three
    ladders sends a far larger message than a card with none.
    """
    outcome.first_call_input_tokens = attempt.input_tokens
    if not attempt.input_tokens:
        return
    if attempt.input_tokens >= num_ctx * _CONTEXT_ALARM_RATIO:
        raise BenchmarkRunError(
            f"{outcome.tier} evaluated {attempt.input_tokens:,} prompt tokens in "
            f"pass {pass_name} against num_ctx={num_ctx:,}. A prompt that fills "
            "its window has probably been truncated, and in a rating call the "
            "calibration ladders are what gets cut -- so every power_rating "
            "from this run would be uncalibrated with nothing in the output to "
            "show it. Raise --num-ctx and re-run."
        )
    logger.info(
        "%s evaluated %d prompt tokens of %d available in pass %s.",
        outcome.tier,
        attempt.input_tokens,
        num_ctx,
        pass_name,
    )


class _CallFailed(Exception):
    """One call could not be made at all, so the tier stops.

    Not a schema failure and never recorded as one: folding a server outage
    into the schema-validity rate would make an unreachable model look like a
    model that answers badly. Raised out of `_record_call` so both passes
    share one handler.
    """

    def __init__(self, card: SampleCard, pass_name: PassName, cause: Exception):
        super().__init__(str(cause))
        self.card = card
        self.pass_name = pass_name


class _CallDiscarded(Exception):
    """The call's timing was not a measurement, so nothing was written."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _record_call(
    *,
    backend: ObservableMetadataBackend,
    tier: ModelTier,
    pass_name: PassName,
    output_model: type,
    system_prompt: str,
    user_prompt: str,
    sample_card: SampleCard,
    session_id: str,
    plan: SessionPlan,
    connection: sqlite3.Connection,
    outcome: TierOutcome,
) -> GenerationAttempt:
    """Make one pass's call, write its row, and return the attempt.

    Shared by both passes, because everything except the prompts and the output
    model is identical between them: the same discard rule, the same
    once-per-tier context check, the same row shape. Two copies of this would
    have been two places for the recording rules to drift.

    Raises:
        _CallFailed: the call could not be made.
        _CallDiscarded: it completed but its timing was not a measurement, so
            no row was written and a resume will retry the cell.
    """
    try:
        attempt = backend.generate_observed(
            system=system_prompt, prompt=user_prompt, output_model=output_model
        )
    except MetadataGenerationError as exc:
        raise _CallFailed(sample_card, pass_name, exc) from exc

    # Before the context check, which reads `input_tokens`: a suspended call's
    # token counts are sound, but letting a discarded call satisfy the
    # once-per-tier check would spend it on a row nobody keeps.
    anomaly = timing_anomaly(attempt)
    if anomaly is not None:
        raise _CallDiscarded(anomaly)

    # Once per tier *per pass*: the two send prompts of very different sizes,
    # so pass one fitting the window says nothing about pass two, whose user
    # message carries the anchor ladders pass one selected.
    if not outcome.calls_by_pass.get(pass_name):
        _check_context_use(attempt, outcome, plan.num_ctx, pass_name=pass_name)

    store.record_run(
        connection,
        BenchmarkRun(
            session_id=session_id,
            card_oracle_id=sample_card.oracle_id,
            card_name=sample_card.name,
            bucket=sample_card.bucket,
            model_tier=tier,
            model_name=outcome.model_name,
            pass_name=pass_name,
            user_prompt=user_prompt,
            raw_response=attempt.raw_response,
            schema_valid=attempt.schema_valid,
            parsed_output=(
                attempt.parsed.model_dump(mode="json")
                if attempt.parsed is not None
                else None
            ),
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            latency_ms=attempt.latency_ms,
            load_ms=attempt.load_ms,
            prompt_eval_ms=attempt.prompt_eval_ms,
            eval_ms=attempt.eval_ms,
            cost_usd=attempt.cost_usd,
        ),
    )

    outcome.record_call(pass_name, attempt.latency_ms)
    if attempt.schema_valid:
        outcome.schema_valid += 1
    else:
        outcome.schema_invalid += 1
        logger.warning(
            "%s: %s pass %s did not validate -- %s",
            tier,
            sample_card.name,
            pass_name,
            attempt.error,
        )
    return attempt


def _stored_pass_one(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    oracle_id: str,
    tier: ModelTier,
) -> MetadataPassOne | None:
    """Pass one's recorded output for a cell, re-validated. `None` if unusable.

    Only a resume needs this, and it needs it for a shape the grid now allows:
    pass one recorded, pass two interrupted. Pass two's prompt is *built from*
    pass one's answer, so without this the resume would have to re-run a call
    it already paid for — and re-running it would write a second row for a cell
    the UNIQUE constraint forbids.

    Re-validated rather than trusted, because the stored JSON went through
    `model_dump` and comes back as a plain dict: if the schema has changed
    since, this returns `None` and the caller re-runs the card rather than
    building pass two's prompt out of a shape that no longer exists.
    """
    rows = store.iter_runs(
        connection,
        session_id=session_id,
        model_tier=tier,
        card_oracle_id=oracle_id,
        pass_name="one",
    )
    if not rows:
        return None
    raw = rows[-1]["parsed_output"]
    if not raw:
        return None
    try:
        return MetadataPassOne.model_validate_json(raw)
    except Exception:  # noqa: BLE001 - a stale shape is a miss, not a crash
        logger.warning(
            "Stored pass one for %s / %s no longer validates against "
            "MetadataPassOne; re-running the card.",
            oracle_id,
            tier,
        )
        return None


def _run_tier(
    *,
    tier: ModelTier,
    backend: ObservableMetadataBackend,
    session_id: str,
    plan: SessionPlan,
    cards: Mapping[str, Card],
    connection: sqlite3.Connection,
    skip: set[tuple[str, str, str]],
) -> TierOutcome:
    model_name = getattr(backend, "model", None) or getattr(backend, "name", tier)
    outcome = TierOutcome(tier=tier, model_name=str(model_name))

    _preflight_tier(backend, plan=plan, outcome=outcome)

    # The session's frozen list, in session order -- never a per-tier view of
    # it. A card is still pending while *either* of its two cells is missing,
    # which is what lets a resume finish a card whose rating call was the one
    # that got interrupted.
    pending = [
        card
        for card in plan.cards
        if card.oracle_id in cards
        and any((card.oracle_id, tier, name) not in skip for name in PASSES)
    ]
    outcome.skipped_existing = len(plan.cards) - len(pending)

    if not pending:
        logger.info("%s: nothing to do (every card already recorded).", tier)
        return outcome

    warm_up = _warm_up(
        backend, system_prompt=plan.system_prompt("one"), outcome=outcome
    )
    _check_residency(backend, outcome, warm_up)

    total = len(pending)
    for position, sample_card in enumerate(pending, start=1):
        card = cards[sample_card.oracle_id]
        card_facts = render_card_facts(card)
        elapsed_ms = 0

        try:
            # ---- pass one: what the card is. No ladders in the prompt.
            if (sample_card.oracle_id, tier, "one") in skip:
                pass_one = _stored_pass_one(
                    connection,
                    session_id=session_id,
                    oracle_id=sample_card.oracle_id,
                    tier=tier,
                )
            else:
                attempt = _record_call(
                    backend=backend,
                    tier=tier,
                    pass_name="one",
                    output_model=MetadataPassOne,
                    system_prompt=plan.system_prompt("one"),
                    user_prompt=card_facts,
                    sample_card=sample_card,
                    session_id=session_id,
                    plan=plan,
                    connection=connection,
                    outcome=outcome,
                )
                elapsed_ms += attempt.latency_ms
                pass_one = attempt.parsed if isinstance(
                    attempt.parsed, MetadataPassOne
                ) else None

            if pass_one is None:
                # No pass one, no pass two: the rating call takes pass one's
                # answer as its input. The cell stays missing and the session
                # is `partial`, which is the honest label for a card that has
                # no power_rating to compare.
                outcome.pass_two_unreachable += 1
                logger.warning(
                    "%s [%d/%d] %s: pass one unusable, so no rating call was "
                    "made. This card is missing from the comparison.",
                    tier,
                    position,
                    total,
                    sample_card.name,
                )
                continue

            # The model was never asked about these -- `derive_themes` reads
            # them off the card. Merged in before pass two exactly as
            # `pipeline.generate_metadata` does it, so the ladders pass two is
            # shown are the ladders production would show it.
            derived = [Theme(value) for value in derive_themes(card)]
            if derived:
                pass_one = pass_one.with_derived_themes(derived)

            # ---- pass two: how good it is, against only the selected ladders.
            if (sample_card.oracle_id, tier, "two") not in skip:
                attempt = _record_call(
                    backend=backend,
                    tier=tier,
                    pass_name="two",
                    output_model=MetadataPassTwo,
                    system_prompt=plan.system_prompt("two"),
                    user_prompt=render_rating_request(
                        card_facts=card_facts, pass_one=pass_one
                    ),
                    sample_card=sample_card,
                    session_id=session_id,
                    plan=plan,
                    connection=connection,
                    outcome=outcome,
                )
                elapsed_ms += attempt.latency_ms

        except _CallFailed as failure:
            # Stop the tier rather than retry: whatever broke the connection
            # will still be broken on the next card, and every row already
            # written is committed, so `run --resume` continues from here.
            outcome.failed += 1
            outcome.aborted_reason = (
                f"pass {failure.pass_name} failed on {failure.card.name} "
                f"({failure.card.oracle_id}): {failure}"
            )
            logger.error(
                "%s: %s Stopping this tier; already-recorded calls are committed "
                "and `run --resume %s` continues from here.",
                tier,
                outcome.aborted_reason,
                session_id,
            )
            return outcome

        except _CallDiscarded as discarded:
            outcome.discarded_timing += 1
            logger.warning(
                "%s [%d/%d] %s DISCARDED: %s. The output was not kept, so "
                "resuming this session will retry this card.",
                tier,
                position,
                total,
                sample_card.name,
                discarded.reason,
            )
            continue

        logger.info(
            "%s [%d/%d] %s (%s) %.1fs across both passes",
            tier,
            position,
            total,
            sample_card.name,
            sample_card.bucket,
            elapsed_ms / 1000,
        )

    return outcome


def _unload(backend: ObservableMetadataBackend, tier: str) -> None:
    unload = getattr(backend, "unload", None)
    if unload is None:
        return
    logger.info("Unloading %s to free VRAM before the next tier.", tier)
    unload()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_benchmark(
    *,
    plan: SessionPlan,
    backends: Mapping[ModelTier, ObservableMetadataBackend],
    db_path: Path | None = None,
    session: Session | None = None,
    connection: sqlite3.Connection | None = None,
) -> RunnerReport:
    """Run one paired session: `plan.cards` through each tier in turn.

    Args:
        plan: the resolved session — cards, tiers, models, decoding knobs and
            the system prompt (`plan_session` or `plan_from_session`).
        backends: one backend per tier in `plan.tiers`.
        db_path: the benchmark store. Defaults to `store.DEFAULT_DB_PATH`.

    `session` and `connection` exist so tests can inject fakes, matching
    `pipeline.generate_metadata`'s convention. Production callers pass neither.

    The session row is created before the first call and closed with a status
    afterwards. `complete` requires every (card, tier) cell to have a row —
    anything less is `partial`, because a win rate over a partial grid is a
    comparison of two different card sets.
    """
    resolved_db = Path(db_path) if db_path is not None else store.DEFAULT_DB_PATH

    missing_backends = [tier for tier in plan.tiers if tier not in backends]
    if missing_backends:
        raise BenchmarkRunError(
            f"No backend supplied for tier(s): {', '.join(missing_backends)}"
        )

    owns_connection = connection is None
    connection = connection or store.connect(resolved_db)

    owns_session = session is None
    session = session or new_session()

    report = RunnerReport(
        db_path=resolved_db,
        sample_size=len(plan.cards),
        sample_reviewed=plan.sample_reviewed,
        session_id=plan.resume_session_id,
    )

    try:
        _refuse_demo_rows(connection, resolved_db)

        cards = cards_for_sample(
            CardSample(cards=list(plan.cards)), session, oracle_ids=plan.card_ids
        )
        unresolved = [card for card in plan.cards if card.oracle_id not in cards]
        if unresolved:
            raise BenchmarkRunError(
                f"{len(unresolved)} drawn card(s) are not in the corpus, starting "
                f"with {unresolved[0].name!r} ({unresolved[0].oracle_id}). Re-run "
                "`benchmark sample --validate` against this database."
            )

        for pass_name in PASSES:
            block = plan.system_prompt(pass_name)
            logger.info(
                "Pass %s system prompt: %d chars (~%d tokens estimated), "
                "num_ctx=%d.",
                pass_name,
                len(block),
                estimate_prompt_tokens(block),
                plan.num_ctx,
            )

        if plan.resume_session_id is None:
            session_id = store.create_session(
                connection,
                BenchmarkSession(
                    buckets=plan.buckets,
                    cards_per_bucket=plan.cards_per_bucket,
                    card_ids=plan.card_ids,
                    tiers=plan.tiers,
                    models=dict(plan.models),
                    draw_seed=plan.draw_seed,
                    num_ctx=plan.num_ctx,
                    temperature=plan.temperature,
                    model_seed=plan.model_seed,
                    system_prompts=dict(plan.system_prompts),
                    output_schemas=dict(plan.output_schemas),
                    label=plan.label,
                    sample_path=plan.sample_path,
                    sample_seed=plan.sample_seed,
                ),
            )
        else:
            session_id = plan.resume_session_id
            store.require_session(connection, session_id)
        report.session_id = session_id

        skip = store.recorded_cells(connection, session_id)
        if skip:
            logger.info(
                "Resuming %s: %d of %d (card, tier, pass) cell(s) already "
                "recorded.",
                session_id,
                len(skip),
                plan.total_calls,
            )

        for tier in plan.tiers:
            backend = backends[tier]
            try:
                outcome = _run_tier(
                    tier=tier,
                    backend=backend,
                    session_id=session_id,
                    plan=plan,
                    cards=cards,
                    connection=connection,
                    skip=skip,
                )
                report.outcomes.append(outcome)
                if outcome.aborted_reason:
                    logger.error(
                        "Stopping the session: %s could not finish, so a later "
                        "tier's numbers would not be comparable against it.",
                        tier,
                    )
                    break
            except KeyboardInterrupt:
                report.interrupted = True
                logger.warning(
                    "Interrupted during %s. Every completed call is committed; "
                    "`run --resume %s` continues.",
                    tier,
                    session_id,
                )
                break
            finally:
                # Always free the weights, including on the way out of a failure:
                # the next tier (or the next attempt) needs the VRAM.
                _unload(backend, tier)

        missing = store.missing_cells(
            connection, session_id, card_ids=plan.card_ids, tiers=plan.tiers
        )
        report.missing_cells = len(missing)
        aborted = any(outcome.aborted_reason for outcome in report.outcomes)
        if aborted:
            report.status = "aborted"
        elif missing:
            report.status = "partial"
        else:
            report.status = "complete"
        store.finish_session(connection, session_id, status=report.status)

        report.finished_at = datetime.now(timezone.utc)
        return report
    finally:
        if owns_session:
            session.close()
        if owns_connection:
            connection.close()


def render_prompts(
    plan: SessionPlan, session: Session | None = None, *, limit: int = 2
) -> str:
    """The dry-run view: what would be sent, without sending it.

    Diverges from `metadata_generator --dry-run`, which still spends real API
    calls. There is no billing to warn about on a local runtime, so the useful
    smoke test here is the opposite one — confirm the drawn cards, the resolved
    config and the prompt before committing hours of inference.
    """
    if session is None:
        with new_session() as owned:
            return render_prompts(plan, owned, limit=limit)

    cards = cards_for_sample(
        CardSample(cards=list(plan.cards)), session, oracle_ids=plan.card_ids
    )

    out = [
        plan.describe(),
        "",
        f"CARDS DRAWN ({len(plan.cards)}):",
    ]
    for sample_card in plan.cards:
        mark = "" if sample_card.oracle_id in cards else "   !! not in corpus"
        out.append(f"  [{sample_card.bucket:6}] {sample_card.name}{mark}")

    for pass_name in PASSES:
        block = plan.system_prompt(pass_name)
        out += [
            "",
            "=" * 72,
            f"SYSTEM PROMPT -- pass {pass_name} ({len(block):,} chars, "
            f"~{estimate_prompt_tokens(block):,} tokens estimated)",
            "=" * 72,
            block,
        ]

    # Pass two's user message is the one worth sizing, because it is where the
    # ladders are -- so the worst case the window was checked against is shown
    # here too, rather than left as a number in a log line.
    worst_case = _worst_case_rating_request()
    out += [
        "",
        "=" * 72,
        f"USER PROMPT -- pass two, WORST CASE ({len(worst_case):,} chars, "
        f"~{estimate_prompt_tokens(worst_case):,} tokens estimated)",
        "=" * 72,
        "Not a real card: a synthetic pass one with its roles and themes filled",
        f"to the schema's ceiling of {MAX_TAGS_PER_CARD}, which is the widest",
        "ladder selection `render_rating_request` can produce. This is what the",
        "context window is checked against, since a rating call that silently",
        "loses its ladders produces uncalibrated ratings and says nothing about",
        "it in the output.",
        "",
        worst_case,
    ]

    for sample_card in plan.cards[:limit]:
        card = cards.get(sample_card.oracle_id)
        if card is None:
            out += ["", f"!! {sample_card.name} ({sample_card.oracle_id}) not in corpus"]
            continue
        out += [
            "",
            "=" * 72,
            f"USER PROMPT -- pass one -- {sample_card.name}  "
            f"[bucket={sample_card.bucket}]",
            "=" * 72,
            render_card_facts(card),
        ]
        # Pass two's real message cannot be shown: it is built from pass one's
        # answer, and no call has been made. Saying so beats printing a
        # plausible-looking prompt the run would never send.
        out.append(
            "  (pass two's user message depends on this card's pass-one tags, "
            "so it does not exist until the call is made -- see the worst case "
            "above.)"
        )
    return "\n".join(out)
