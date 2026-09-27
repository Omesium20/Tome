# Benchmarking & Testing: Metadata Generation

**Status: design only. Nothing in this document has been run yet.** It specifies a benchmark that has to happen *before* `metadata_generator` runs against the full corpus, not the production run itself. See `knowledge-pipeline.md#metadata-generation` for where that stage sits in the pipeline, and `data-model.md#cardmetadata` for the schema this benchmark evaluates against — the taxonomy, `GameStageProfile`, and anchor-card mechanism referenced throughout this document are defined there.

## Why this exists

Metadata generation uses a tiered strategy: a local model generates most of the ~31,830-card Commander-legal pool (`knowledge-pipeline.md#pool-sizes`), escalating to a frontier model only for cards it's likely to get wrong. That's far cheaper than a frontier-only pass, but it rests on two claims that don't hold by default and have to be checked: that a local model's judgment is close enough to frontier's on the easy majority, and that whatever rule flags a card as "hard" actually catches the cases where it isn't. This benchmark is how both get checked before a full run commits to them. (Why tiered generation at all, rather than the simpler frontier-only approach: `lessons-learned.md#metadata-generation-frontier-only-plan--tiered-localfrontier`.)

## The 300-card sample

Stratified by difficulty into three buckets of 100:

| Bucket | Definition | Purpose |
|---|---|---|
| Easy | Obvious role, theme, and game stage — a card no two reasonable raters would disagree on (e.g. Sol Ring: Ramp, obviously earliest-game, obviously high power) | Sets the accuracy floor every tier must clear; also the only bucket anchor cards are drawn from |
| Medium | A typical card: one or two roles, ordinary rules text, no unusual interactions | Represents the bulk of the real corpus |
| Hard | Genuinely ambiguous: multi-role cards, modal or conditional rules text, narrow build-arounds, cards whose power depends heavily on deck context | Where local-vs-frontier disagreement is expected — the boundary this benchmark exists to find |

Sample across colors, mana value, and card type within each bucket too, so it isn't accidentally all one archetype (e.g. all green ramp spells in "easy").

## Ground truth

A maintainer hand-labels all 300 cards against the full `CardMetadata` shape — roles, themes, game stage profile, power rating, strengths, weaknesses, synergy tags — before any model sees them. This hand-labeled set is the answer key model output is scored against; it is never scored against another model's output.

From the easy bucket, pick one anchor card per `roles` enum value and one per `themes` enum value — the clearest embodiment of that tag. These become the calibration examples embedded in the generation prompt (`data-model.md#anchor-cards`). Anchors must exist before any model tier is benchmarked: all three tiers see the same anchors in their prompt, because the benchmark is testing the model, not the anchors.

## Models under test

Three tiers, identical prompt (system block: closed taxonomy + anchors + rubric; user message: rendered card facts) and identical output schema:

| Tier        | Model                                                                                                                       | Role in this benchmark                                                                              |
| ----------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Local, low  | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF`                                                                                       | Cheapest possible per-card cost — tests whether a small local model is viable at all                |
| Local, high | `Qwen/Qwen2.5-Coder-14B-Instruct-GGUF`                                                                                      | The likely production default if it clears the bar — still local, still free per call, more capable |
| Frontier    | Claude (current pinned model — see `model-providers.md` for the client-facing provider interface this is *not* the same as) | The quality ceiling and escalation target; every hard-bucket disagreement is checked against this   |

Both Qwen models are **code-tuned**, not general-purpose instruction models — confirm during the benchmark, rather than assume, that they follow the closed-vocabulary/structured-output contract as reliably as a general chat model would. If they don't, that is a benchmark finding to report, not something to quietly work around with extra parsing.

Running local GGUF models needs an inference runtime (e.g. llama.cpp or Ollama) — a knowledge-pipeline concern local to the benchmark and, eventually, the maintainer-run production job. This is deliberately **not** the client-facing `ModelProvider` interface (`model-providers.md`): that interface exists so a *user* can choose their own deck-generation model. The metadata generator's model backend has no user-facing configuration at all — it's a pipeline implementation detail, run by maintainers, against a corpus every client only ever reads.

## Scoring

Scoring is **human review, not an automated pass alone.** 300 cards per tier is small enough that a maintainer reads every model output directly against the hand-labeled ground truth, rather than trusting only an aggregate metric — a card can score well on F1 while still being a judgment call the model got wrong in a way the metric can't see (e.g. a technically-valid role pick that misses the card's actual best role). The metrics below are computed per card, per tier, to make that review fast and comparable across tiers — they inform the human read, they don't replace it.

- **Role / theme / synergy_tag accuracy** — multi-label, scored as precision/recall/F1 against ground truth, not exact-match.
- **`game_stage` error** — mean absolute error, per stage (`early`/`mid`/`late`), against ground truth.
- **`power_rating` error** — mean absolute error against ground truth.
- **Schema validity rate** — how often raw output actually parses as a valid `CardMetadataOutput` at all: closed-enum values only, numbers in range, no missing fields. A model that ignores the closed vocabulary has failed independently of how good its judgment otherwise is, and this rate is what would catch that.

Aggregate all four **per difficulty bucket**, not only overall — the number that decides anything is the *gap* between tiers on the hard bucket, not the average across all 300.

## Run tracking

Every generation call made during the benchmark is logged, not just its score. This is what makes tier comparison possible on more than accuracy — the escalation rule this benchmark picks has to be justified against latency and cost per card, not just against quality.

**Storage: a standalone SQLite file, not a hand-maintained `.md` file and not either production database.** ~900 runs (300 cards × 3 tiers) with full prompt/response text need to be aggregated and sorted by bucket, tier, cost, and latency — a markdown table can't do that, and a spreadsheet-by-hand doesn't survive re-running the benchmark. It also isn't `KnowledgeSettings` or `LocalSettings` data: this is a maintainer-only tool that runs once (or occasionally, if the benchmark is re-run against a new model release), not part of the app either plane serves, so it gets no Alembic migration and no `KNOWLEDGE_DATABASE_URL`/`LOCAL_DATABASE_URL` entanglement. Lives at `backend/knowledge_pipeline/benchmark/benchmark.db`, created by a small script, gitignored like the Scryfall cache — the artifact worth keeping is the summary this data produces, not the database file itself.

`benchmark_runs` — one row per (card, tier) generation call:

| Field | Notes |
|---|---|
| id | autoincrement PK |
| card_oracle_id | which of the 300 cards |
| card_name | denormalized for readability without a join back to the corpus |
| bucket | `easy` / `medium` / `hard` |
| model_tier | `local_low` / `local_high` / `frontier` |
| model_name | the exact model identifier, e.g. `Qwen/Qwen2.5-Coder-14B-Instruct-GGUF` or the pinned Claude model |
| system_prompt | the cached system block: closed taxonomy + anchors + rubric |
| user_prompt | the rendered card facts sent as the user message |
| raw_response | exactly what the model returned, before parsing |
| schema_valid | whether `raw_response` parses as a valid `CardMetadataOutput` |
| parsed_output | the parsed `CardMetadata` fields as JSON; null if `schema_valid` is false |
| input_tokens / output_tokens | |
| latency_ms | wall-clock time for the call |
| cost_usd | billed API cost; `0` for the local tiers, which have no per-call billing — `latency_ms` is the number that matters for them instead |
| run_at | when the call was made |
| human_verdict | null until reviewed, then the maintainer's read: e.g. `good` / `acceptable` / `wrong` |
| human_notes | free text — what the automated metrics didn't catch, per the *Scoring* section above |
| reviewed_at | null until `human_verdict` is filled in |

One table, not split into a run log and a separate scores table: a benchmark row's technical facts (prompt, response, timing, cost) and its eventual human verdict describe the same event, and splitting them would only add a join for a dataset this small.

## What the results decide

1. **Whether 14B is viable at all.** If its easy-bucket accuracy is materially worse than frontier's, no escalation rule rescues it — local-only isn't viable yet, and the fallback is frontier-only generation (or benchmarking a larger local model instead) until it is.
2. **The escalation rule.** Whichever signal in this benchmark best predicts a 14B-vs-frontier disagreement becomes the production trigger for routing a card to frontier instead of the local model. Candidates to test, not commitments made in advance: oracle text length, modal/choose-one keyword count, hand-labeled multi-role count, low structured-output confidence, or 7B/14B disagreement used as a cheap ambiguity proxy.
3. **The production split.** Once an escalation rule is chosen, applying it to the hand-labeled 300 and extrapolating gives a real estimate of how much of the ~31,830-card corpus routes to frontier — a number to check before committing to a mixed-tier production run, not a plan that only works if escalation happens to stay rare.

## Explicitly out of scope here

- Running metadata generation against the full corpus — that's `metadata_generator` (`backend/knowledge_pipeline/metadata_generator/`) once this benchmark has picked a tiering strategy.
- The Batches API / prompt caching mechanics for a production run — those matter once a model tier is chosen; this benchmark is small enough to run as ordinary synchronous calls, not a batch job.
- Any schema or migration change to either production database (knowledge or local) beyond what `data-model.md#cardmetadata` already documents — `benchmark_runs` above is a standalone benchmark-only store, never migrated alongside either plane.

This document specifies the benchmark. It does not execute it.
