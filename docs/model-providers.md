# Model Providers

Deck generation runs on the user's machine, against a model the user chooses — a frontier API or something running locally on their own hardware. This document is the source of truth for the abstraction that makes that possible: the interface, the supported backends, what a model must be able to do to be usable at all, and how it's configured.

Architectural context: `architecture.md#model-provider-abstraction`. This file holds the detail.

---

## Why an interface rather than an SDK

Before this change the deck pipeline called the Anthropic SDK directly, which was fine when we assumed one hosted deployment and one API key. Once generation moved to the client, the model became the user's choice, and that choice spans a wide range:

- Someone with a GPU wants to run a local model and pay nothing.
- Someone with an Anthropic key wants the best decks the app can produce.
- Someone in a company wants to point at an internal OpenAI-compatible gateway.

These differ in latency, quality, cost, and privacy — but not in what the deck pipeline needs from them. Every one of them is "given this prompt, return a deck list as structured data." That is the whole surface, and it is the whole interface.

**Only `deck_pipeline/generator.py` talks to a provider, and it only talks to the protocol.** Retrieval, prompt construction, validation, and the repair loop are provider-agnostic code paths shared by every backend. Adding a provider means adding one file under `ai/providers/` and one registry entry — never a change to the pipeline.

---

## The interface

`backend/ai/provider.py` defines the protocol and the factory. Deliberately small — the pipeline only ever needs one call:

```python
from typing import Protocol

class ModelProvider(Protocol):
    name: str                      # "anthropic", "openai", "ollama"
    model: str                     # the configured model identifier

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict,              # JSON Schema the response must satisfy
        max_tokens: int,
    ) -> dict:
        """Return a parsed object matching `schema`.

        Raises ProviderError on transport/auth failure, and
        ProviderSchemaError when the model returned something unparseable
        after the provider's own internal retries.
        """

    def health(self) -> ProviderHealth:
        """Reachability, auth, and whether `model` actually exists.

        Called at startup and by the settings UI so an unreachable Ollama
        or a bad API key surfaces before a user waits out a generation.
        """
```

Two shapes to note:

- **Structured, not text.** The pipeline never parses prose. Every provider is responsible for returning an object matching the supplied JSON Schema, using whatever native mechanism it has. That's what keeps the validator's input the same regardless of backend.
- **`health()` is part of the contract.** A local-model setup has failure modes a hosted API doesn't — the server isn't running, the model was never pulled, the GPU is busy. Making reachability a first-class call lets the UI say "Ollama isn't running" instead of failing twelve minutes into a deck build.

`ProviderError` and `ProviderSchemaError` are the only exceptions the pipeline handles. Vendor SDK exceptions are translated inside each provider and never escape it — otherwise the pipeline would grow `except anthropic.RateLimitError` branches and the abstraction would be worthless.

---

## Supported backends

| Provider | `MODEL_PROVIDER` | Runs | Structured output mechanism |
|---|---|---|---|
| Anthropic Claude | `anthropic` | Hosted API | Native structured outputs — `output_config={"format": {...}}` on `messages.create()` |
| OpenAI-compatible | `openai` | Hosted API or self-hosted | JSON-schema response format; covers OpenAI, OpenRouter, vLLM, LM Studio, and most gateways |
| Ollama | `ollama` | Local | Ollama's `format` parameter with a JSON Schema |

`openai` is deliberately one provider rather than several. Any server speaking the OpenAI chat-completions shape is reached by pointing `MODEL_BASE_URL` at it, which is how vLLM, LM Studio, OpenRouter, and corporate gateways are all supported without a file each.

### Anthropic specifics

Uses the official `anthropic` Python SDK (already a dependency). Notes that matter for this provider, from the current API:

- Default model is `claude-opus-5`. Model IDs are complete as written — never append a date suffix.
- Structured output goes through `output_config={"format": {...}}`. The older top-level `output_format` parameter is deprecated; don't reach for it.
- Use adaptive thinking (`thinking={"type": "adaptive"}`) for deck construction — it's a genuinely multi-constraint reasoning task. `budget_tokens` is rejected with a 400 on current models; depth is controlled with `output_config={"effort": ...}` instead.
- Assistant prefill is not available on current models, so the "start the JSON for it" trick some older code uses is not an option here. Structured outputs replace it.
- Deck construction produces long output over a large candidate list — stream the request and take `.get_final_message()` rather than risking an HTTP timeout on a non-streaming call.

---

## Capability floor

Not every model can do this job, and a user pointing Tome at a model too small for it should get a clear message rather than twelve failed repair rounds. A usable model must:

| Requirement | Why | Rough floor |
|---|---|---|
| Emit valid JSON against a supplied schema | The pipeline consumes structured data only | Reliable JSON mode, or a schema-constrained decoder |
| Hold the candidate list in context | ~100–200 candidates of condensed card knowledge plus rules and preferences | **~32K context**, comfortably |
| Follow a long multi-constraint instruction | "100 cards, singleton, this color identity, this curve, these roles" is many simultaneous constraints | The practical filter; small models fail here first |

In practice that puts the floor around a competent mid-size instruct model. Below it, decks still come out *legal* — the validator guarantees that — but they come out generic, because the model quietly ignores the strategic half of the prompt. That is the failure mode to warn users about: not crashes, blandness.

**Correctness never depends on the model.** Every deck is validated locally by the same deterministic Python regardless of provider, so a weak model produces a worse deck or more repair rounds, never an illegal one. This is the whole reason the division of responsibility in `architecture.md` is enforced in code — it is what makes "bring any model" a safe offer instead of a reckless one.

### The repair loop and weak models

`generator.py` feeds validation errors back to the model for a bounded number of retries. Two rules keep this from becoming pathological on local models:

- **Bound the retries and degrade honestly.** After the limit, return the best deck produced along with the specific unresolved violations. Never loop indefinitely, and never present an invalid deck as valid.
- **Repair prompts name the violation, not the fix.** "These three cards are outside the commander's color identity" — the model re-decides. Handing it the correction makes the backend the deck builder.

---

## Configuration

All via `backend/.env`, read through `config.py` like every other setting. Never `os.environ`.

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_PROVIDER` | `anthropic` | `anthropic` · `openai` · `ollama` |
| `MODEL_NAME` | provider-specific | The model identifier to use |
| `MODEL_API_KEY` | — | Required for hosted providers; **ignored by `ollama`** |
| `MODEL_BASE_URL` | provider default | Required for `openai` against a non-OpenAI host, and for `ollama` if not on the default port |
| `MODEL_MAX_TOKENS` | `16000` | Output ceiling for deck construction |
| `MODEL_TIMEOUT_SECONDS` | `600` | Local models on CPU are slow; the default is generous on purpose |

```bash
# Frontier — best deck quality
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-opus-5
MODEL_API_KEY=sk-ant-...

# Local — no API key, no network, no cost
MODEL_PROVIDER=ollama
MODEL_NAME=<a model you have pulled>
MODEL_BASE_URL=http://localhost:11434

# Any OpenAI-compatible gateway (OpenRouter, vLLM, LM Studio, internal proxy)
MODEL_PROVIDER=openai
MODEL_NAME=<model>
MODEL_BASE_URL=https://your-gateway/v1
MODEL_API_KEY=...
```

**`MODEL_API_KEY` is no longer unconditionally required.** It was, when Anthropic was the only option. A local-model user needs no key at all, so validation is per-provider: the factory raises only if the *selected* provider needs a credential it doesn't have. Startup and the settings UI both call `health()`, so a missing key or an unreachable Ollama is reported immediately with the variable to fix.

---

## Testing

The protocol exists partly so tests never touch a network. `tests/` uses a `FakeProvider` returning canned structured responses, which lets the interesting cases — a deck that fails validation and gets repaired, a model that returns unparseable output, a provider that times out — be tested deterministically and offline.

Per-provider tests stub the transport rather than the SDK surface, so they catch a real mistake: a provider that returns valid JSON which doesn't match the schema the pipeline asked for.
