"""The model backend `pipeline.py` calls, one card at a time.

**Not the client-facing `ModelProvider` interface** (`docs/model-providers.md`).
That interface exists so a *user* picks their own deck-generation model; this
one is a maintainer-run pipeline implementation detail with no user-facing
configuration at all — see
`docs/benchmarking-and-testing.md#models-under-test`. `MetadataModelBackend`
is deliberately small and separate so a local-model tier can be added, once
the benchmark picks an escalation rule, without `ModelProvider` growing a
pipeline-only concern or this module depending on client-plane code.

`AnthropicMetadataBackend` is the only implementation used in production —
every card routes through the frontier model until that benchmark runs
(`docs/knowledge-pipeline.md#metadata-generation`). `OllamaMetadataBackend`
(`ollama_backend.py`) is the local tier, built for the benchmark and not yet
routed to by `pipeline.py`.

Two protocols live here, and the second exists because of a measurement
problem rather than a design preference. `MetadataModelBackend` is all
`pipeline.py` wants: a validated blueprint, or an exception. The benchmark
needs strictly more — the raw text, the token counts, the latency, and above
all a *row for the calls that failed*, since "how often does this model
produce valid output at all" is one of the four metrics
(`docs/benchmarking-and-testing.md#scoring`) and `generate` answers it by
throwing the evidence away. `ObservableMetadataBackend` adds that without
touching the production contract.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

import anthropic
from anthropic import Anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)
"""The shape one call is asked to produce.

A parameter rather than a fixed `CardMetadataBlueprint` because generation
takes two passes per card (`prompt.py`): `MetadataPassOne` and then
`MetadataPassTwo`. Both backends enforce the shape from the model class
itself — the Anthropic SDK builds the schema from it, Ollama turns it into a
decoding grammar — so parameterizing the type is all it takes to support a
pass; neither backend knows or cares which one it is serving.
"""


class MetadataGenerationError(Exception):
    """A backend could not produce valid `CardMetadata` for a card.

    Raised after the backend's own internal retries are exhausted. The
    caller's job is to count this as a failed card and move on — one bad
    card must never abort a run over the other ~33,000.
    """


class MetadataModelBackend(Protocol):
    """What `pipeline.py` needs from a model backend. Nothing more."""

    name: str

    def generate(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> OutputT:
        """Return one validated object of shape `output_model`.

        `output_model` is required rather than defaulting to
        `CardMetadataBlueprint`: generation is two passes with two different
        shapes, and a default would let a call site silently ask for the wrong
        one. Every caller says which pass it is making.

        Raises `MetadataGenerationError` if no valid response could be
        produced after this backend's own retries.
        """
        ...


@dataclass(frozen=True)
class GenerationAttempt:
    """One model call, as it happened — including the ways it went wrong.

    Frozen for the same reason `benchmark.store.BenchmarkRun` is: this records
    a call that already completed. The call took that long and returned that
    text; nothing downstream should be able to revise it.

    `parsed` is `None` exactly when the response did not validate against the
    model the call asked for, and `error` says why. That pairing is what the
    benchmark store's `CHECK (schema_valid = 1 OR parsed_output IS NULL)`
    encodes, so keeping the two fields consistent here means a recorded row
    cannot contradict itself.

    It is `parsed` rather than `blueprint` because a call no longer necessarily
    decodes a whole `CardMetadataBlueprint`: production asks for
    `MetadataPassOne` and then `MetadataPassTwo`, and the harness now makes the
    same two calls. The attempt records whichever shape was requested, and the
    caller knows which it asked for.

    `raw_response` is always populated, even on a validation failure — it is
    the whole point. A model that ignores the closed vocabulary has failed in
    a way worth reading, and `docs/benchmarking-and-testing.md#models-under-test`
    is explicit that such a failure "is a benchmark finding to report, not
    something to quietly work around with extra parsing."

    `latency_ms` is wall clock for the whole call. `prompt_eval_ms` and
    `eval_ms` split it into its two phases, and keeping them apart is not
    bookkeeping — a single blended rate is actively misleading here. This
    prompt is ~10,200 tokens against a ~200-token answer, so output tokens
    divided by total time is dominated by prefill and by one-off model load,
    and reads like catastrophically slow inference even when generation is
    perfectly healthy. Phase-blind throughput numbers cost this project a
    wrong conclusion once already; see `docs/lessons-learned.md`.

    Backends that cannot report the split leave both at 0.

    `load_ms` is the third phase, and it is here so the three can be checked
    *against* `latency_ms` rather than merely reported beside it. Wall clock
    that no phase claims is wall clock that was not spent on inference — a
    suspended host, a descheduled process — and `unaccounted_ms` is what makes
    that visible. See `runner.timing_anomaly`.
    """

    raw_response: str
    parsed: BaseModel | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    error: str | None = None
    prompt_eval_ms: int = 0
    eval_ms: int = 0
    load_ms: int = 0

    @property
    def schema_valid(self) -> bool:
        return self.parsed is not None

    @property
    def accounted_ms(self) -> int:
        """Wall clock the backend attributed to a named phase.

        Zero when a backend reports no phase split at all, which is why
        callers must treat a zero here as "unknown" rather than as "nothing
        happened" — `unaccounted_ms` returns `None` in that case.
        """
        return self.load_ms + self.prompt_eval_ms + self.eval_ms

    @property
    def unaccounted_ms(self) -> int | None:
        """Wall clock belonging to no phase. `None` if there is no split.

        A healthy call leaves tens of milliseconds here: HTTP, JSON encoding,
        tokenizer setup. A large value means the clock advanced while nothing
        was computing, and the one thing it never means is "the model was
        slow" — the phase timers would have caught that.

        Clamped at zero. The phase durations and the total come from different
        counters, so a fast call can report a sum a millisecond or two over
        the total without anything being wrong.
        """
        if self.accounted_ms <= 0:
            return None
        return max(0, self.latency_ms - self.accounted_ms)

    @property
    def generation_tokens_per_second(self) -> float | None:
        """Pure decode rate: output tokens over *generation* time only.

        The number to compare against a GPU's expected throughput, because it
        excludes model load and prompt prefill. `None` when the backend didn't
        report the split.
        """
        if not self.output_tokens or self.eval_ms <= 0:
            return None
        return self.output_tokens / (self.eval_ms / 1000)

    @property
    def prefill_tokens_per_second(self) -> float | None:
        """Prompt-processing rate. Separate because it scales differently.

        Prefill is compute-bound over the whole prompt at once; generation is
        memory-bandwidth-bound one token at a time. A model can be fine at one
        and unremarkable at the other, and with a 10,200-token prompt prefill
        is what dominates a cold call.
        """
        if not self.input_tokens or self.prompt_eval_ms <= 0:
            return None
        return self.input_tokens / (self.prompt_eval_ms / 1000)


class ObservableMetadataBackend(Protocol):
    """A backend that can also report what a call cost and what it returned.

    Required by the benchmark harness (`knowledge_pipeline/benchmark/runner.py`);
    `pipeline.py` neither needs nor uses it. A backend satisfying this also
    satisfies `MetadataModelBackend`, so one class serves both the benchmark
    and — once the benchmark picks an escalation rule — production.
    """

    name: str

    def generate(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> OutputT: ...

    def generate_observed(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> GenerationAttempt:
        """Make one call for `output_model` and report everything about it.

        Unlike `generate`, this does **not** raise when the response fails to
        validate — that outcome is a result, returned as an attempt with
        `parsed=None`. It still raises `MetadataGenerationError` when the call
        could not be made at all (the server is unreachable, the model tag
        doesn't exist): an unmade call is not a model's failure to answer, and
        recording it as one would corrupt the schema-validity rate.

        `output_model` is required rather than fixed at
        `CardMetadataBlueprint`, because the harness measures production's two
        calls: `MetadataPassOne`, then `MetadataPassTwo` against the ladders
        pass one's tags selected. It used to be fixed, and the cost of that was
        not abstract — three recorded sessions measured a single-call prompt
        production does not use, which still carried all 66 ladders and none of
        `game_stage`'s calibration (`docs/benchmarking-and-testing.md`).
        """
        ...


class AnthropicMetadataBackend:
    """Calls the Anthropic API with native structured outputs.

    Uses `output_format=<the caller's model>` directly on `messages.stream()`
    rather than hand-building a JSON Schema in `output_config` — the SDK
    builds and enforces the schema from the Pydantic model itself and returns
    an already-validated instance via `get_final_message().parsed_output`
    (Context7 `/anthropics/anthropic-sdk-python`, `structured_outputs_streaming.py`).
    Streamed rather than a single blocking call per `docs/model-providers.md`'s
    Anthropic notes, even though one card's response is short — the same
    client is reused across ~33,000 calls in a long-running process, and a
    stream can't silently hang past a timeout the way a non-streaming call can.

    Extended thinking is deliberately not enabled here. It's the right tool
    for deck construction's many-simultaneous-constraint reasoning
    (`docs/model-providers.md#anthropic-specifics`), but generating one card's
    metadata is a bounded classification-and-scoring task against a schema the
    API already enforces — thinking would multiply cost across the whole
    corpus for a task it isn't needed for. Splitting generation into two
    passes does not change that: each pass is smaller and more bounded than
    the single call it replaced.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 2.0,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "METADATA_MODEL_API_KEY is not set. Add it to backend/.env. "
                "This is a knowledge-plane credential for the metadata "
                "generator, separate from the client's MODEL_API_KEY."
            )
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def generate(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> OutputT:
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                with self._client.messages.stream(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            # Both passes' system blocks are fixed strings,
                            # identical on every call; caching them is most of
                            # the cost saving across a ~33,000-card run. This
                            # is why the per-card anchor ladders travel in the
                            # user message instead (`prompt.py`) — a system
                            # block that varied per card would never hit.
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": prompt}],
                    output_format=output_model,
                ) as stream:
                    message = stream.get_final_message()
            except anthropic.APIError as exc:
                last_error = exc
                logger.warning(
                    "Anthropic call failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(self._retry_base_delay_seconds * attempt)
                continue

            if message.stop_reason == "max_tokens":
                last_error = MetadataGenerationError(
                    f"Response hit max_tokens ({self._max_tokens}) before "
                    "completing — raise METADATA_MODEL_MAX_TOKENS."
                )
                break

            parsed = message.parsed_output
            if parsed is None:
                last_error = MetadataGenerationError(
                    f"No parsed structured output (stop_reason={message.stop_reason!r})."
                )
                continue

            return parsed

        raise MetadataGenerationError(
            f"Failed to generate metadata after {self._max_retries} attempt(s): {last_error}"
        ) from last_error
