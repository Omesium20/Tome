# Benchmarking & Testing: Metadata Generation

**Status: the harness is built and runnable; the full benchmark has not been run, and two things still gate its conclusions.** What exists in code: the run log (`backend/knowledge_pipeline/benchmark/store.py`), the reporting layer (`report.py`), the anchors (all 66 ladders), the 300-card sample selector (`sample.py`), the execution loop (`runner.py`), and a local-model backend (`metadata_generator/ollama_backend.py`) wiring the two Qwen tiers through Ollama. What does not: hand-labeled ground truth, and the frontier tier. So the suite produces real schema-validity and latency numbers today, and reports accuracy as *unavailable* and the hard-bucket-vs-frontier gap as *uncomputable* rather than as zero.

One further caveat applies to everything below: **the sample's difficulty buckets are an unreviewed heuristic**, not the human judgment the *300-card sample* section describes. The sample file records that in a `reviewed: false` field, and every consumer surfaces it, because the hard-bucket gap is the number this benchmark exists to produce.

This document specifies a benchmark that has to happen *before* `metadata_generator` runs against the full corpus, not the production run itself. See `knowledge-pipeline.md#metadata-generation` for where that stage sits in the pipeline, and `data-model.md#cardmetadata` for the schema this benchmark evaluates against — the taxonomy, `GameStageProfile`, and anchor-card mechanism referenced throughout this document are defined there.

## Why this exists

Metadata generation uses a tiered strategy: a local model generates most of the ~31,830-card Commander-legal pool (`knowledge-pipeline.md#pool-sizes`), escalating to a frontier model only for cards it's likely to get wrong. That's far cheaper than a frontier-only pass, but it rests on two claims that don't hold by default and have to be checked: that a local model's judgment is close enough to frontier's on the easy majority, and that whatever rule flags a card as "hard" actually catches the cases where it isn't. This benchmark is how both get checked before a full run commits to them. (Why tiered generation at all, rather than the simpler frontier-only approach: `lessons-learned.md#metadata-generation-frontier-only-plan--tiered-localfrontier`.)

## The 300-card sample

Stratified by difficulty into three buckets of 100:

| Bucket | Definition | Purpose |
|---|---|---|
| Easy | Obvious role, theme, and game stage — a card no two reasonable raters would disagree on (e.g. Sol Ring: Ramp, obviously earliest-game, obviously a strong staple) | Sets the accuracy floor every tier must clear; also the standard anchor ladder rungs were picked to (see *Ground truth* — 330 rungs can't all come from 100 cards) |
| Medium | A typical card: one or two roles, ordinary rules text, no unusual interactions | Represents the bulk of the real corpus |
| Hard | Genuinely ambiguous: multi-role cards, modal or conditional rules text, narrow build-arounds, cards whose power depends heavily on deck context | Where local-vs-frontier disagreement is expected — the boundary this benchmark exists to find |

Sample across colors, mana value, and card type within each bucket too, so it isn't accidentally all one archetype (e.g. all green ramp spells in "easy").

### Selecting it

`sample.py` builds the sample and the `sample` subcommand drives it. Read-only against the corpus; writes one JSON file, and that file is **tracked in git**, not gitignored like `benchmark.db`'s neighbors: it's small, it's meant to be read and argued with by a maintainer (`signals`, `reviewed`), and pinning the exact 300 cards a run was scored against matters more than the convenience of regenerating it blind from the seed. `sample.DEFAULT_SAMPLE_PATH` (`backend/knowledge_pipeline/benchmark/sample.json`) is the default for `--out`, `--validate`, and the sample `run` reads — bare flags use it, so the usual flow needs no path at all:

```
python -m knowledge_pipeline.benchmark sample --out --seed 20261001   # writes sample.json beside the package
python -m knowledge_pipeline.benchmark sample --validate              # checks that same file
```

**The selector does not make the judgment the table above describes.** Difficulty as defined there is a judgment about Magic; the selector ranks the Commander-legal pool on properties that *correlate* with it — modal/conditional marker count, rules-text length, distinct ability lines, keyword count — cuts the ranking into terciles, and draws from each. Hence `reviewed: false`, which nothing in the selector ever sets true. The per-card signals that decided each bucket are kept in the file, so a later human pass has something to argue with instead of starting over.

Two selection rules are enforced in code rather than left to care:

- **Anchor cards are excluded.** All 330 rungs are quoted verbatim into every system prompt, so a model scored on one is being scored on recall of its own prompt. The rule excludes 330 cards, leaving 31,500 eligible of the 31,830 Commander-legal pool. The exclusion list grows whenever the registry does, so **a recalibration has to be re-checked against the shipped `sample.json`** — a card that was legal to sample can become an anchor later. The 2026-10 growth from 255 to 330 rungs was checked and introduced no collision, so the shipped sample stands.
- **Each bucket is drawn stratified** across colour identity, mana-value band and base type — and the strata are visited in **randomized, not sorted, order**. That second detail is load-bearing rather than fussy: the first implementation walked strata in sorted order and stopped once the quota filled, which produced 300 cards containing no white or blue at all. `lessons-learned.md` records it.

`--validate` checks structure offline and then every card against the corpus — the `oracle_id` resolves, the name matches, the card is Commander-legal. Same two-stage shape, and the same reason, as the anchor check in `metadata_generator/verify_anchors.py`.

## Ground truth

A maintainer hand-labels all 300 cards against the full `CardMetadata` shape — roles, themes, game stage profile, power rating, strengths, weaknesses, synergy tags — before any model sees them. This hand-labeled set is the answer key model output is scored against; it is never scored against another model's output.

Anchors are labeled in the same pass. Each `roles` and `themes` enum value needs a **ladder of five** anchor cards — one per band of the 1–10 power scale (1-2, 3-4, 5-6, 7-8, 9-10), each the clearest embodiment of that tag *at that power level*, each carrying a hand-assigned `power_rating` inside its band and a one-line note saying why it sits there. These become the calibration block embedded in the generation prompt (`data-model.md#anchor-cards`, which also holds the band rubric every rung is picked against). Anchors must exist before any model tier is benchmarked: all three tiers see the same anchors in their prompt, because the benchmark is testing the model, not the anchors.

**Be clear about what that cost.** The single-anchor design this replaced meant 53 cards to pick and label (24 roles + 29 themes). The ladder meant 5 × 51 = **255** at first — 51 tags, not 53, because `Theme.MIDRANGE` and `Theme.FLYING` were both dropped rather than anchored (see *A member has to be decidable from the card* in `data-model.md#controlled-vocabulary-roles-themes-synergy_tags`). They were not 255 easy picks: a tag's 1-2 and 9-10 rungs are the two hardest cards in its ladder to choose well, because the obvious example of "Ramp" is a mid-band card and the extremes have to be hunted for. It was worth it for the reason `data-model.md#anchor-cards` gives: one labeled point per tag doesn't calibrate a scale, so the cheaper version of this work would have bought a prompt that looks calibrated and isn't.

**And the cost recurs with the vocabulary.** The `Role`/`Theme` revision took the registry to 66 members, so the ladder set is now 5 × 66 = **330**. That is the number to budget against a future vocabulary change: adding one member is five cards, and splitting one role into four is twenty. It is also why the review step is the first thing to buckle under its own weight — see the note in `data-model.md#anchor-cards` on which of the 330 rungs a human actually chose.

**This item is now done** — the 330 rungs are picked, reviewed, and shipped in `metadata_generator/anchors.py`; `data-model.md#anchor-cards` records how they were chosen and `lessons-learned.md` records the two biases found in the selection machinery and what changed about the review step at 330.

Note the arithmetic against the sample: 330 rungs cannot all come from a 100-card easy bucket, and in the event most were found by searching the corpus to the bucket's standard rather than drawn from the bucket. The bucket is the *standard* — a card obvious enough that hand-labeling it isn't itself a judgment call — and supplies the rungs it can; the rest are picked to the same standard from outside the 300. A ladder rung is never sourced from the medium or hard bucket, whose whole definition is that reasonable raters disagree about them.

A ladder is **complete or absent**: five rungs or none. `AnchorLadder` enforces that in code, and the reason is a labeling rule as much as a validation rule — a tag with four reviewed rungs and one guess produces ratings indistinguishable from fully anchored ones. If a tag's fifth rung can't be found, leave that tag unanchored and record it; `missing_tags()` is what reports the list.

### Recalibrating the anchors

The ladders are chosen, and `anchor-recalibration.md` is the procedure for revising a rung or rebuilding the set after a rubric change or a vocabulary revision. The short version: a model proposes *and* picks the rungs against the band rubric, writing `register(AnchorLadder(...))` calls straight into `anchors.py`, and two gates stand on the result.

```
python -m knowledge_pipeline.metadata_generator.verify_anchors   # the cards are real
pytest tests/test_anchors.py                                     # the set is coherent
```

- **`verify_anchors`** is the only part of this that needs the corpus. It reports any rung whose name is absent, matches more than one card, or is not Commander-legal. A rung is quoted verbatim into the cached system prompt of every generation call, so a misremembered card is not a typo but a permanent invisible error in every rating calibrated against it — and two first picks were Commander-banned when the shipped ladders were built, which no amount of reading would have caught.
- **`test_anchors.py`** is offline and holds the whole-set rules: five rungs per ladder, every rating inside its band, no card at two different ratings, no card anchoring two ladders, and `test_every_role_and_theme_ships_with_a_ladder` as the completeness scoreboard.

This replaced a larger tool — a candidate-pool format, a triage scorer, and an HTML page a human picked each rung on. The human pass it was built around stopped happening the first time the vocabulary grew, so the scaffolding went with it; `lessons-learned.md` records both halves of that.

## Models under test

Three tiers, identical prompt (system block: closed taxonomy + anchors + rubric; user message: rendered card facts) and identical output schema:

> **The harness measures production's two passes** (`knowledge-pipeline.md#two-calls-per-card`): `runner.py` calls `build_pass_one_system_prompt()` and then `build_pass_two_system_prompt()` with the ladders pass one's tags selected, and records one row per `(card, tier, pass)`. It made a single combined call for its first three sessions, and what those measured is a prompt production does not use — see *Measuring the two-pass path* for the conversion, and for what S-001 to S-003 are and are not evidence for.

| Tier        | Model                                                                                                                       | Role in this benchmark                                                                              |
| ----------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Local, low  | `Qwen/Qwen2.5-7B-Instruct-GGUF`                                                                                             | Cheapest possible per-card cost — tests whether a small local model is viable at all                |
| Local, high | `Qwen/Qwen2.5-14B-Instruct-GGUF`                                                                                            | The likely production default if it clears the bar — still local, still free per call, more capable |
| Frontier    | Claude (current pinned model — see `model-providers.md` for the client-facing provider interface this is *not* the same as) | The quality ceiling and escalation target; every hard-bucket disagreement is checked against this   |

**These were the `qwen2.5-coder` variants through session S-001, and are the general instruction-tuned models from S-002 on.** The coder line is Qwen2.5 continued-pretrained on roughly 5.5T tokens of code, and that capacity is bought *from* general and world knowledge — which is the entirety of this task. Reviewing S-001 card by card turned up the symptom directly: `Echo Storm` was themed `Storm`, a mechanic it does not have, because the word appears in its name, and neither tier knew why `Elvish Reclaimer` or `Fist of Suns` see play. Those are missing domain knowledge, not missing reasoning. The replacements hold parameter count, quantization and trained context constant (see the tag table below), so the base model is the only variable between S-001 and the next session.

Confirm during the benchmark, rather than assume, that whichever line is running follows the closed-vocabulary/structured-output contract as reliably as a general chat model would. If it doesn't, that is a benchmark finding to report, not something to quietly work around with extra parsing.

> **This question is currently unanswered, by construction.** The local tiers run with grammar-constrained decoding (`format=CardMetadataBlueprint.model_json_schema()`), chosen so their *judgment* is comparable against the frontier tier, which also enforces its schema. Constrained decoding makes malformed output nearly impossible, so `schema_valid` reads ~100% for the local tiers whatever they would have done unaided. Read it as **enforced, not measured**, and see *Scoring* below. Answering the adherence question needs a separate free-JSON pass into its own store; that pass has not been run.

Running local GGUF models needs an inference runtime (e.g. llama.cpp or Ollama) — a knowledge-pipeline concern local to the benchmark and, eventually, the maintainer-run production job. This is deliberately **not** the client-facing `ModelProvider` interface (`model-providers.md`): that interface exists so a *user* can choose their own deck-generation model. The metadata generator's model backend has no user-facing configuration at all — it's a pipeline implementation detail, run by maintainers, against a corpus every client only ever reads. Concretely: `metadata_generator/ollama_backend.py` implements it, every knob is a constructor argument, and **no `KnowledgeSettings` field or env var configures it** — the CLI passes flags over module-constant defaults. When the benchmark picks a tier and production actually routes locally, *that* change adds the setting.

### Model identifiers: GGUF repo vs. Ollama tag

The tier table names Hugging Face GGUF repositories. Ollama addresses the same weights by registry tag, and `model_name` in the store records **what was really called**:

| Tier | This document's identifier | Ollama tag actually run | Size | Sessions |
|---|---|---|---|---|
| `local_low` | `Qwen/Qwen2.5-7B-Instruct-GGUF` | `qwen2.5:7b` | 4.68 GB, Q4_K_M | S-002 → |
| `local_high` | `Qwen/Qwen2.5-14B-Instruct-GGUF` | `hf.co/bartowski/Qwen2.5-14B-Instruct-GGUF:Q4_K_M` | 9.0 GB, Q4_K_M | S-002 → |
| `local_low` | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` | `qwen2.5-coder:7b-instruct` | 4.68 GB, Q4_K_M | S-001 only |
| `local_high` | `Qwen/Qwen2.5-Coder-14B-Instruct-GGUF` | `qwen2.5-coder:14b-instruct` | 8.99 GB, Q4_K_M | S-001 only |

Two things this table is making explicit. **The divergence is recorded rather than smoothed over** — `model_name` is meant to answer "what produced this row", not "what did the design document intend", which is also why a session's models are stamped on `benchmark_sessions` and never inferred at report time. And **the two current tags come from different registries**: the 7B is the Ollama-registry build, the 14B is pulled straight from the Hugging Face repo via Ollama's `hf.co/` addressing. They look nothing alike and both are correct. The registry's own `qwen2.5:14b` is the same Q4_K_M weights at the same 8.99 GB, so that choice is interchangeable — but the tag that ran is the tag that gets recorded, so don't "tidy" it to the shorter name afterwards.

Because the per-tier identity is stored per session, a report spanning S-001 and later sessions is comparing two different base models — which is the point of the swap, but it means **win rates must not be pooled across that boundary**. Compare sessions, not rows.

### Four operational facts to know before running it

All measured on real hardware while building the harness. Every one of them produces confident, plausible, wrong numbers rather than errors — and the second did so to this harness's own author twice, in opposite directions, before the measurement was taken properly.

**1. The context window must be set explicitly, and it must fit the *larger of the two passes*.** This fact got smaller when the harness went two-pass, but it did not go away — it moved. The retired single-call prompt carried all 66 ladders and rendered to ~49,400 characters ≈ 14,100 tokens, which is why `DEFAULT_NUM_CTX` climbed from 12,288 to 16,384 and why the ladders kept falling off the end of the window. Now pass one is ~7,400 chars ≈ 2,126 tokens and pass two's *system* block is a mere 1,705 chars — but **pass two's user message is where the ladders are**, and it grows with the card, because `anchors.ladders_for` selects them from the tags pass one just assigned. Its worst case — a pass one that filled `roles` and `themes` to `MAX_TAGS_PER_CARD` — is what the window has to fit, and that is what the preflight checks: a synthetic widest-case `MetadataPassOne` put through the real `render_rating_request`, rather than a multiplier on the system block. **Ollama's default `num_ctx` is 4096.** A model given more than its context does not see the overflow, so a too-small window drops precisely the part that gives `power_rating` its meaning, and every rating comes back uncalibrated with nothing in the output to show it. The harness guards this twice:

- `preflight()` refuses, before loading the model, a `num_ctx` that cannot fit the rendered prompt plus `num_predict` plus the card facts. This is a floor check against gross misconfiguration — it has to estimate, because the real tokenizer is inside the model.
- after the first real call **of each pass**, the runner compares the model's own `prompt_eval_count` against `num_ctx` and **aborts the whole run** if it is within 5% of the window. A prompt that fills its window is indistinguishable from one clipped to fit. This is the accurate check, and the reason the estimate above is allowed to be approximate. Once per pass rather than once per tier, because pass one fitting says nothing about pass two.

There is a third check on the *other* side of the window: `preflight()` also refuses a `num_ctx` larger than the model's own trained context length, read from `/api/show`'s `model_info` (`qwen2.context_length`). Worth knowing that this one **did nothing until it was fixed** — it read `context_length` off the `/api/list` entry's `details`, which has no such field, so the value was None for every model and the comparison was skipped every time. See `docs/lessons-learned.md`.

The default is `--num-ctx 8192`: more than double what either pass can ask for, and deliberately **not** the models' full 32,768, because the KV cache is VRAM. That is not a theoretical concern on a 12 GB card — see fact 3. Coming down from 16,384 took the 14B from 11.5 GB of 12.4 GB resident to comfortable, and its measured decode rate from 2.7 tok/s (contended, S-002) to **37 tok/s**.

**2. Judge throughput on the generation phase alone, never on a blended rate.** For local tiers `cost_usd` is 0 by definition, so `latency_ms` is the *entire* basis on which they are compared — a tier that quietly ran on the CPU does not make the benchmark slow, it makes it measure something else. But the obvious way to check that is wrong, and it was wrong here first.

A call has three phases with very different costs: one-off **model load**, **prefill** of the prompt, and **generation** of the answer. A pass-one call is ~2,100 prompt tokens against a ~200-token answer and a pass-two call ~1,400 against ~80, so output tokens divided by total call time is dominated by the first two — and the two passes are dominated by them to *different* degrees, which is why the report's **Per pass** table exists and why nothing merges their rates. Measured on the 12 GB AMD RX 7700 XT this was developed against:

| Tier | Model load | Prefill (cold) | **Generation** | Output ÷ total time |
|---|---|---|---|---|
| 7B @ 12288 ctx | 5.7 s | 1,918 tok/s | **59 tok/s** | 14.7 "tok/s" |
| 14B @ 12288 ctx | 9.3 s | 291 tok/s | **30 tok/s** | 4.1 "tok/s" |

That last column is the trap. 4.1 tok/s reads as CPU inference, and an earlier version of this harness concluded exactly that. It was wrong: the Ollama server log showed `offloaded 49/49 layers to GPU`, with 8,148 MiB of weights + 2,304 MiB of KV cache + 123 MiB of compute buffers ≈ **10.3 GB resident in 12 GB of VRAM, with ~1.7 GB spare.** The 14B's real decode rate is 30 tok/s — healthy, and roughly half the 7B's, which is what doubling the parameters predicts. **Both models fit comfortably and both run fully on the GPU.**

**But the decode rate is not a fixed property of the card, and that is the second half of the lesson.** A later run of the same 14B at the same `--num-ctx` decoded at **4 tok/s**, and the cause was neither the model nor the metric: Windows' own counters showed `12,046 MB of 12,272 MB` dedicated VRAM in use with `1,037 MB` spilled to shared memory, because desktop applications held ~1.4 GB (Wallpaper Engine 415 MB, Chrome 261 MB, Steam 218 MB, VS Code 139 MB, Discord 99 MB, Obsidian 52 MB). llama.cpp's load-time fit check had seen 12,126 MiB free and planned 1,538 MiB of headroom; the WDDM driver paged ~1 GB out *afterwards*. Decode reads every weight per token, so ~1 GB arriving over PCIe costs roughly 7.5x. Prefill was unaffected, being compute-bound rather than bandwidth-bound.

Three rates for one unchanged configuration, all measured:

| Condition | Decode | Per card |
|---|---|---|
| Uncontended | 30 tok/s | ~7 s |
| Light contention | 20 tok/s | ~10 s |
| ~1.4 GB held by desktop apps | **4 tok/s** | **~52 s** |

So: **close GPU-heavy applications before a 14B run**, and treat a decode-rate warning as a question about the whole machine, not just about Ollama. `/api/ps` reported `11.1GB of 11.1GB in VRAM` throughout — which is exactly why it is quoted as colour and never as the detector.

So the harness warns on **decode rate only** (`eval_count / eval_duration`, below 10 tok/s), reports prefill separately, and quotes `/api/ps` only as colour on a problem already established by measurement — never as the detector. `/api/ps` is not reliable in either direction on AMD/Windows, where it can report `size_vram == size` for a model partly in GTT (shared system memory over PCIe).

**If the warning does fire,** the authoritative check is the Ollama server log (`%LOCALAPPDATA%\Ollama\server.log` on Windows): look for `offloaded N/M layers to GPU`. If N &lt; M, free VRAM or lower `--num-ctx` — and since the two-pass split there is real room to do so: the default is 8,192 against a worst case near 3,500, where the one-call prompt could not go below ~11,000 without truncating. `OLLAMA_KV_CACHE_TYPE=q8_0` (server restart) is the lever after that, at some cost to output quality which must then be recorded alongside the results. If all layers are offloaded, the weights are fine and something else is throttling.

**3. Runtime depends on prompt-prefix caching, and that is worth knowing before you plan a run.** The system block is byte-identical across all 300 cards, so llama.cpp reuses its KV prefix and prefill collapses from 35 s to ~0.2 s after the first call. Measured steady state:

| Tier | Cold call | Steady state | 300 cards |
|---|---|---|---|
| 7B | ~15 s | **~3.4 s/card** | **~17 min** |
| 14B, uncontended | ~51 s | ~7-10 s/card | ~35-52 min |
| 14B, VRAM contended | ~51 s | ~52 s/card | **~4.3 hours** |

The 7B figure is measured over 15 consecutive cards (`p50 3,422 ms`, `p95 4,008 ms`). The 14B's spread is not caching behaviour at all — prefill was cached in every case, with `cached n_tokens = 10143` of 10,226 — but the VRAM contention described in fact 2. **Plan a 14B run around what else is using the GPU, not around the cache.**

The runner's unrecorded warm-up call is what pays the cold prefill, so card #1 is not an outlier in the latency percentiles. Two consequences: the whole two-tier run is under an hour rather than the several hours a cold-prefill-per-card arithmetic would suggest; and anything that invalidates the prefix — editing the rubric or the anchors mid-run — silently restores the slow path. This is a measured effect of the runtime, not a configured one; Ollama has no `cache_control` equivalent to ask for it.

**4. `latency_ms` is wall clock, so a sleeping host corrupts it silently.** Ollama's `total_duration` — and `eval_duration` with it — measures elapsed time, not CPU time. If the machine suspends mid-call, the suspension is billed to inference. This happened during the first real run: one call recorded **49,866,300 ms (13.8 hours) for 220 tokens**, with llama.cpp itself printing `0.00 tokens per second`. The damage is specifically hard to notice, because the median is robust to it and the mean is not:

| | p50 | p95 | mean |
|---|---|---|---|
| With the stalled row | 51,112 ms | **15,004,868 ms** | **3,365,675 ms** |
| Without it | 50,986 ms | 59,857 ms | 44,201 ms |

No client timeout catches this: the whole machine sleeps, so the HTTP read timer stops too and resumes with the socket still alive. `runner.timing_anomaly` is the guard, and it discards such a call rather than recording it:

- **decode rate below 0.5 tok/s** — a stall *during* generation, which inflates `eval_duration` too and collapses the ratio. The floor sits far below the 10 tok/s residency warning on purpose: that one asks "is this on the GPU?" and a wrong answer is a warning someone reads, while this one asks "is this a measurement?" and a wrong answer deletes a model's good output. Pure CPU inference on a 14B still manages 2-5 tok/s.
- **more than 60 s of wall clock belonging to no phase** — a stall *outside* generation, where every phase timer looks healthy and only the total is wrong. This is why `load_duration` is captured: a cold call's legitimate 9.3 s model load would otherwise read as unexplained time.

Both checks are one-sided, rejecting only impossibly *slow* calls. An impossibly fast one is what prefix caching is supposed to produce.

A discarded call's model output was sound, and discarding it loses nothing: an unrecorded `(card, tier)` cell is exactly what `run --resume` re-runs, so **a discard is a retry deferred to the next run** — and the session it belonged to comes back `partial` rather than `complete` in the meantime, so the hole is visible rather than merely absent. The guard also runs before the residency check, so a stalled warm-up produces no residency verdict rather than a false CPU accusation.

The *discard* decision can only be made at write time, but the judgment behind it is no longer unrepeatable: `benchmark_runs` now stores `load_ms`, `prompt_eval_ms` and `eval_ms` alongside `latency_ms`, so decode rate — the one throughput number a ~10k-token prefill does not dominate — is recoverable from the store months later. Treat all three as *unknown* when they are 0, which is what a backend reporting no phase split writes.

## Paired sessions

**A run is one session: one card set, every tier, in serial succession.** That is the structure the whole harness now hangs off, and it exists because of a specific failure rather than as tidiness.

The first version of the harness had no concept of a run. `benchmark_runs` carried a `run_at` timestamp and nothing tying a sitting together, each tier iterated "the sample, minus whatever that tier already had, capped at `--limit`", and `--limit` applied *after* the resume filter. Three sittings later the store held 45 rows in which the 7B had covered 15 cards, the 14B 30, and **every single row came from the easy bucket** — because `sample.json` is ordered easy→medium→hard and `--limit` took the first N. No comparison could be computed from it, and nothing in the harness could notice. Those rows were discarded rather than kept; `docs/lessons-learned.md` records the whole thing.

What a session fixes, and how:

| Property | Mechanism |
|---|---|
| Every tier answers **the same cards** | The card set is drawn once, written to `benchmark_sessions.card_ids` **before the first call**, and iterated by every tier |
| A **bucket is always chosen** | `--bucket` is required and repeatable; there is no default and no flag that stands in for one |
| Cards are drawn **across strata**, not off the front | `sample.draw_session_cards` reuses the 300-card selection's round-robin-over-shuffled-strata draw, inside one bucket |
| Resume cannot **drift** | The skip set is scoped to `(session, card, tier)`, so a card run in some *other* session is still run in this one |
| The comparison is **provably like-for-like** | A session closes `complete` only when every cell of its grid has a row; anything less is `partial`, and the report refuses to present a win rate over it without saying so |
| Two sessions are **comparable or visibly not** | The session records `system_prompt_sha256`, `num_ctx`, `temperature`, `model_seed`, the tier order and the model tags — so a prompt edit between sittings is detectable rather than silent |

### Running it

One tier at a time, never two: the two models do not co-reside on a 12 GB card, and the runner unloads each tier before the next loads.

```
python -m knowledge_pipeline.benchmark run --bucket hard --cards 3 --dry-run
python -m knowledge_pipeline.benchmark run --bucket hard --cards 30
python -m knowledge_pipeline.benchmark run --bucket hard --cards 30 --tier local_low
python -m knowledge_pipeline.benchmark run --resume S-003
```

- **`--bucket` is required and repeatable.** The bucket is what the session measures. The full 300 is `--cards 100 --bucket easy --bucket medium --bucket hard`.
- **`--cards N` (default 30) is per bucket**, and it replaced `--limit`. 30 is about half an hour of 14B inference per tier; it is also only ~±18 points of certainty on a win rate (see *Pairwise scoring*), so raise it before concluding anything from a near-even split.
- **`--draw-seed` (default 20261012) decides which cards.** Holding it fixed is what makes a re-run after a prompt change comparable; changing it is what guards against a 30-card set that flatters one model by luck. Do both, in that order.
- `--dry-run` prints the resolved config, **the cards it would draw**, and the prompts, and makes **no model calls** and opens **no session**. This diverges from `metadata_generator --dry-run`, which still spends real API calls; there is no billing to warn about locally, so the useful smoke test is the opposite one — confirm the draw and the config before committing hours of inference.
- **Resumable per session.** `record_run` commits per row, so `Ctrl-C` costs nothing; `run --resume S-NNN` fills only that session's missing cells, taking its buckets, card set and prompt from the stored row. It **refuses** if the system prompt has changed since the session started, or if the sample no longer holds the session's cards — finishing it either way would compare one tier's answers against another tier's answers to a different question.
- `--tier` restricts which tiers run (repeatable); `--tier-order descending` flips which goes first, since the second tier always runs on a machine the first one just warmed, and that confound is better tested than assumed away.
- A sample that isn't 100-per-bucket **warns and runs**; one with a duplicate `oracle_id`, an unknown bucket name or an anchor card is **refused**. The size rule governs whether per-bucket conclusions hold, not whether a run can execute; the others corrupt the rows themselves. Asking for more cards than a bucket holds is also refused — silently running fewer would make two sessions look comparable when they are not.
- The runner **refuses to write into a store holding `seed-demo` rows.** `seed_demo` already refuses the reverse; mixing synthetic into measured corrupts every aggregate, and the `demo/` prefix that makes a synthetic row obvious in a listing does nothing for a mean.
- A call that could not be *made* (server down, model missing) is never recorded as a schema failure — that would fold an outage into the schema-validity rate — and stops the tier instead, leaving the session `aborted` and resumable.

The frontier tier is **not wired**: `--tier frontier` is refused rather than silently recording nothing. Adding it means giving `AnthropicMetadataBackend` the `generate_observed` method `ObservableMetadataBackend` defines. Nothing in the harness assumes two tiers, so frontier becomes a third column rather than a rewrite.

## Scoring

Scoring is **human review, not an automated pass alone.** 300 cards per tier is small enough that a maintainer reads every model output directly against the hand-labeled ground truth, rather than trusting only an aggregate metric — a card can score well on F1 while still being a judgment call the model got wrong in a way the metric can't see (e.g. a technically-valid role pick that misses the card's actual best role). The metrics below are computed per card, per tier, to make that review fast and comparable across tiers — they inform the human read, they don't replace it.

- **Role / theme / synergy_tag accuracy** — multi-label, scored as precision/recall/F1 against ground truth, not exact-match.
- **`game_stage` error** — mean absolute error, per stage (`early`/`mid`/`late`), against ground truth.
- **`power_rating` error** — mean absolute error against ground truth.
- **Schema validity rate** — how often raw output actually parses as a valid `CardMetadataBlueprint` at all: closed-enum values only, numbers in range, no missing fields. A model that ignores the closed vocabulary has failed independently of how good its judgment otherwise is, and this rate is what would catch that.

  **For the local tiers as currently run, this metric is enforced rather than measured.** Grammar-constrained decoding makes invalid output nearly impossible, so a ~100% rate says the constraint worked, not that the model would have complied without it. What the rate still catches under constraint is a response truncated by `num_predict` or an empty one — real failures, just rarer and different in kind. Treat the *code-tuned-model adherence* question in *Models under test* as open, and do not read a high rate here as having answered it.

Aggregate all four **per difficulty bucket**, not only overall — the number that decides anything is the *gap* between tiers on the hard bucket, not the average across all 300.

### Castability: the one accuracy metric that needs no ground truth

`game_stage.early` asks when a card first does something, so it should track mana value **downward** — a card you cannot cast until turn seven is not doing much on turn two. That makes it the rare metadata field with a free, objective check, and `report.score_timing` computes it on every run:

- **`castability r`** — Pearson correlation of mana value against `early`. Strongly negative is the target.
- **Curve shape distribution** — what share of profiles rise, fall, peak, dip or stay flat.
- **The excepted cards, by name**, listed under the table rather than silently dropped.

**It is computed over plain-cost cards only, and that scoping is the metric.** For 19.7% of the Commander-legal corpus mana value is not the cost to function (`data-model.md#early-is-scored-on-cost-to-function-not-mana-value`): Convoke really does make a five-drop castable early, so a weak correlation there is the *right* answer and pooling those cards in would mark right answers wrong. `cost_profile.facts_from_prompt` classifies each row from its own stored `user_prompt`, so the reporting layer still reaches neither a database nor a model.

**Report it per tier, never pooled.** On S-001 the pooled figure was −0.03, which reads as "no relationship". Per tier it was −0.37 for the 7B and **+0.41 for the 14B** — one tier correct, one inverted, very nearly cancelling. An aggregate across groups that differ in *sign* is not a weak version of the per-group number; see `lessons-learned.md`.

Baselines to beat, from S-001: plain-cost `r` of −0.03 pooled, and 50% of all profiles non-decreasing against 17% falling and 1-in-60 flat.

### Pairwise scoring: which tier won this card

Everything above needs ground truth, and ground truth does not exist yet. **The judgment that is sound without it is a relative one**, so a session's primary score is a per-card pick: for each card, both tiers' answers side by side, and one of `local_low` / `local_high` / `tie` / `both_bad`.

- **`tie` and `both_bad` are deliberately distinct.** Both mean "no winner", but equally good and equally useless drive opposite decisions about whether local generation is viable at all.
- **Picking happens in the HTML report, and is copied back out.** The session tab renders one row per card with both raw responses expandable, a radio group, and a notes box; "Collect picks" serializes them to JSON, and `pair-verdict --from picks.json` records them. The page never writes to the database — the reporting layer is read-only by design, and one copy-paste step is a better trade than a browser with a write path to measured evidence.
- **The picks are non-blind**: the model names are shown. That keeps the expectation bias that the 14B is the better model, which is a known cost of the choice rather than an oversight.
- **A pick points at the run ids it was formed against.** A re-run of the same card writes new rows, so a stale verdict is visibly stale rather than silently reattached to answers nobody judged.
- **The per-run verdict (`good`/`acceptable`/`wrong`) stays alongside it.** They answer different questions: one is "was this answer any good", the other is "which of these two was better". A model can win every pair in a session and still be wrong on most cards.

**Read the interval, not the percentage.** 30 paired cards place a win rate within roughly ±18 points at 95% confidence, so `17-13` is noise and only a lopsided tally is a finding. The report prints a Wilson score interval beside every rate for exactly this reason — Wilson rather than the normal approximation, which is wrong in this regime and can produce bounds outside [0, 1].

**And weigh it against the clock.** The 14B is ~9× slower per card than the 7B on this hardware (30.1 s vs 3.4 s measured), which over the ~31,830 Commander-legal cards is ~266 hours against ~30. The report prints that projection next to the win rate: a tier that wins by a nose and costs nine extra days of inference has not obviously won.

**What agreement is, and is not.** The comparison table also scores how closely the tiers' answers line up — Jaccard on roles/themes/synergy tags, absolute deltas on `power_rating` and `game_stage` — and sorts the cards by disagreement, worst first. This is **not accuracy**: two models agreeing says only that, and on a hard card they are often wrong together. Its job is to order the review queue, because reading the eight cards where the tiers differ is worth more than reading all 30 alphabetically. A card where one tier produced nothing parseable scores as *unknown* rather than 0%, and sorts above everything — an unmeasurable comparison is more interesting than a measured disagreement, not less.

## Run tracking

Every generation call made during the benchmark is logged, not just its score. This is what makes tier comparison possible on more than accuracy — the escalation rule this benchmark picks has to be justified against latency and cost per card, not just against quality.

**Storage: a standalone SQLite file, not a hand-maintained `.md` file and not either production database.** Hundreds of runs with full prompt/response text need to be aggregated and sorted by session, bucket, tier, cost, and latency — a markdown table can't do that, and a spreadsheet-by-hand doesn't survive re-running the benchmark. It also isn't `KnowledgeSettings` or `LocalSettings` data: this is a maintainer-only tool that runs once (or occasionally, if the benchmark is re-run against a new model release), not part of the app either plane serves, so it gets no Alembic migration and no `KNOWLEDGE_DATABASE_URL`/`LOCAL_DATABASE_URL` entanglement. Lives at `backend/knowledge_pipeline/benchmark/benchmark.db`.

**Tracked in git, not gitignored.** The repo root's `*.db` rule would otherwise catch it like the Scryfall cache, but `.gitignore` carves out an explicit exception for this one file: a benchmark conclusion rests on the measured evidence in this table, not just the aggregate `report` prints, so losing the file to a blanket ignore rule would hide the evidence rather than discard a rebuildable cache. The 300-card `sample.json` next to it is tracked for the same reason.

The file-size worry that used to accompany this is gone: the ~48KB system prompt is stored **once per session** rather than denormalized into every row, which is what had cost 1.8 MB of opaque binary for 45 rows. A full 300-card, two-tier session is now a few megabytes. SQLite is still a binary format, so each commit replacing it is an opaque diff with no history compression — a deliberate trade for having the evidence reviewable without anyone needing to re-run the benchmark themselves.

**This store is built** — `backend/knowledge_pipeline/benchmark/`. `store.py` owns the schema and the whole read/write API (`connect`, `create_session`, `finish_session`, `record_run`, `set_verdict`, `record_pair`, `iter_*`, `seed_demo`); `report.py` owns aggregation and rendering; `sample.py` picks the 300 cards and draws a session's subset; `runner.py` executes a session against the tiers; `__main__.py` is the CLI. It uses stdlib `sqlite3` directly rather than SQLAlchemy: three tables, one dialect, no migrations, so the `CREATE TABLE` *is* the schema and "delete the file and re-run" is the only upgrade path it needs. It reads no settings at all — the path is a module constant with a `db_path` override for tests.

That upgrade path is enforced rather than assumed. Every statement in the DDL is `IF NOT EXISTS`, so applying it to a store from an older schema would leave the old tables in place and fail much later on a column that does not exist — so the store stamps `PRAGMA user_version` and **refuses to open a file from another version**, naming the file to delete. There is no migration from the pre-session schema and deliberately so: its runs record no session, so there is nothing to say which tiers were compared against which cards.

**`store.py` and `report.py` depend on neither a database URL nor a model runtime, and that boundary is worth keeping.** Only `sample.py` and `runner.py` import sqlalchemy or the corpus; only `runner.py` can reach a model. So inspecting or reviewing a run needs no `KNOWLEDGE_DATABASE_URL`, no Ollama, and no vendor SDK — and a reporting layer that cannot reach a model cannot accidentally score one model's output against another's.

```
python -m knowledge_pipeline.benchmark init             # create it; print path + counts
python -m knowledge_pipeline.benchmark seed-demo        # synthetic sessions (see below)
python -m knowledge_pipeline.benchmark sample --out       # pick the 300 cards -> sample.json
python -m knowledge_pipeline.benchmark sample --validate   # check that file
python -m knowledge_pipeline.benchmark run --bucket hard --cards 30   # one paired session
python -m knowledge_pipeline.benchmark run --resume S-003             # finish a partial one
python -m knowledge_pipeline.benchmark sessions        # what each measured; is it paired?
python -m knowledge_pipeline.benchmark report          # sessions first, pooled views after
python -m knowledge_pipeline.benchmark report --html out.html   # tabbed, self-contained page
python -m knowledge_pipeline.benchmark report --session S-003   # scope it to one session
python -m knowledge_pipeline.benchmark review           # per-run verdicts; needs a terminal
python -m knowledge_pipeline.benchmark set-verdict --id N --verdict good|acceptable|wrong
python -m knowledge_pipeline.benchmark pair-verdict --from picks.json   # the per-card picks
```

`report`'s HTML output is **tabbed by session**: an Overview tab holds everything cross-session, and each session gets its own tab with its provenance strip (cards, buckets, tiers and model tags, `num_ctx`/temperature/seed, prompt fingerprint, status), its win rate with the interval, the per-card comparison table, and its own per-tier aggregates. The Overview still aggregates per `(bucket, tier)` — schema-validity rate, latency mean/p50/p95, cost, tokens, review coverage, verdict split — and leads with the hard-bucket gap between each tier and frontier, because that gap, not the 300-card average, is what decides the escalation rule. **It also says out loud when the sessions it pools do not share one `system_prompt_sha256`**, because a prompt edit between sittings changes what every rating means and the prompt is a 48KB blob nobody diffs by eye. The accuracy metrics in *Scoring* above are reported as **unavailable**, not as zero, until hand-labeled ground truth exists; `report.score_accuracy` implements them and lights up once a label set is passed in. Model output is never scored against another model's output, so "no ground truth yet" cannot be quietly satisfied by treating the frontier tier as the answer key. The module docstring also carries raw-SQL recipes for querying `benchmark.db` by hand — the store is a plain SQLite file and reading it should never require this tool.

`seed-demo` fills the store with deterministic synthetic data — card names `Demo Card NN`, model names prefixed `demo/`, session ids prefixed `S-D`, **one internally-paired session per bucket** with a few picks recorded — so the reporting layer and the tab bar can be built and reviewed before 300 cards are hand-labeled and three model runtimes are stood up. It refuses outright if the table already holds anything that isn't a demo row: synthetic numbers mixed into measured ones would corrupt every aggregate the conclusions below rest on. **`run` refuses the mirror case** — writing measured rows into a store that holds demo rows — because that is the same corruption approached from the other side, and only one direction was originally guarded. Either way the fix is `--db` a fresh path, or delete the file.

`benchmark_sessions` — one row per invocation of `run`. Every field here is something that must not vary between the tiers inside it, which is why it is a session column rather than repeated onto every run:

| Field | Notes |
|---|---|
| id | `S-001`-style, allocated by the store. Typed by hand into `--session`/`--resume`, and it names the tab in the HTML report — a UUID would be correct and unusable. Demo sessions use `S-D` and never advance this counter |
| label | optional short name, shown on the tab and in `sessions` |
| started_at / finished_at | ISO-8601 UTC |
| status | `running` / `complete` / `partial` / `aborted`. `complete` requires every cell of the grid; see below |
| buckets | JSON list, in the order given to `--bucket` |
| cards_per_bucket | what `--cards` was |
| **card_ids** | JSON list — **the frozen card set**, written before the first call |
| tiers | JSON list, in execution order |
| models | JSON `{tier: model identifier}` — what actually answered, not what the design intended |
| sample_path / sample_seed / draw_seed | the provenance of the draw, enough to reproduce it |
| num_ctx / temperature / model_seed | the decoding knobs the whole session ran under |
| system_prompts | JSON `{pass: system block}` — pass one's taxonomy + `game_stage` calibration, pass two's rating rubric. **Stored once here**, and `iter_runs` joins each row the block *its own pass* was sent |
| **system_prompts_sha256** | half the comparability key across sessions, over the whole map so a change to either block moves it |
| **output_schemas_sha256** | the other half. Under constrained decoding the schema *is* the grammar, so two sessions with an identical prompt and a changed schema are not comparable — see *Measuring the two-pass path* for the time that happened |
| notes | free text |

`benchmark_runs` — one row per (card, tier, **pass**) generation call, belonging to exactly one session. Two rows per card per tier, because production makes two calls per card:

| Field | Notes |
|---|---|
| id | autoincrement PK |
| session_id | FK to `benchmark_sessions`, `NOT NULL`. There is no unsessioned run |
| card_oracle_id | which card |
| card_name | denormalized for readability without a join back to the corpus |
| bucket | `easy` / `medium` / `hard` |
| model_tier | `local_low` / `local_high` / `frontier` |
| model_name | the exact model identifier, e.g. `qwen2.5:7b` or the pinned Claude model |
| **pass_name** | `one` or `two`. Part of the row's identity, not a flag on it: the two calls send different prompts, decode against different grammars, and can fail independently |
| user_prompt | pass one: the rendered card facts. Pass two: the rating request, carrying **the ladders pass one's tags selected** — which is what a reviewer needs in order to judge a rating |
| raw_response | exactly what the model returned, before parsing |
| schema_valid | whether `raw_response` parses as a valid `MetadataPassOne` / `MetadataPassTwo` |
| parsed_output | that pass's fields as JSON; null if `schema_valid` is false. Seven fields for pass one, two for pass two — never a whole blueprint |
| input_tokens / output_tokens | |
| latency_ms | wall-clock time for the call |
| load_ms / prompt_eval_ms / eval_ms | the three phases that make up `latency_ms`. All 0 means *unknown*, never *instant* — see fact 4 |
| cost_usd | billed API cost; `0` for the local tiers, which have no per-call billing — `latency_ms` is the number that matters for them instead |
| run_at | when the call was made |
| human_verdict | null until reviewed, then the maintainer's read of *this answer*: `good` / `acceptable` / `wrong` |
| human_notes | free text — what the automated metrics didn't catch, per the *Scoring* section above |
| reviewed_at | null until `human_verdict` is filled in |

There is **no `system_prompt` column**: the blocks live on the session and `iter_runs` joins the right one back in under that name (`json_extract(s.system_prompts, '$.' || r.pass_name)`), so readers are unaffected and the tracked file stays small.

A card's *answer* is both rows, and `report.merge_passes` is the one place that rebuilds it: pass two's keys over pass one's, timings and tokens summed, `schema_valid` the conjunction, both prompts and both responses concatenated under labelled headers, and the row id pointed at the rating call so an id read off the page resolves to the row a pick was mostly made on. Everything in `report.py` but the **Per pass** table consumes merged rows; that table reads the raw ones, because blending a pass-one call's throughput with a pass-two call's describes neither.

`benchmark_pairs` — one row per card that has been decided, i.e. the per-card pick:

| Field | Notes |
|---|---|
| id | autoincrement PK |
| session_id / card_oracle_id | `UNIQUE` together: re-deciding a card replaces the earlier pick rather than adding a second opinion to the tally |
| card_name | denormalized, as above |
| runs | JSON `{tier: run_id}` — the exact outputs the pick was formed against. The id is the cell's **pass-two** row, the one carrying `power_rating`; its existence also implies pass one succeeded |
| winner | a tier name, or `tie` / `both_bad` |
| notes | why |
| decided_at | ISO-8601 UTC |

**Why three tables and not one.** The original single-table argument was right about what it covered: a run's technical facts and *its own* verdict describe the same event, so splitting those two would add a join for nothing. Neither of the other two is that split. A session describes a *set* of events, and repeating its fields onto every row would give them N places to disagree — "did these two tiers really see the same prompt?" would stop being a question the data can answer. A pair verdict is a judgment about *two* runs; written onto the runs themselves it would land on both with nothing keeping the copies consistent, and "which of these was better" is not a property either one has alone.

Storage details worth knowing before writing against it: `bucket`, `model_tier`, `human_verdict`, `status` and `winner` are enforced by SQLite `CHECK` constraints, not by convention — a typo'd tier would silently create a fourth one and skew every per-tier aggregate. `UNIQUE (session_id, card_oracle_id, model_tier)` on the runs is what makes a session's pairing countable: a duplicate would be two rows for one cell and a silent double weight in that tier's mean. Foreign keys are enabled (`PRAGMA foreign_keys = ON`, which SQLite does not do by default) and cascade on delete, which is how `seed_demo` replaces demo data. `parsed_output` is JSON TEXT (null when `schema_valid` is false, and a `CHECK` enforces that pairing); `iter_runs` returns raw `sqlite3.Row` objects, so a reader does its own `json.loads`, and the session's JSON columns come back as strings too. `schema_valid` is INTEGER `0`/`1`; every timestamp is ISO-8601 UTC text, which sorts chronologically as text. `set_verdict` stamps `reviewed_at` in the same statement as the verdict, so "reviewed" has exactly one answer in the data.

## What the results decide

1. **Whether 14B is viable at all.** If its easy-bucket accuracy is materially worse than frontier's, no escalation rule rescues it — local-only isn't viable yet, and the fallback is frontier-only generation (or benchmarking a larger local model instead) until it is.
2. **The escalation rule.** Whichever signal in this benchmark best predicts a 14B-vs-frontier disagreement becomes the production trigger for routing a card to frontier instead of the local model. Candidates to test, not commitments made in advance: oracle text length, modal/choose-one keyword count, hand-labeled multi-role count, low structured-output confidence, or 7B/14B disagreement used as a cheap ambiguity proxy.
3. **The production split.** Once an escalation rule is chosen, applying it to the hand-labeled 300 and extrapolating gives a real estimate of how much of the ~31,830-card corpus routes to frontier — a number to check before committing to a mixed-tier production run, not a plan that only works if escalation happens to stay rare.

### What the harness cannot decide yet

Stated plainly, because a populated report reads more conclusive than it is. Three things are missing, and each blocks a specific conclusion above:

| Missing | Blocks |
|---|---|
| Hand-labeled ground truth for the 300 | All of *Scoring*'s accuracy metrics — reported **unavailable**, never zero. Without it, (1) cannot be answered in absolute terms at all; the per-card picks answer the *relative* question only. |
| The frontier tier | The hard-bucket gap, which is the number (1) and (2) turn on. `report` says *uncomputable* rather than showing a gap against nothing. |
| Human review of the difficulty buckets | Makes every per-bucket split provisional, including the hard-bucket gap once frontier exists. The sample's `reviewed: false` tracks this. |
| An unconstrained free-JSON run | Means `schema_valid` cannot discriminate the tiers — it is ~100% for both by construction — so a session's verdict rests on human judgment and latency alone. This is the single highest-value addition for telling these two models apart; see *Explicitly out of scope*. |

What the harness *does* deliver today is measured schema-validity and latency per tier on real hardware, every prompt and response recorded for review, a paired per-card comparison whose win rate is reported with its uncertainty, and the gaps above reduced from engineering work to data entry.

One caveat on the pairwise score itself: temperature is 0 and the seed is fixed, so a session is deterministic and `n = 1` per card. That is the right default — a result that cannot be reproduced cannot be re-reviewed after a prompt change — but it means a `tie` says the models agreed *once*, not that they are equally stable. A repeats-with-varied-seeds pass over a subset would measure that; it is not built.

## Measuring the two-pass path

**Done.** The harness makes production's two calls per card, as of schema version 3. What follows is what the conversion changed, what it cost, and what the three sessions recorded before it are and are not evidence for.

### What the harness now does

Per card, per tier, in order: pass one against `build_pass_one_system_prompt()` with the rendered card facts; `derive_themes` merged into its answer exactly as `pipeline.generate_metadata` does it; then pass two against `build_pass_two_system_prompt()` with `render_rating_request`, whose user message carries only the ladders pass one's tags selected. Both calls go through one `_record_call`, so the discard rule, the context check and the row shape cannot drift apart between them.

A pass one that fails its grammar gets **no pass two** — the rating call takes pass one's output as its input, so there is nothing to send. That leaves a real hole in the grid, the session comes back `partial`, and the report declines to quote a win rate over it. This is the honest outcome: a card with no `power_rating` cannot be compared on one.

### What it cost the store

| Assumption | What replaced it |
|---|---|
| One system prompt per session, fingerprinted | `benchmark_sessions.system_prompts` — a JSON map keyed by pass — with `system_prompts_sha256` over the whole map. Both blocks are still byte-identical across every card of a session, because the per-card ladders ride in pass two's *user* message precisely to keep that true, so one fingerprint still stands for the session. |
| One row per `(card, tier)` | `UNIQUE (session_id, card_oracle_id, model_tier, pass_name)`, and `recorded_cells` / `missing_cells` key on the triple. Without the pass in the key, a card whose rating call was interrupted would read as finished and resume would skip the call that is actually missing. |
| `parsed_output` is a `CardMetadataBlueprint` | A pass-one row holds seven fields, a pass-two row two. `report.merge_passes` is where they become a card's answer again — and the only place that knows they were ever apart. |
| Throughput read off a row | `report.summarize_passes` and the **Per pass** table, computed from the *unmerged* rows. Pass one prefills ~2,100 tokens of taxonomy to generate seven fields; pass two prefills a short rubric plus a few ladders to generate two. Measured on this hardware: 7B 69.9 then 71.4 tok/s decode, 14B 37.1 then 38.1, with prefill rates differing by 7× between the passes. One blended rate describes neither. |

The schema-fingerprint hazard this section used to flag is closed, and it had been caught being real: **S-002 and S-003 share a prompt byte for byte and differ only in whether the tag fields were `required`**, which was the difference between a tier emitting `roles` on every card and on none (`lessons-learned.md`). `output_schemas_sha256` now sits beside the prompt fingerprint, so a resume across that change is refused instead of accepted, and the report's Overview warns on either key.

### `num_ctx` came down from 16,384 to 8,192, and it mattered

16,384 was sized for the retired single-call prompt (~14,100 tokens). Pass one is ~2,126; pass two's worst case — its rubric plus the widest ladder selection the schema permits — is ~1,400. The preflight checks **both arms**, the pass-two arm against a synthetic `MetadataPassOne` with its lists filled to `MAX_TAGS_PER_CARD`, because a rating call that silently loses its ladders produces uncalibrated ratings with nothing in the output to show it.

The payoff was not theoretical. The KV cache is VRAM, and at 16k the 14B sat at 11.5GB of 12.4GB — the condition under which the driver evicted ~1GB mid-session and dropped its decode rate to 2.7 tok/s, costing S-002 its entire latency column. At 8,192 the same model on the same machine measures **37 tok/s**, above even the 30 tok/s that was recorded as its uncontended rate.

### What S-001 to S-003 are evidence for, and what they are not

They measured a prompt production does not use. The single-call prompt still carries all 66 anchor ladders and none of `game_stage`'s turn ranges or its 14 reference profiles, so:

- Their `power_rating` reproduced the flat band around 7 that splitting the call exists to remove — 26 of 30 cards on exactly 7 for the 7B in S-003 — across two model families and three vocabularies. That is now well established as a property of the *one-pass prompt*, which is the one thing those sessions prove cleanly.
- Their `game_stage` was scored by `castability r` against a field the prompt never defined. Read per session rather than pooled, the 14B went +0.406 (coder models) → −0.025 → −0.124; at n=24 every one of those but the first includes zero. The honest finding is not a sign but a shape: the 14B's `early` is a near-constant around 6, and a near-constant has no correlation to give.
- Their `roles`/`themes`/`synergy_tags` story is a schema finding, not a model one, and it holds: see `lessons-learned.md`.

The file is archived as `benchmark-v2-sessions-001-003.db` beside the live store and tracked in git. Reading it needs the version-2 code from git history — the store has no migration path by design — which is why the conclusions above live in these docs rather than only in that file.

**How the split was measured before the harness could.** A throwaway script, not committed and touching no harness code: the same cards the sample froze, each run through the frozen one-pass prompt and the new two-pass path against the same model, seed, `num_ctx` and `num_predict`, printing both `power_rating` distributions. On S-001's own 30 hard cards with `qwen2.5:7b`, the one-pass arm came back at a 7.23 mean with **19 of 30 cards on exactly 7** and nothing below 5 — closely reproducing S-001's 7.07 / 7.13, which is worth knowing on its own: that cluster is a property of the prompt, not of that session. The two-pass arm came back at 5.57, median 6, range 3–7.

That is enough to answer "did the spread move", which is the question the change was making. It is not enough to answer anything in *Scoring* — those need the conversion above — and it cannot separate "the scale is being used" from "everything moved down", since the two-pass arm produced nothing above 7 either. Settling *that* needs ground truth.

Two things that measurement turned up, both worth knowing before the conversion:

- **`num_ctx` has to fit the *larger* arm.** The one-pass prompt needs ~16.7k tokens with `num_predict=2048`; at 16384 the preflight refuses it, and anything that didn't check would have silently truncated the baseline's anchor block and flattered the new path. Fact 1 above, met in the wild.
- **Removing context is not free for a small model.** With the anchor block gone from the prompt, the 7B fell into a repetition loop on at least one card — emitting `"Big Mana", "Lands Matter", "Big Mana", …` until it hit `num_predict`, failing the whole card. The fix was in the schema, not the prompt (`maxItems` on the closed-vocabulary lists); see `lessons-learned.md`. A shorter prompt is not automatically an easier one.

### A cheap tier-viability probe: can the model reproduce a rung?

Not part of the harness, but it answers a piece of the escalation question for about twelve model calls, and it should probably become part of it.

Ask the model to rate a card that **is itself a rung** of the ladder it is shown, so the prompt literally contains `1-2  Jayemdae Tome (power_rating=1)` while the card under test is Jayemdae Tome. Reproducing a printed answer is the easiest form of the task, so this is a floor on calibration rather than evidence of it — but a tier that fails it cannot be trusted to place novel cards on the same scale.

| Rung | `qwen2.5:7b` | `qwen2.5:14b` |
|---|---|---|
| Jayemdae Tome = 1 | **3** | 1 ✓ |
| Healing Salve = 1 | **3** | **3** |
| Divination = 3 | 3 ✓ | 3 ✓ |
| Mind's Eye = 5 | — | 5 ✓ |
| Blue Sun's Zenith = 7 | — | 7 ✓ |
| Sol Ring = 8 | 8 ✓ | 8 ✓ |
| | 2/4 | **5/6** |

Two findings worth carrying into the escalation rule. The **7B floors at 3** — it never emits 1 or 2, even with the answer in front of it — so its usable range is 3-10 and any `power_rating` it produces in the bottom two bands is unreachable by construction, not merely rare. And `game_stage`, which has no ladder, is close to a *constant* on both tiers (the 7B returned 7/8/6 for three unrelated cards; the 14B spans 6/7/5 to 9/8/7). Since `game_stage` is generated before `power_rating` and handed to pass two as settled fact, an unanchored near-constant is feeding the one field that is anchored.

## Explicitly out of scope here

- Running metadata generation against the full corpus — that's `metadata_generator` (`backend/knowledge_pipeline/metadata_generator/`) once this benchmark has picked a tiering strategy.
- The Batches API / prompt caching mechanics for a production run — those matter once a model tier is chosen; this benchmark is small enough to run as ordinary synchronous calls, not a batch job. Note that Ollama has no `cache_control` equivalent, so there is nothing to configure on the local path; it does get an implicit prefix-KV-cache benefit from the identical system block across every card of a session, which shows up in `latency_ms` as a measured effect rather than an asserted one.
- A free-JSON pass measuring whether the Qwen models honor the closed vocabulary *unaided* — a separate run into its own store, not yet done. See the note under *Models under test*.
- Wiring the frontier tier, which needs `generate_observed` on `AnthropicMetadataBackend`.
- Repeating a card with varied seeds to measure the models' own stability — a `--repeats` pass over a subset. The deterministic default is the right one; this is what would turn a `tie` from "agreed once" into "agree reliably".
- Blind pairwise picking (model identity hidden, sides randomized). The picks are non-blind by choice; the expectation bias that comes with that is a known cost, not an oversight.
- Any schema or migration change to either production database (knowledge or local) beyond what `data-model.md#cardmetadata` already documents — the three tables above are a standalone benchmark-only store, never migrated alongside either plane.

This document specifies the benchmark and the harness that runs it. The harness exists; the full three-tier run, and the hand-labeling it would be scored against, do not.
