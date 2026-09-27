"""The model backend `pipeline.py` calls, one card at a time.

**Not the client-facing `ModelProvider` interface** (`docs/model-providers.md`).
That interface exists so a *user* picks their own deck-generation model; this
one is a maintainer-run pipeline implementation detail with no user-facing
configuration at all — see
`docs/benchmarking-and-testing.md#models-under-test`. `MetadataModelBackend`
is deliberately small and separate so a local-model tier can be added, once
the benchmark picks an escalation rule, without `ModelProvider` growing a
pipeline-only concern or this module depending on client-plane code.

`AnthropicMetadataBackend` is the only implementation today — every card
routes through the frontier model until that benchmark runs
(`docs/knowledge-pipeline.md#metadata-generation`).
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

import anthropic
from anthropic import Anthropic

from .schema import CardMetadataBlueprint

logger = logging.getLogger(__name__)


class MetadataGenerationError(Exception):
    """A backend could not produce valid `CardMetadata` for a card.

    Raised after the backend's own internal retries are exhausted. The
    caller's job is to count this as a failed card and move on — one bad
    card must never abort a run over the other ~33,000.
    """


class MetadataModelBackend(Protocol):
    """What `pipeline.py` needs from a model backend. Nothing more."""

    name: str

    def generate(self, *, system: str, prompt: str) -> CardMetadataBlueprint:
        """Return validated metadata for one card.

        Raises `MetadataGenerationError` if no valid response could be
        produced after this backend's own retries.
        """
        ...


class AnthropicMetadataBackend:
    """Calls the Anthropic API with native structured outputs.

    Uses `output_format=CardMetadataBlueprint` directly on `messages.stream()`
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
    corpus for a task it isn't needed for.
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

    def generate(self, *, system: str, prompt: str) -> CardMetadataBlueprint:
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
                            # The taxonomy + anchors block is identical on
                            # every call; caching it is most of the cost
                            # saving across a ~33,000-card run.
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": prompt}],
                    output_format=CardMetadataBlueprint,
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
