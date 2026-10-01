# Benchmarking & Testing: Metadata Generation

**Status: the benchmark has not been run.** The one piece of it that exists in code is the run log it writes to (`backend/knowledge_pipeline/benchmark/`, see *Run tracking* below) — the sample, the ground-truth labels, the anchors, and the runs themselves are all still design. This document specifies a benchmark that has to happen *before* `metadata_generator` runs against the full corpus, not the production run itself. See `knowledge-pipeline.md#metadata-generation` for where that stage sits in the pipeline, and `data-model.md#cardmetadata` for the schema this benchmark evaluates against — the taxonomy, `GameStageProfile`, and anchor-card mechanism referenced throughout this document are defined there.

## Why this exists

Metadata generation uses a tiered strategy: a local model generates most of the ~31,830-card Commander-legal pool (`knowledge-pipeline.md#pool-sizes`), escalating to a frontier model only for cards it's likely to get wrong. That's far cheaper than a frontier-only pass, but it rests on two claims that don't hold by default and have to be checked: that a local model's judgment is close enough to frontier's on the easy majority, and that whatever rule flags a card as "hard" actually catches the cases where it isn't. This benchmark is how both get checked before a full run commits to them. (Why tiered generation at all, rather than the simpler frontier-only approach: `lessons-learned.md#metadata-generation-frontier-only-plan--tiered-localfrontier`.)

## The 300-card sample

Stratified by difficulty into three buckets of 100:

| Bucket | Definition | Purpose |
|---|---|---|
| Easy | Obvious role, theme, and game stage — a card no two reasonable raters would disagree on (e.g. Sol Ring: Ramp, obviously earliest-game, obviously a strong staple) | Sets the accuracy floor every tier must clear; also the standard anchor ladder rungs were picked to (see *Ground truth* — 255 rungs can't all come from 100 cards) |
| Medium | A typical card: one or two roles, ordinary rules text, no unusual interactions | Represents the bulk of the real corpus |
| Hard | Genuinely ambiguous: multi-role cards, modal or conditional rules text, narrow build-arounds, cards whose power depends heavily on deck context | Where local-vs-frontier disagreement is expected — the boundary this benchmark exists to find |

Sample across colors, mana value, and card type within each bucket too, so it isn't accidentally all one archetype (e.g. all green ramp spells in "easy").

## Ground truth

A maintainer hand-labels all 300 cards against the full `CardMetadata` shape — roles, themes, game stage profile, power rating, strengths, weaknesses, synergy tags — before any model sees them. This hand-labeled set is the answer key model output is scored against; it is never scored against another model's output.

Anchors are labeled in the same pass. Each `roles` and `themes` enum value needs a **ladder of five** anchor cards — one per band of the 1–10 power scale (1-2, 3-4, 5-6, 7-8, 9-10), each the clearest embodiment of that tag *at that power level*, each carrying a hand-assigned `power_rating` inside its band and a one-line note saying why it sits there. These become the calibration block embedded in the generation prompt (`data-model.md#anchor-cards`, which also holds the band rubric every rung is picked against). Anchors must exist before any model tier is benchmarked: all three tiers see the same anchors in their prompt, because the benchmark is testing the model, not the anchors.

**Be clear about what that cost.** The single-anchor design this replaced meant 53 cards to pick and label (24 roles + 29 themes). The ladder means 5 × 51 = **255** — 51 tags, not 53, because `Theme.MIDRANGE` and `Theme.FLYING` were both dropped rather than anchored (see *A member has to be decidable from the card* in `data-model.md#controlled-vocabulary-roles-themes-synergy_tags`). They were not 255 easy picks: a tag's 1-2 and 9-10 rungs are the two hardest cards in its ladder to choose well, because the obvious example of "Ramp" is a mid-band card and the extremes have to be hunted for. It was worth it for the reason `data-model.md#anchor-cards` gives: one labeled point per tag doesn't calibrate a scale, so the cheaper version of this work would have bought a prompt that looks calibrated and isn't.

**This item is now done** — the 255 rungs are picked, reviewed, and shipped in `metadata_generator/anchors.py`; `data-model.md#anchor-cards` records how they were chosen and `lessons-learned.md` records the two biases found in the selection machinery. It was the gating item for the benchmark, so what remains gating is the 300-card sample's own hand-labeling below.

Note the arithmetic against the sample: 255 rungs cannot all come from a 100-card easy bucket, and in the event most were found by searching the corpus to the bucket's standard rather than drawn from the bucket. The bucket is the *standard* — a card obvious enough that hand-labeling it isn't itself a judgment call — and supplies the rungs it can; the rest are picked to the same standard from outside the 300. A ladder rung is never sourced from the medium or hard bucket, whose whole definition is that reasonable raters disagree about them.

A ladder is **complete or absent**: five rungs or none. `AnchorLadder` enforces that in code, and the reason is a labeling rule as much as a validation rule — a tag with four reviewed rungs and one guess produces ratings indistinguishable from fully anchored ones. If a tag's fifth rung can't be found, leave that tag unanchored and record it; `missing_tags()` is what reports the list.

### Recalibrating the anchors

The ladders are chosen, but the choosing is a tool rather than a one-off pass: `backend/knowledge_pipeline/anchor_bench/`. It exists because the rungs set the meaning of the whole 1–10 scale, so the realistic reasons to revisit them — a revised band rubric, a new `Role`/`Theme` member, a rung that turns out to be a poor ruler — all demand the same machinery, and the first build of it lived in throwaway scripts.

Maintainer-only, and read-only against both databases. It proposes nothing: a candidate pool is a judgment call about Magic, arrives as JSON, and `anchor_bench example` writes a small valid one to work from.

```
python -m knowledge_pipeline.anchor_bench example  -o pool.json
python -m knowledge_pipeline.anchor_bench validate    pool.json
python -m knowledge_pipeline.anchor_bench rank        pool.json -o ranked.json
python -m knowledge_pipeline.anchor_bench page      ranked.json -o bench.html
# review in the page, copy the selection out of its last tab, then:
python -m knowledge_pipeline.anchor_bench export   ranked.json --selection sel.json
```

What each step is for, and the property each one protects:

- **`validate`** checks structure offline, then checks every candidate against the corpus — the `oracle_id` resolves, the name matches, the card is Commander-legal, the oracle text is the corpus's own. A rung is quoted verbatim into the cached system prompt of every generation call, so a misremembered card is not a typo but a permanent invisible error in every rating calibrated against it. Two first picks were Commander-banned when the shipped ladders were built, which no amount of reading would have caught.
- **`rank`** scores the properties that make a *ruler mark* reliable, which are not the properties that make a card good, and it reports how often the top two tie — the honest measure of how little it decided. **Provenance is never an input**; `lessons-learned.md` records the bias that got in the first time.
- **`page`** builds one self-contained HTML file: no server, no network, opens by double-click, and publishable as an artifact for review elsewhere. It shows a rung's candidates side by side (reading them one at a time rewards whichever you read last) and applies nothing automatically.
- **`export`** renders the reviewed selection as the `register(AnchorLadder(...))` calls to paste into `anchors.py`. Only reviewed ladders appear — an unreviewed tag is left out rather than guessed, the same rule `anchors.py` enforces. It refuses a selection naming a card the pool no longer holds, and re-checks the two whole-set rules across the *chosen* cards: no card at two ratings, no card anchoring two ladders.

The pool the shipped ladders came from is deliberately not kept. It would be stale against a later corpus, and the point of the tooling is that a fresh one is cheap.

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

**Storage: a standalone SQLite file, not a hand-maintained `.md` file and not either production database.** ~900 runs (300 cards × 3 tiers) with full prompt/response text need to be aggregated and sorted by bucket, tier, cost, and latency — a markdown table can't do that, and a spreadsheet-by-hand doesn't survive re-running the benchmark. It also isn't `KnowledgeSettings` or `LocalSettings` data: this is a maintainer-only tool that runs once (or occasionally, if the benchmark is re-run against a new model release), not part of the app either plane serves, so it gets no Alembic migration and no `KNOWLEDGE_DATABASE_URL`/`LOCAL_DATABASE_URL` entanglement. Lives at `backend/knowledge_pipeline/benchmark/benchmark.db`, gitignored by the repo root's `*.db` rule like the Scryfall cache — the artifact worth keeping is the summary this data produces, not the database file itself.

**This store is built** — `backend/knowledge_pipeline/benchmark/` — even though the benchmark it records has not been run. `store.py` owns the schema and the whole read/write API (`connect`, `record_run`, `set_verdict`, `iter_runs`, `seed_demo`); `report.py` owns aggregation and rendering; `__main__.py` is the CLI. It uses stdlib `sqlite3` directly rather than SQLAlchemy: one table, one dialect, no migrations, so the `CREATE TABLE` *is* the schema and "delete the file and re-run" is the only upgrade path it needs. It reads no settings at all — the path is a module constant with a `db_path` override for tests.

```
python -m knowledge_pipeline.benchmark init             # create it; print path + row count
python -m knowledge_pipeline.benchmark seed-demo        # synthetic rows (see below)
python -m knowledge_pipeline.benchmark report           # per-bucket summary, tiers side by side
python -m knowledge_pipeline.benchmark report --html out.html   # self-contained offline page
python -m knowledge_pipeline.benchmark review           # score unreviewed runs; needs a terminal
python -m knowledge_pipeline.benchmark set-verdict --id N --verdict good|acceptable|wrong
```

`report` aggregates per `(bucket, tier)` — schema-validity rate, latency mean/p50/p95, cost, tokens, review coverage, verdict split — and leads with the hard-bucket gap between each tier and frontier, because that gap, not the 300-card average, is what decides the escalation rule. The accuracy metrics in *Scoring* above are reported as **unavailable**, not as zero, until hand-labeled ground truth exists; `report.score_accuracy` implements them and lights up once a label set is passed in. Model output is never scored against another model's output, so "no ground truth yet" cannot be quietly satisfied by treating the frontier tier as the answer key. The module docstring also carries raw-SQL recipes for querying `benchmark.db` by hand — the store is a plain SQLite file and reading it should never require this tool.

`seed-demo` fills the store with deterministic synthetic runs — card names `Demo Card NN`, model names prefixed `demo/`, spread across all three buckets and tiers — so the reporting layer can be built and reviewed before 300 cards are hand-labeled and three model runtimes are stood up. It refuses outright if the table already holds anything that isn't a demo row: synthetic numbers mixed into measured ones would corrupt every aggregate the conclusions below rest on.

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

Storage details worth knowing before writing against it: `bucket`, `model_tier`, and `human_verdict` are enforced by SQLite `CHECK` constraints, not by convention — a typo'd tier would silently create a fourth one and skew every per-tier aggregate. `parsed_output` is JSON TEXT (null when `schema_valid` is false, and a `CHECK` enforces that pairing); `iter_runs` returns raw `sqlite3.Row` objects, so a reader does its own `json.loads`. `schema_valid` is INTEGER `0`/`1`; `run_at`/`reviewed_at` are ISO-8601 UTC text, which sorts chronologically as text. `set_verdict` stamps `reviewed_at` in the same statement as the verdict, so "reviewed" has exactly one answer in the data.

## What the results decide

1. **Whether 14B is viable at all.** If its easy-bucket accuracy is materially worse than frontier's, no escalation rule rescues it — local-only isn't viable yet, and the fallback is frontier-only generation (or benchmarking a larger local model instead) until it is.
2. **The escalation rule.** Whichever signal in this benchmark best predicts a 14B-vs-frontier disagreement becomes the production trigger for routing a card to frontier instead of the local model. Candidates to test, not commitments made in advance: oracle text length, modal/choose-one keyword count, hand-labeled multi-role count, low structured-output confidence, or 7B/14B disagreement used as a cheap ambiguity proxy.
3. **The production split.** Once an escalation rule is chosen, applying it to the hand-labeled 300 and extrapolating gives a real estimate of how much of the ~31,830-card corpus routes to frontier — a number to check before committing to a mixed-tier production run, not a plan that only works if escalation happens to stay rare.

## Explicitly out of scope here

- Running metadata generation against the full corpus — that's `metadata_generator` (`backend/knowledge_pipeline/metadata_generator/`) once this benchmark has picked a tiering strategy.
- The Batches API / prompt caching mechanics for a production run — those matter once a model tier is chosen; this benchmark is small enough to run as ordinary synchronous calls, not a batch job.
- Any schema or migration change to either production database (knowledge or local) beyond what `data-model.md#cardmetadata` already documents — `benchmark_runs` above is a standalone benchmark-only store, never migrated alongside either plane.

This document specifies the benchmark. It does not execute it.
