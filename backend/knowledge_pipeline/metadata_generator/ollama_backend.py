"""The local-model tier: metadata generation through an Ollama runtime.

Built for the benchmark in `docs/benchmarking-and-testing.md`, which exists to
decide whether a local model can carry most of the ~31,830-card Commander-legal
pool with a frontier model handling only the cards it would get wrong. Nothing
in `pipeline.py` routes here yet — production is still single-tier frontier
until that benchmark says otherwise.

**Not the client-facing `ollama` provider** (`docs/model-providers.md`, which
specifies `ai/providers/ollama_provider.py` for *deck generation*, configured by
a user through `MODEL_PROVIDER`/`MODEL_BASE_URL`). This one is a maintainer-run
pipeline detail with no user-facing configuration at all: every knob is a
constructor argument, the way `AnthropicMetadataBackend` takes its api key and
model. The two interfaces are kept apart deliberately — see `model_backend.py`.

Two things about running a local model here are not obvious and are easy to get
silently wrong:

**The context window.** Pass one's system block renders to ~7,400 characters
(~2,100 tokens) and pass two's to ~1,700 — but pass two's *user* message
carries the calibration ladders its card selected, and that is what has to
fit: its worst case is ~1,400 tokens on top. The retired one-call prompt
needed ~13,900 on its own, which is why `runner.DEFAULT_NUM_CTX` could come
down from 16,384 to 8,192 once the split landed; see `docs/lessons-learned.md`
on prompt size as a budget the vocabulary spends. Ollama's default `num_ctx`
is 4096, and a model handed more than its context simply does not see the
overflow. In a rating call the ladders are the bulk of the prompt, so a
too-small window drops *exactly* the part
that gives `power_rating` its meaning, and every rating would come back
confidently uncalibrated. There is no error for this, which is why `num_ctx`
has no default small enough to be wrong here and why `preflight()` refuses a
window the prompt cannot fit.

**Schema enforcement is not schema adherence.** Passing
`format=CardMetadataBlueprint.model_json_schema()` makes the runtime decode
against a grammar, so malformed output becomes close to impossible by
construction. That is the right call for comparing *judgment* against the
frontier tier, which enforces its schema too — but it means a benchmark run
through this backend cannot answer the separate question
`docs/benchmarking-and-testing.md#models-under-test` raises about whether the
Qwen models honor the closed vocabulary unaided. Report that metric as
enforced, not measured.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import ollama
from pydantic import BaseModel, ValidationError

from .model_backend import GenerationAttempt, MetadataGenerationError, OutputT

logger = logging.getLogger(__name__)

__all__ = [
    "CHARS_PER_TOKEN",
    "GenerationAttempt",
    "OllamaMetadataBackend",
    "OllamaPreflight",
    "estimate_prompt_tokens",
]

# Output ceiling for one card's metadata. The blueprint is a `mechanics`
# restatement, a summary, two short string lists, three numbers and three enum
# lists -- comfortably under 1,000 tokens even for a wordy card, since
# `mechanics` is bounded by the oracle text it restates. The cap exists so a
# model that starts repeating itself fails fast instead of generating until the
# context fills.
DEFAULT_NUM_PREDICT = 1024

# Deterministic by default: the benchmark compares tiers on the same cards, and
# a run that cannot be reproduced cannot be re-reviewed after a prompt change.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 20261001

# Long enough for a 14B model on a partially CPU-offloaded context. Local
# inference is slow in a way an API is not, and a timeout that fires mid-run
# costs the whole card.
DEFAULT_TIMEOUT_SECONDS = 300.0

# Keep the weights resident between cards. Reloading ~9GB per call would
# dominate `latency_ms` and measure the disk instead of the model.
DEFAULT_KEEP_ALIVE = "30m"

# Characters per token for this prompt's mix of English prose and rules text.
# Measured rather than guessed: the system block as it stood at 51 ladders --
# 36,295 characters -- tokenized to 10,245 on qwen2.5-coder, a ratio of 3.54. The
# block has grown since; the ratio is what this constant records. Floored to 3.5 so estimates err
# high. Only ever an estimate -- the model's own `prompt_eval_count` is what the
# runner's truncation guard trusts, and this exists for the checks that have to
# happen before any model is loaded.
CHARS_PER_TOKEN = 3.5


def estimate_prompt_tokens(prompt: str) -> int:
    """Rough token count for a prompt, for preflight checks and logs."""
    return int(len(prompt) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class OllamaPreflight:
    """What a reachability check found, before 600 calls are made against it.

    `fits_context` is the one field that matters most and the only one whose
    failure is invisible at runtime: a `num_ctx` smaller than the rendered
    system prompt produces plausible output from a truncated prompt.

    `model_context_length` is None when the server would not report it, and
    `within_model_limit` then passes rather than blocking a run over a missing
    number. Until it was read from `/api/show` it was None *always* -- see
    `OllamaMetadataBackend._trained_context_length`.
    """

    host: str
    model: str
    model_exists: bool
    num_ctx: int
    model_context_length: int | None
    required_tokens: int
    available_models: tuple[str, ...]

    @property
    def fits_context(self) -> bool:
        return self.num_ctx >= self.required_tokens

    @property
    def within_model_limit(self) -> bool:
        if self.model_context_length is None:
            return True
        return self.num_ctx <= self.model_context_length

    def problems(self) -> list[str]:
        """Everything that should stop a run, in the order worth reading."""
        issues: list[str] = []
        if not self.model_exists:
            known = ", ".join(self.available_models) or "(none pulled)"
            issues.append(
                f"model {self.model!r} is not pulled on {self.host}. "
                f"Available: {known}. Run: ollama pull {self.model}"
            )
        if not self.fits_context:
            issues.append(
                f"num_ctx={self.num_ctx:,} is too small for this prompt, which needs "
                f"about {self.required_tokens:,} tokens. The anchor ladders are the "
                "last section of the system block, so they would be the part "
                "silently dropped -- and every power_rating would be uncalibrated "
                "with nothing in the output to show it. Raise --num-ctx."
            )
        if not self.within_model_limit:
            issues.append(
                f"num_ctx={self.num_ctx:,} exceeds {self.model!r}'s trained context "
                f"length of {self.model_context_length:,}."
            )
        return issues


class OllamaMetadataBackend:
    """Calls a local Ollama runtime with grammar-constrained structured output.

    Satisfies both `MetadataModelBackend` and `ObservableMetadataBackend`:
    `generate_observed` does the work and reports everything, `generate` is the
    production-shaped wrapper that raises instead.

    Deliberately has **no retries**, unlike `AnthropicMetadataBackend`'s three.
    That backend retries because a frontier API has transient failures worth
    riding out. Here the realistic failure is the model producing something the
    schema rejects, and retrying that would quietly convert a measurement into
    a success rate — the benchmark's whole job is to count those. Transport
    failures are not retried either, because a local server that stopped
    answering will not start again within a backoff, and a half-finished run is
    resumable (`benchmark/runner.py`).
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        num_ctx: int,
        host: str = "http://localhost:11434",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = DEFAULT_SEED,
        num_predict: int = DEFAULT_NUM_PREDICT,
        keep_alive: str | float = DEFAULT_KEEP_ALIVE,
        client: Any | None = None,
    ) -> None:
        """`num_ctx` is required and has no default on purpose.

        Every other knob has a sane value; this one depends on how long the
        rendered system prompt is, getting it wrong is invisible, and the cost
        of being wrong is a whole benchmark run of uncalibrated ratings. Make
        the caller state it. `preflight()` then checks the number against the
        prompt it will actually be used with.

        `client` is an injection seam for tests, matching how `pipeline.py`
        takes `backend`/`session`. Production callers pass none.
        """
        if num_ctx < 1:
            raise ValueError(f"num_ctx must be positive, got {num_ctx}")

        self._model = model
        self._num_ctx = num_ctx
        self._host = host
        self._temperature = temperature
        self._seed = seed
        self._num_predict = num_predict
        self._keep_alive: str | float = keep_alive
        self._client = client if client is not None else ollama.Client(
            host=host, timeout=timeout_seconds
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def host(self) -> str:
        return self._host

    @property
    def num_ctx(self) -> int:
        return self._num_ctx

    @property
    def temperature(self) -> float:
        """Exposed so a benchmark session can record what it actually ran.

        A session's whole claim is that its tiers answered the same cards
        under the same decoding settings. Recording the temperature from a
        module constant rather than from the backend that was really
        constructed would make that claim unfalsifiable.
        """
        return self._temperature

    @property
    def seed(self) -> int | None:
        """The decoding seed, for the same reason as `temperature`."""
        return self._seed

    @property
    def num_predict(self) -> int:
        """The output ceiling; a truncated answer is a schema failure."""
        return self._num_predict

    # ---- reachability -------------------------------------------------

    def preflight(self, *, system_prompt: str) -> OllamaPreflight:
        """Check the server, the model tag, and the context window.

        The token estimate is deliberately a **floor check, not a precise
        gate.** The honest tokenizer lives inside the model, which is not loaded
        yet, so this can only divide characters by a ratio — and the accurate
        check already exists elsewhere: the runner compares the first real
        call's `prompt_eval_count`, produced by the model's own tokenizer,
        against `num_ctx`.

        That division of labor decides the ratio. An over-cautious estimate
        here is not free: it rejects a `num_ctx` that would have worked and
        pushes the operator to raise it, and on a 12GB card raising the window
        is what makes the 14B model spill into system RAM — turning a phantom
        problem into a real one that corrupts the latency measurement. So the
        ratio is set near the true one for English rules text (~3.6 chars per
        token, floored to 3.5 for margin) rather than far below it. What this
        still catches with room to spare is the failure that matters: Ollama's
        4096 default, which would silently drop the anchor ladders.
        """
        try:
            listed = self._client.list()
        except Exception as exc:  # noqa: BLE001 - any failure here means unreachable
            raise MetadataGenerationError(
                f"Could not reach the Ollama server at {self._host}: {exc}. "
                "Is `ollama serve` running?"
            ) from exc

        available: list[str] = []
        for entry in getattr(listed, "models", None) or []:
            tag = getattr(entry, "model", None) or getattr(entry, "name", None)
            if not tag:
                continue
            available.append(tag)

        context_length = (
            self._trained_context_length() if self._model in available else None
        )

        return OllamaPreflight(
            host=self._host,
            model=self._model,
            model_exists=self._model in available,
            num_ctx=self._num_ctx,
            model_context_length=context_length,
            # The system block, plus the two things it doesn't contain: the
            # generated response (bounded by num_predict) and the per-card user
            # message (~400 characters of card facts, so 512 tokens is generous).
            required_tokens=estimate_prompt_tokens(system_prompt)
            + self._num_predict
            + 512,
            available_models=tuple(sorted(available)),
        )

    def _trained_context_length(self) -> int | None:
        """The model's own trained context window, or None if unreadable.

        Must come from `/api/show`, not the `/api/list` entry: a list entry's
        `details` carries only `family`, `parameter_size` and
        `quantization_level` -- never `context_length`. Reading it from there
        returned None for every model ever checked, which made
        `within_model_limit` pass vacuously and left `num_ctx` ungated against
        the model's real ceiling. That is half of the guard the `num_ctx` trap
        needs (`docs/benchmarking-and-testing.md`), so it failing open was
        invisible rather than noisy.

        `/api/show` reports it under `model_info` keyed by architecture --
        `qwen2.context_length`, `gemma4.context_length` -- with the
        architecture itself at `general.architecture`. Both are read here, and
        a suffix scan is the fallback for a model whose `general.architecture`
        disagrees with its own key prefix.

        Returns None rather than raising. An unreadable context length must not
        break a preflight whose real job is the `fits_context` check below it;
        `within_model_limit` then passes, as it did before -- but now only when
        the value is genuinely unavailable, not always.
        """
        try:
            shown = self._client.show(self._model)
        except Exception:  # noqa: BLE001 - advisory only; never fail preflight
            return None

        info = getattr(shown, "modelinfo", None) or getattr(shown, "model_info", None)
        if not isinstance(info, dict):
            return None

        architecture = info.get("general.architecture")
        keys = [f"{architecture}.context_length"] if architecture else []
        keys += [k for k in info if k.endswith(".context_length") and k not in keys]
        for key in keys:
            value = info.get(key)
            if isinstance(value, int) and value > 0:
                return value
        return None

    def resident_models(self) -> list[dict[str, Any]]:
        """What is loaded right now, with VRAM vs. total size per model.

        Used by the runner to tell a fully-offloaded model from a partially
        CPU-resident one. For local tiers `cost_usd` is always 0, so
        `latency_ms` is the only number that distinguishes them — and a model
        spilling into system RAM produces latencies that describe the spill
        rather than the model.
        """
        try:
            running = self._client.ps()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read loaded models from %s: %s", self._host, exc)
            return []

        out: list[dict[str, Any]] = []
        for entry in getattr(running, "models", None) or []:
            out.append(
                {
                    "model": getattr(entry, "model", None)
                    or getattr(entry, "name", ""),
                    "size": getattr(entry, "size", None),
                    "size_vram": getattr(entry, "size_vram", None),
                }
            )
        return out

    def unload(self) -> None:
        """Free this model's VRAM now, rather than after `keep_alive` lapses.

        The benchmark runs tiers one after another, and 7B + 14B together do
        not fit on a 12GB card. Letting the first tier linger would make the
        second one load partially onto the CPU and quietly change what
        `latency_ms` is measuring, so the runner unloads between tiers instead
        of trusting eviction to happen in the right order.
        """
        try:
            self._client.chat(
                model=self._model,
                messages=[],
                keep_alive=0,
            )
        except Exception as exc:  # noqa: BLE001 - best effort; never fatal
            logger.debug("Unload request for %s failed: %s", self._model, exc)

    # ---- generation ---------------------------------------------------

    def _chat(
        self, *, system: str, prompt: str, output_model: type[BaseModel]
    ) -> Any:
        """One constrained-decoding call. Raises only when it could not be made.

        Shared by `generate` and `generate_observed` so the two can never
        differ in how they call the model — the schema, the sampling options
        and the error translation all live here once. They differ only in what
        they do with the response.
        """
        try:
            return self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                # The schema is built from the Pydantic model rather than
                # hand-written, the same choice AnthropicMetadataBackend makes
                # with `output_format=`: one definition of the shape, so the
                # grammar and the validator can never drift.
                format=output_model.model_json_schema(),
                options={
                    "num_ctx": self._num_ctx,
                    "temperature": self._temperature,
                    "seed": self._seed,
                    "num_predict": self._num_predict,
                },
                keep_alive=self._keep_alive,
                stream=False,
            )
        except ollama.ResponseError as exc:
            raise MetadataGenerationError(
                f"Ollama rejected the call to {self._model!r}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - transport, timeout, socket
            raise MetadataGenerationError(
                f"Ollama call to {self._model!r} at {self._host} failed: {exc}"
            ) from exc

    def generate_observed(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> GenerationAttempt:
        """One call for `output_model`, reported in full.

        See `ObservableMetadataBackend`. Shares `_chat` with `generate`, so
        there is still exactly one place in this class that calls the model;
        what differs is that a failure to validate comes back as an attempt
        rather than as an exception.
        """
        started = time.perf_counter()
        response = self._chat(
            system=system, prompt=prompt, output_model=output_model
        )

        # Wall clock is the fallback: `total_duration` is Optional in the SDK
        # and absent would otherwise record a 0ms call.
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw = (response.message.content or "") if response.message else ""

        total_duration = getattr(response, "total_duration", None)
        latency_ms = (
            int(total_duration // 1_000_000) if total_duration else elapsed_ms
        )

        parsed, error = self._parse(raw, response, output_model)

        prompt_eval_duration = getattr(response, "prompt_eval_duration", None)
        eval_duration = getattr(response, "eval_duration", None)
        load_duration = getattr(response, "load_duration", None)

        return GenerationAttempt(
            raw_response=raw,
            parsed=parsed,
            input_tokens=int(getattr(response, "prompt_eval_count", None) or 0),
            output_tokens=int(getattr(response, "eval_count", None) or 0),
            latency_ms=latency_ms,
            # Local inference has no per-call billing. The benchmark store
            # requires a number here, and 0 is the honest one -- latency is
            # what distinguishes the local tiers from each other.
            cost_usd=0.0,
            error=error,
            # Kept apart from `latency_ms` so throughput can be judged on the
            # generation phase alone. With a ~10,200-token prompt and a
            # ~200-token answer, a blended rate says far more about prefill
            # than about the model.
            prompt_eval_ms=(
                int(prompt_eval_duration // 1_000_000) if prompt_eval_duration else 0
            ),
            eval_ms=int(eval_duration // 1_000_000) if eval_duration else 0,
            # The third phase, and the one that makes the other two add up.
            # `total_duration` covers load + prefill + generation, so without
            # this a cold call's legitimate model load would look like
            # unexplained wall time to `runner.timing_anomaly`.
            # https://docs.ollama.com/api/chat -- all durations in nanoseconds.
            load_ms=int(load_duration // 1_000_000) if load_duration else 0,
        )

    def _parse(
        self, raw: str, response: Any, output_model: type[OutputT]
    ) -> tuple[OutputT | None, str | None]:
        """Validate the response, returning a reason rather than raising.

        No salvage parsing: no fenced-block stripping, no trailing-comma
        repair, no "find the first {". If constrained decoding did not produce
        valid JSON for the schema it was given, that is the finding.
        """
        if not raw.strip():
            reason = getattr(response, "done_reason", None)
            return None, f"empty response (done_reason={reason!r})"

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            # The usual cause is hitting num_predict mid-object, so name it.
            return None, (
                f"response is not valid JSON ({exc}); "
                f"output_tokens={getattr(response, 'eval_count', None)} against "
                f"num_predict={self._num_predict}"
            )

        try:
            return output_model.model_validate(payload), None
        except ValidationError as exc:
            return None, (
                f"does not match {output_model.__name__}: "
                f"{exc.error_count()} error(s); {exc}"
            )

    def generate(
        self, *, system: str, prompt: str, output_model: type[OutputT]
    ) -> OutputT:
        """`MetadataModelBackend`'s contract: a valid object, or an exception.

        Does not go through `generate_observed`: that one returns a failure to
        validate as a result, and this contract owes the caller either a valid
        object or an exception. Both share `_chat`, so there is still only one
        place that calls the model.
        """
        response = self._chat(system=system, prompt=prompt, output_model=output_model)
        raw = (response.message.content or "") if response.message else ""

        parsed, error = self._parse(raw, response, output_model)
        if parsed is None:
            raise MetadataGenerationError(
                f"{self._model} did not return valid {output_model.__name__}: {error}"
            )
        return parsed
