# Lessons Learned

A record of decisions that were tried, scoped, or built one way and later changed — and why. The rest of the docs describe Tome as it stands today; this file is the only place that talks about how it used to be. If you're trying to understand the *current* system, you almost certainly want `CLAUDE.md` or the doc this file is linked from instead.

---

## Knowledge base: from per-user generation to a hosted knowledge plane

**Original design:** every user's own install would run the full Knowledge Pipeline — analyze all ~33,000 cards with a model, embed them, and store the result in a local vector store. Each install built its own copy of the same knowledge base.

**Why it changed:** metadata generation is one model call per card across the whole corpus, plus a full embedding pass. Making every user pay that bill (hours of compute, a real API cost) before generating their first deck was the single largest barrier to using Tome, and it produced no benefit — the analysis of a given card doesn't depend on who's asking.

**What replaced it:** Tome now runs as two planes. A **knowledge plane**, hosted centrally by maintainers, builds the card corpus and its AI analysis once and serves it read-only over HTTPS through the Knowledge API. A **client plane** runs on each user's machine — frontend, local backend, deck generation, and a local database holding only that user's collection and decks. Deck generation still happens per-user (and still supports local or frontier models), but knowledge-base construction does not.

This is the split documented as current architecture in `architecture.md#the-split-shared-knowledge-local-generation` and `PRD.md#deployment-model`. Everything else in this file is a downstream consequence of that one redesign.

---

## Vector storage: ChromaDB → pgvector

**Original design:** each user's install ran its own ChromaDB instance on disk as the vector store for retrieval.

**Why it changed:** once the knowledge base moved to a single hosted Postgres (as part of the two-plane split above), a separate per-user vector store had no reason to exist — it just meant a second system to keep in sync with the relational card data.

**What replaced it:** `pgvector` in the same cloud Postgres as `cards` and `card_metadata`. Current rationale for the choice (not the ChromaDB comparison) lives in `architecture.md#why-pgvector-and-not-chromadb`.

---

## Orchestration: LangChain removed

**Original design:** LangChain sat between the retrieval step and the model-calling step.

**Why it changed:** once retrieval was behind the Knowledge API's HTTP contract and generation was behind our own `ModelProvider` interface, LangChain was gluing together two abstractions the project already owned outright — it added a layer without adding capability.

**What replaced it:** direct calls to the Knowledge API client and the `ModelProvider` interface. See `model-providers.md`.

---

## Model access: direct Anthropic SDK → `ModelProvider` interface

**Original design:** the deck pipeline called the Anthropic SDK directly. This was fine under the original assumption of one hosted deployment with one API key.

**Why it changed:** once generation moved to the client plane, the model became the user's choice — a local model on their own hardware, a frontier API key, or an internal OpenAI-compatible gateway. A hardcoded Anthropic call couldn't serve any of those without becoming three hardcoded calls.

**What replaced it:** the `ModelProvider` protocol (`backend/ai/provider.py`) — one small interface (`complete_structured`, `health`) that every backend implements, so the pipeline itself never branches on which provider is configured. See `model-providers.md`.

---

## Scryfall importer: format-scoped → whole-pool

**Original design:** the importer accepted a `--format` flag and only pulled in cards legal in the selected format(s); `--prune` deleted cards that fell outside a newly-narrowed scope, guarded by a check that refused to run while the same database held `collection`/`decks` rows.

**Why it changed:** format scoping existed so a user self-hosting the whole knowledge base could trim it to their own disk and time budget. Once the knowledge base moved to one hosted deployment (see above), that reason was gone — breadth is now paid once, centrally, and Commander alone is 91% of the importable pool anyway, so scoping the *import* saved almost nothing. Meanwhile a user's *collection* can contain cards legal in no format at all (Un-set cards, etc.), which still need to resolve during CSV import — so a format-filtered corpus would have made those permanent placeholders.

**What replaced it:** the importer takes every card, every run, with no format flag — passing `--format` or `--prune` now exits 2 rather than silently doing nothing. Format filtering moved downstream, to the AI stages and to the client. Current behavior: `knowledge-pipeline.md#the-import-is-not-format-scoped-and-cannot-be-made-so`.

**Knock-on effects of the same change:**
- `--prune`'s guard read `collection`/`decks` from the same database to decide whether it was safe to delete rows. Those tables now live on users' machines, invisible to the importer, so the guard could no longer see what it was protecting — it was removed along with the flag rather than left returning a false "all clear."
- `--reset`'s own confirmation used to refuse outright while `collection`/`decks` held rows, for the same reason. It was replaced with a different safeguard: printing the target database's name and card count, then requiring that name typed back exactly, with no non-interactive override.
- `ImportRun` used to carry `format_profile` and `cards_pruned` columns, tracking which format scope a run used and how many rows a `--prune` deleted. Both were dropped — every run now takes the whole pool (nothing to record a "profile" of), and nothing deletes rows anymore (nothing for `cards_pruned` to count).
- A `formats.py` module used to own the nuance that a Scryfall `"restricted"` legality value means *legal, limited to one copy* (e.g. Black Lotus in Vintage), not banned. It was deleted along with format scoping; the nuance itself still matters for anything that filters by format client-side, so it's preserved as a note in `knowledge-pipeline.md#the-import-is-not-format-scoped-and-cannot-be-made-so`.

---

## Metadata generation: frontier-only plan → tiered local/frontier

**Original plan:** generate `CardMetadata` for every card with a single frontier model call, once, centrally. Expensive per call, but simple, and the frontier model set the quality ceiling by construction — no ambiguity about whether the result was good enough.

**Why it changed:** running a frontier model against the full ~31,830-card Commander-legal pool is a large, recurring cost for a stage that only needs to happen once per card ever (not once per user). A local model handling most of the corpus, escalating only cards it's likely to get wrong, is far cheaper — but unlike the frontier-only plan, it isn't obviously correct. It trades a quality guarantee for an empirical claim (a local model's judgment is close enough on the easy majority, and the escalation rule actually catches the hard cases) that has to be checked, not assumed.

**What replaced it:** a 300-card hand-labeled benchmark, run before any full-corpus generation, that decides whether a local tier is viable at all, what triggers escalation to frontier, and what fraction of the real corpus that escalation rule would route to frontier. Full design: `benchmarking-and-testing.md`. **The harness is now built** — sample selection, the Ollama-backed local tiers, the execution loop — but the full run is not done, and two things still gate its conclusions: hand-labeled ground truth, and the frontier tier. See *Local-model benchmark: the guards learned by building it* at the end of this file for what standing it up taught.

---

## Config: `MODEL_API_KEY` mandatory → optional per provider

**Original design:** `MODEL_API_KEY` was a required setting, because Anthropic was the only supported provider and every deployment needed a key.

**Why it changed:** the `ModelProvider` interface (see above) added `ollama` as a fully local option that needs no key and no network call at all. A blanket "key required" check would have broken that path for no reason.

**What replaced it:** per-provider validation — the settings factory only requires a credential from the provider actually selected. Current behavior: `model-providers.md#configuration`, `self-hosting.md#configuration`.

---

## Frontend: collection filter logic unified

**Original state:** collection filtering/sorting logic was duplicated between the `/collection` page and the deck builder's `CollectionPanel`.

**Why it changed:** the two copies were the same filtering rules serving two views of the same data, and drifted as one was edited without the other.

**What replaced it:** `src/lib/filter-cards.ts` as the single source of truth (`CollectionFilters`, `DEFAULT_FILTERS`, `applyFilters`), consumed by both surfaces. Current convention: `frontend.md#shared-filter-logic-and-ui-do-not-re-duplicate`.

---

## Anchor cards: one exemplar per tag → a five-rung ladder

**Original state:** every `Role`/`Theme` value had **exactly one** designated anchor card carrying a fixed `power_rating`, embedded in the generation prompt as a calibration example. `ANCHORS` was a `dict[Role | Theme, AnchorCard]`.

**Why it changed:** one labeled point cannot calibrate a scale. Telling the model "Cultivate is a 6" gives it no reference for what a 3 or a 9 looks like, so the remaining nine-tenths of the range stayed exactly as ungrounded as before — the drift the anchors existed to stop was displaced to the ends rather than removed. The candidate set made this concrete: the clearest-embodiment pick for each of the 53 tags clustered at 6–7 and produced *nothing* in the 1–2 band, so the model would never have seen an example of "unplayable".

A second problem surfaced with it: `docs/data-model.md` defined the 1–10 scale only as "calibrated against the anchor card", which is circular — the anchors *are* the scale, so the scale had no stated meaning at all.

**What replaced it:** an `AnchorLadder` per tag — five `AnchorCard`s, one per band (`1-2`, `3-4`, `5-6`, `7-8`, `9-10`). The model now rates by reading a card against the rungs of a ladder for a tag it shares and placing it between them, a comparison rather than a judgment against an abstraction. A ladder is complete or absent: `AnchorLadder` refuses to construct with fewer than five, because a tag with four reviewed rungs and one guess yields ratings indistinguishable from fully anchored ones. The band rubric is now written down (`data-model.md#the-band-rubric`), including the rule the design most easily gets wrong — **universality is not power**: a cheap colorless card playable everywhere is a 7–8 staple, not a 9–10.

Cost: the hand-labeling job went from 53 cards to 260, and it gates the whole benchmark (`benchmarking-and-testing.md#ground-truth`).

**Two bugs this surfaced, both worth remembering:**

- `ANCHORS` keyed on the enum member silently lost ladders. `Role` and `Theme` are `StrEnum`s, so `Role.LIFEGAIN == Theme.LIFEGAIN` and the two hash identically — and both vocabularies define `Lifegain` *and* `Equipment`. Each pair collapsed into one dict entry, last write winning, with nothing raised and `missing_tags()` blind to it for the same reason. The registry is now keyed by `anchor_key(tag)` (`"Role.LIFEGAIN"`) and populated through `register(ladder)`, which takes the ladder so the tag can't be stated twice and disagree. `AnchorLadder.render()` also qualifies its heading by kind, since two prompt blocks headed `Lifegain:` would be ambiguous to the model as well.
- Building the two halves of the registry independently let the same card be rated two ways. 17 cards ended up with conflicting ratings across the role and theme files, 12 of them crossing a band boundary. All 52 ladders render into **one shared cached system block**, so a card appearing at two ratings teaches the model the inconsistency the anchors exist to prevent. Any future split of this work needs a cross-file rating check, not just per-file validation.

## Taxonomy: `Theme.MIDRANGE` dropped rather than anchored

**Original state:** `Midrange` was a member of the `Theme` closed vocabulary.

**Why it changed:** building its anchor ladder proved it wasn't assignable. Generation is stateless and per-card, but "midrange" describes a deck's posture across a whole game — no printed card *is* midrange, and a three-mana value creature is equally at home in aggro, control, or combo. The ladder built for it came back self-labeled as not honest. Because the vocabulary is closed, the model couldn't decline the tag; it would have returned a confident, schema-valid guess that retrieval then filtered on.

**What replaced it:** nothing — the member was removed, leaving 28 themes and 52 tags. The general rule it produced is now stated in `data-model.md#a-member-has-to-be-decidable-from-the-card`: a closed-vocabulary member has to be a question answerable from the one card in the prompt. `Theme.CONTROL` and `Theme.AGGRO` sit near the same line and were kept deliberately, because a card's own text can be evidence that it *supports* those strategies.

## Taxonomy: `Theme.FLYING` dropped rather than anchored

**Original state:** `Flying` was a member of the `Theme` closed vocabulary, added on the reasoning that flying is unambiguously printed on cards and so trivially decidable — the exact test `MIDRANGE` had just failed.

**Why it changed:** it is decidable, and that turned out not to be enough. The theme is meant to collect cards a flying-based deck *wants* — payoffs — but almost every card that reads as a strong flying card is one that *grants* evasion. Building the ladder made the mismatch impossible to paper over: candidate after candidate was an anthem or a keyword-granter, and the 9-10 rung had essentially nothing on the payoff side at all. Every rung would have graded a different question than the tag asks, which is the same failure as `MIDRANGE` arriving by a different route.

**What replaced it:** nothing — the member was removed, leaving 27 themes and 51 tags. Evasion is a property of a card, not an archetype, and the card's own oracle text already carries it for anything downstream that cares.

**The transferable rule:** *decidable from the card* is necessary but not sufficient. A member also has to be **anchorable** — there must be a real card at each band that is a typical instance of the tag. A member that cannot fill its own top rung is describing something other than what its name suggests. Try building the ladder before adding the member; it is a cheaper test than it looks, and it is the one that catches this class.

## Anchors: registry ships populated (and a bias found in the selection itself)

**Original state:** `ANCHORS` shipped **empty** by design, with the pipeline logging a warning and falling back to the model's own judgment. The reasoning was sound — a guessed anchor is indistinguishable from a reviewed one in the generated corpus — but it left `power_rating` with no stated meaning at all, since the anchors *are* the scale.

**What replaced it:** all 51 ladders, 255 distinct cards — since grown to **66 ladders, 330 cards** by the vocabulary revision below. Candidates were proposed by corpus search (four per rung, each resolved against the `cards` table so no rung is a recollection), ranked mechanically, then chosen by hand.

**What the numbers say about that process:** the ranking left the top two candidates **exactly tied in 101 of 260 rungs**, and within 0.75 in 204. It reliably separates a clearly-worse candidate from the rest and rarely picks a winner among near-equals. That is the honest shape of the problem, not a defect in the scoring — which is why the selection is a human pass with machine triage, and why nothing was auto-applied.

**Two biases found in the selection machinery, both worth remembering because both favoured the newer work:**

- **An "obscurity bonus" that only new candidates could earn.** The brief argued that a low-profile card is often a *better* ruler than a famous one, because the model cannot substitute reputation for applying the rubric — so the scorer awarded a point for it. But only the second search pass recorded a `profile` field; the 500 earlier picks had none, so the signal was a structural one-point handicap on every older candidate. It moved the recommendation split from 163 new / 97 old to **129 / 131** once removed. A signal that one group cannot physically earn is not a signal, it is a thumb on the scale — check that every candidate *can* score on a criterion before weighting it.
- **A too-literal text test on the heaviest-weighted signal.** Tag membership was checked by matching each ladder's own SQL `LIKE` pattern as a substring, which scored *Cultivate* as having no evidence of being Ramp: the predicate says "basic land card" and the card says "basic land cards". Content-word overlap cut false negatives from 380 to 225. On the signal that carries the most weight, a false negative is worse than a loose match.

**Two whole-set rules the ladders obey, now enforced by tests rather than by comment** (`tests/test_anchors.py`): no card carries two different ratings across ladders, and no card anchors two ladders. The first is the cross-file check the previous entry said any future split of this work would need. The second is weaker but real — a shared rung is one fewer independent reference point, and on the `Role`/`Theme` pairs that share a word (Lifegain, Equipment) the same card on both sides anchors both halves of the distinction those tags exist to draw. Enforcing the second turned up three rungs whose *axis* was wrong as well: `Theme.SACRIFICE`'s top rung graded a payoff on a ladder that measures outlets, and `Theme.LIFEGAIN`'s graded a card that gains life on a ladder that measures cards triggered *by* gaining it.

## Anchors again: the human-review step did not survive 330 rungs

**Original state:** the process above, stated as a principle — *the tool proposes and ranks; a human picks every rung*. It held comfortably at 255 rungs, and `anchor_bench` was built around it: the ranking is documented as triage, the review page applies nothing automatically, and `export` renders only the ladders a reviewer explicitly marked reviewed.

**What happened:** the `Role`/`Theme` revision orphaned 9 ladders and added 24 unanchored members. Recalibrating meant 31 ladders proposed four-candidates-deep — **155 rungs, 444 candidates** — on top of 35 ladders carried over verbatim. Partway through the review the maintainer asked for the ranking's recommendation to be taken wholesale, then overrode it on 6 rungs. So 149 of 330 rungs were settled by a scorer whose own documentation says it is not a verdict, and **48 of those were exact ties** decided by the tie-break (shorter oracle text, then alphabetical name) — a rule chosen for *reproducibility*, which then found itself choosing anchors.

**What that actually cost, stated plainly rather than defended:** eight previously hand-reviewed cards lost their rung, five of them the deliberately contested cards that had been offered to two ladders *precisely* so a human would choose a side. The ranking rated a different card first on both sides, so they anchor nothing now. Two axis splits (Reanimator/Reanimation, Voltron/Auras) were ratified by default rather than approved. None of this is invisible — the picks were read over and judged reasonable, and six were changed — but "reviewed as a batch and found acceptable" is a weaker claim than "chosen," and the registry's provenance should not be remembered as the stronger one.

**The outcome, recorded because it tests the paragraph above:** a verification pass later read the 155 rungs back against the corpus and found eleven notes that misdescribed their own card and eight rungs that graded a different property than their ladder's axis comment; the notes were rewritten and the rungs replaced across six ladders. The Voltron/Auras split ratified by default was among the things that turned out wrong — each ladder's comment claimed the *other* one held the Auras, while two Aura rungs sat in Voltron the whole time. The delegation cost something measurable, and it took a second reading against the oracle text to see it.

**The transferable rule:** *a review step whose cost scales with the vocabulary will be skipped exactly when the vocabulary grows.* The design error was not delegating — 155 rungs is genuinely a lot — it was building a process whose only mode is all-or-nothing human attention, and leaving the tie-break to double as a chooser. What would have helped: ranking *rungs* by how much the decision matters (a tie among four near-identical mana rocks is cheap; a tie at a 9-10 rung is not), so review effort could go to the 48 that were coin flips instead of being spread evenly over 155. A triage scorer should triage the reviewer's attention, not just the candidates.

**The second-order consequence, caught by a guard that was built for a different reason:** the anchor block grew 60%, from 27,652 to **44,469 characters (~12,562 tokens)**. `runner.py`'s `DEFAULT_NUM_CTX = 12288` was sized against the old figure "with room to spare", so the benchmark's default context no longer holds the ladders. The first-call truncation guard (next section) refuses the run rather than recording uncalibrated ratings — which is the whole argument for that guard existing. **Prompt size is a budget the vocabulary spends.** Five cards per new member is not only five cards to choose.

## `anchor_bench` deleted: scaffolding for a review step that had stopped happening

**Original state:** `anchor_bench` — a candidate-pool JSON contract, a triage scorer, a self-contained HTML review page, and an `export` step that rendered only the ladders a reviewer had explicitly marked reviewed. About 2,800 lines of Python plus a 1,324-line HTML template, every piece of it in service of the principle in the entry above: *the tool proposes and ranks; a human picks every rung.*

**What happened:** that principle had already failed under load (above), and the recalibration that followed showed the cheaper path. A frontier model proposing *and* choosing rungs against the band rubric is better calibrated than a tie-break on oracle-text length, and it costs a prompt rather than a day. So the tool's whole middle — rank, page, export, and the pool format that fed them — existed to support a step nobody was performing. The docs kept asserting that step anyway, which is the part worth noticing: `CLAUDE.md` and three files under `docs/` described a human review process the project had quietly stopped following.

**What replaced it:** the model writes `register(AnchorLadder(...))` calls straight into `anchors.py` — no intermediate format, because the registry *is* the artifact — and two deterministic gates check the result. `metadata_generator/verify_anchors.py` (~130 lines, ported from the one part of `pool.py` worth keeping) says every rung resolves to a real, Commander-legal card in the corpus; `tests/test_anchors.py`, already written and untouched, says the set is coherent. `docs/anchor-recalibration.md` is the runbook. Net: ~4,100 lines deleted, ~220 added.

**The transferable rule:** *when a review step stops being performed, delete the scaffolding built for it rather than keeping it as a statement of intent.* Tooling that encodes a process is a claim about how the work is done, and a stale claim in a doc is worse than no claim — the next person reads it as current. The honest move when the process changes is to change the tooling, or to delete it and say why.

**A second observation, about which checks were worth keeping.** The parts that went were the judgment parts: ranking cards by "ruler-mark quality", laying candidates out side by side, scoring text-reading speed. A model does that work better and without a page to click through. The part that stayed is the part a model *cannot* do for itself at all — whether a card exists under that exact name and whether the format can legally play it. That is a database question, and no amount of model capability answers it. When deciding what survives an automation pass, keep the deterministic checks and drop the judgment scaffolding, not the other way round.

---

## Local-model benchmark: the guards learned by building it

The benchmark harness (`benchmarking-and-testing.md`) was specified before any local model had actually been run. Standing it up against real hardware surfaced a series of failure modes that share one shape: **each produces confident, plausible, well-formed numbers rather than an error**, so none would have been caught by reading the output. The second of them was a fault in the harness's own instrumentation rather than in anything it measured, and shipped a wrong conclusion into these docs before being caught.

### A context default that silently truncates the calibration block

**What happened:** the system prompt carries all 51 anchor ladders and renders to 36,295 characters — 10,245 tokens, measured. Ollama's default `num_ctx` is **4096**. A model handed more than its context does not see the overflow and does not complain, and the ladders are the *last* section of the system block, so the default drops precisely the part that gives `power_rating` its meaning. The run completes, every response validates, and every rating is uncalibrated.

**What replaced it:** `num_ctx` is a **required** constructor argument on `OllamaMetadataBackend` — no default, because the right value depends on the prompt and the cost of guessing low is invisible — plus two guards. A preflight refuses a window too small for the rendered prompt before the model loads, and after the first real call the runner compares the model's own `prompt_eval_count` against `num_ctx` and aborts the run if it is within 5% of it. A prompt that fills its window is indistinguishable from one clipped to fit.

**The transferable rule:** the preflight's estimate was first written as `chars / 3`, erring deliberately large on the theory that asking for too much context only wastes VRAM. That theory was wrong here, and the correction is the more interesting half: an over-cautious estimate **rejected a working configuration and pushed the operator to raise `num_ctx`**, which on a 12GB card is what makes the 14B model spill to system memory — manufacturing the next failure below. "Err on the safe side" is only meaningful once you know which side is safe; when a precise check already exists downstream, the cheap check upstream should be accurate rather than paranoid.

### A throughput metric that invented the problem it was built to detect

**This entry originally recorded the opposite conclusion.** It is kept as a correction rather than rewritten away, because the mistake is more instructive than the fix.

**What happened:** latency is the *entire* basis for comparing local tiers, since `cost_usd` is 0 for both — so the runner checks that a model is really on the GPU before trusting its timings. The check computed tokens per second as `output_tokens / total_duration` and warned below 10. On a 12GB AMD RX 7700 XT the 7B scored 57 and the 14B scored **3.9**, which reads unmistakably as CPU inference. The warning fired, and the conclusion written up here was that the 14B was spilling into GTT (shared system memory over PCIe) while Ollama's `/api/ps` wrongly reported it as VRAM-resident.

**That was wrong, and `/api/ps` had been telling the truth.** The Ollama server log settles it: `offloaded 49/49 layers to GPU`, `ROCm0 model buffer size = 8148.38 MiB`, `ROCm0 KV buffer size = 2304.00 MiB` — about **10.3GB resident in 12GB of VRAM with ~1.7GB spare**, every layer on the GPU. Measuring the phases separately:

| | model load | prefill | **generation** | output ÷ total |
|---|---|---|---|---|
| 7B | 5.7s | 1,918 tok/s | **59 tok/s** | 14.7 |
| 14B | 9.3s | 291 tok/s | **30 tok/s** | 4.1 |

The 14B decodes at 30 tok/s — healthy, and about half the 7B's, exactly what doubling the parameters predicts. The "3.9 tok/s" was an artifact of dividing a ~200-token answer by a call that also contained a one-off 9.3-second model load and a 35-second prefill of a 10,245-token prompt. **The metric conflated three phases with different costs and manufactured a hardware fault out of a large prompt.**

**What replaced it:** `GenerationAttempt` now carries `prompt_eval_ms` and `eval_ms` separately, the guard judges `eval_count / eval_duration` alone, prefill is reported beside it as its own number, and `/api/ps` is quoted only as colour on a problem already established by measurement. The regression test encodes the real cold-call numbers and asserts no warning fires.

**Three transferable rules, in order of how much they cost:**

- **A ratio is only meaningful if its denominator contains one thing.** Total call time held load, prefill and generation; dividing output tokens by it produced a number with units of tokens-per-second and the meaning of nothing. Before trusting a rate, ask what is in the denominator that does not belong.
- **The previous entry's rule was applied too eagerly.** "Prefer measuring the quantity you care about over reading a field that claims to describe it" is good advice, and it is what led here — `/api/ps` was *distrusted on principle* and the home-made measurement believed, when the home-made measurement was the broken one. A disagreement between an instrument and your own metric is not self-evidently the instrument's fault. Check your metric against a third source: the server log was available the whole time and would have settled it in one grep.
- **Corroborating evidence was ignored because it was inconvenient.** The 7B scored 14.7 "tok/s" on the same metric — also absurd for a model that was demonstrably fast — and that should have indicted the metric rather than only the 14B. A measure that is implausible for the *healthy* case is not measuring what you think.

**The incidental finding worth keeping:** prefill *is* genuinely ~6.6× slower on the 14B, not the ~2× the parameter count suggests. It does not matter much in practice, because the system prompt is identical across all 300 cards and llama.cpp reuses its KV prefix — prefill collapses to ~0.2s after the first call, putting steady state at ~7.5s/card for the 14B and ~3.5s for the 7B. The whole two-tier run is under an hour rather than the 4.5 hours the wrong numbers implied. That the benchmark's runtime rests on prefix caching is now stated in `benchmarking-and-testing.md`, since editing the rubric or anchors mid-run silently restores the slow path.

### A stratified sample that could not reach the end of the alphabet

**What happened:** the 300-card sample is drawn stratified across colour identity, mana-value band and base type, so a difficulty bucket cannot come out as 100 green ramp spells. The draw visited strata in `sorted()` order, round-robin, stopping once the 100-card quota filled. With a tercile of ~10,500 cards spread over several hundred strata, the quota fills **before one pass completes** — so the pass never reaches the end of the sorted keys. The shipped sample contained no white or blue cards at all, and red twice in 300. Bucket sizes were exactly right, mana-value and type spread looked healthy, and the file validated.

**What replaced it:** the strata keys are shuffled with the seeded RNG before each pass. Reproducible from the seed as before, but every stratum is now equally reachable. A regression test builds a corpus with more strata than the quota and asserts that the alphabetically-last colours are drawn.

**The transferable rule:** this is the *same bias* as the anchor selection's "obscurity bonus" two entries up, in different clothing. There it was "a signal one group cannot physically earn is not a signal"; here it is "a stratum the draw order never reaches is not a stratum". Both are cases where a mechanism looked fair because every candidate appeared in the input, while a detail of the traversal meant some could never be chosen. When sampling or scoring, check reachability explicitly — not just that the inputs are all present.

### A guard that passed vacuously for its entire life

**What happened:** `preflight()` has two bounds on `num_ctx`. The lower one — is the window big enough for the rendered prompt — is the entry at the top of this section, and it works. The upper one asks whether `num_ctx` exceeds the model's own *trained* context length, and it never once ran.

It read the value off the `/api/list` entry's `details`. That object carries `parent_model`, `format`, `family`, `families`, `parameter_size` and `quantization_level` — it has no `context_length`, and never did. The read was a `getattr(..., None)`, so it returned None for every model ever checked. `within_model_limit` is written to pass when the value is unknown, which is the right call for an advisory check, and the result is that it returned `True` unconditionally from the day it was written. The trained ceiling actually lives on `/api/show`, under `model_info`, keyed by architecture (`qwen2.context_length`), with the architecture itself at `general.architecture`.

Nothing failed. No run was wrong because of it — `DEFAULT_NUM_CTX` is 12,288 against a 32,768 ceiling, so the check had nothing to catch yet. It was found by printing a preflight while validating an unrelated model swap and noticing the field was None for a model whose context length is plainly reported by `ollama show`.

**What replaced it:** `_trained_context_length()` reads `/api/show`, preferring the `general.architecture` key and falling back to a scan for any `*.context_length`. It returns None rather than raising, so an unreadable value still cannot break a preflight whose real job is the lower bound — but now None means genuinely unavailable instead of always. `show` is skipped entirely when the tag is not pulled. Fourteen tests cover it, and the four that matter were confirmed to fail against the old behavior before being kept.

**The transferable rule:** a guard that can only ever pass is indistinguishable from a guard that works, and unit tests written against the same wrong assumption would have agreed with it. What separates the two is an observation that the check has ever *fired* — so when a safety check has no failing case in its test suite, the missing test is not an edge case, it is the only test that proves the thing is wired up at all. The cheap version of that proof is the one used here: break the implementation on purpose and confirm the tests go red.

### Wall-clock latency, a sleeping host, and the GPU question answered a third time

**What happened:** the first real benchmark run produced two findings, one of which corrects the entry two above.

The 14B's decode rate had been established at 30 tok/s, fully GPU-resident, with ~1.7 GB of VRAM spare. In the actual run it decoded at **4 tok/s** — the same model, the same `--num-ctx`, the same 49/49 layers offloaded. Neither the earlier measurement nor the earlier correction was wrong about the moment it was taken; both were wrong to treat the rate as a property of the hardware. Windows' performance counters showed `12,046 MB of 12,272 MB` dedicated VRAM in use with `1,037 MB` paged out to shared memory, because desktop applications — Wallpaper Engine, Chrome, Steam, VS Code, Discord, Obsidian — held about 1.4 GB between them. llama.cpp's load-time fit check had seen 12,126 MiB free and planned 1,538 MiB of headroom; the WDDM driver evicted ~1 GB *after* the fit succeeded. Decode reads every weight per token, so a gigabyte arriving over PCIe costs ~7.5×, while prefill was untouched because it is compute-bound rather than bandwidth-bound.

So the original GTT hypothesis had the right mechanism, the correction had the right residency reading, and neither was a stable fact. **Three measured rates for one unchanged configuration: 30 tok/s uncontended, 20 under light contention, 4 with ~1.4 GB held elsewhere.**

Separately, the run recorded one call at **49,866,300 ms — 13.8 hours — for 220 tokens.** The machine had slept mid-generation. Ollama's `total_duration` and `eval_duration` are both wall clock, so the suspension was billed to inference, and llama.cpp printed `0.00 tokens per second` without anything failing. No client timeout fired: the whole host slept, so the HTTP read timer stopped with it and resumed on a live socket. The resulting corruption was quiet in the way that matters — `p50` moved by 126 ms while the **mean went to 3,365,675 ms and p95 to 15,004,868 ms**, because a median is robust to one outlier and a mean is not. A reader checking the median would have found nothing wrong.

**What replaced it:** `runner.timing_anomaly` rejects a call whose decode rate is below 0.5 tok/s (a stall during generation, which inflates `eval_duration` too) or which leaves more than 60 s of wall clock belonging to no phase (a stall outside it, where every phase timer looks healthy). `load_duration` is now captured as `load_ms` so the three phases can be checked *against* the total rather than merely reported beside it. A flagged call is discarded rather than recorded — and because `resume` skips only recorded `(card, tier)` pairs, a discard is a retry deferred to the next run, so nothing is lost. The guard also runs before the residency check, since a stalled warm-up would otherwise collapse to ~0 tok/s and produce a confident CPU accusation: the same wrong conclusion as the entry above, reached by a different route.

**Three transferable rules:**

- **A measurement is not a property.** "The 14B decodes at 30 tok/s" was recorded as a hardware fact when it was a reading taken under conditions nobody wrote down. The fix is not more precision but stating the conditions: the docs now carry all three rates with what was contending for VRAM in each.
- **Wall clock measures the world, not your program.** Any duration crossing a suspend boundary includes the suspension. This is not exotic — an overnight run on a laptop hits it routinely — and it argues for sanity-checking every externally-supplied duration against a physical floor rather than trusting a provider's telemetry because it is detailed.
- **Prefer the statistic that breaks loudly.** The median's robustness was a liability here: it hid the corruption that destroyed the mean. When a pipeline can ingest a poisoned value, a fragile summary statistic beside a robust one is a free detector — the two disagreeing is the alarm.

**Since closed:** `benchmark_runs` now stores `load_ms`, `prompt_eval_ms` and `eval_ms` per row, so the decode rate is reproducible from the database rather than only from the runner's console summary. The *discard* decision is still made at write time — a row that was never written cannot be re-judged — but the judgment behind it no longer evaporates with the console scrollback. See the entry below, which is where that came from.

---

## One pooled run log → paired sessions

**What changed:** the benchmark's run log had no concept of a run. `benchmark_runs` carried a `run_at` timestamp and nothing tying a sitting together; each tier iterated "the sample, minus whatever that tier already had, capped at `--limit`". It is now organized around **sessions**: one invocation of `run` draws a card set from named difficulty buckets, freezes it on a `benchmark_sessions` row *before the first call*, and puts every tier through that same list.

**Why.** Three sittings into the first real run, the store held 45 rows which could not answer the only question the benchmark exists to ask. The 7B had covered 15 cards; the 14B, 30. **Every row was from the easy bucket.** Neither fact was a mistake anyone made — both followed from the design:

- `run` had no `--bucket` flag at all (only `report` and `review` did), and `sample.json` is ordered easy→medium→hard, so `--limit N` could only ever return easy cards. A flag added for smoke tests had silently become the thing that chose what the benchmark measured.
- `--limit` applied *after* the resume filter, and resume skipped any `(card, tier)` pair recorded anywhere in the store. So each sitting took "the next N not yet done *for this tier*", and the two tiers walked apart.

This is the same shape as the anchor-selection biases recorded above: **a measurement whose subject is chosen by a mechanism nobody is looking at.** There the signal was one a group could not physically earn; here it was a card set the harness never stated and therefore never checked.

**What the fix actually is.** Not a flag rename. The structural claim a comparison needs — *these tiers answered these cards under this prompt* — had nowhere to live, so nothing could verify it. Now it does:

- the card set is a session column, written before any call, and resume is scoped to `(session, card, tier)` — so a card run in another session is still run in this one;
- a session closes `complete` only when every cell of its grid has a row, and `partial` otherwise, which is what the report gates every paired number on;
- `--bucket` is required and repeatable, with no default and no flag that stands in for one;
- the draw within a bucket reuses the 300-card selection's stratified round-robin rather than taking the first N, so a session is not an archetype sample either;
- the session records `system_prompt_sha256`, `num_ctx`, `temperature`, `model_seed` and the model tags, so two sessions are either comparable or visibly not. A prompt edit between sittings had previously been undetectable — the prompt is a 36KB blob nobody diffs by eye.

**The 45 rows were deleted rather than migrated.** They were the measured output of real hardware, and the store is tracked in git precisely so measured evidence survives. They were still discarded, because keeping them would have been worse: no session record could honestly describe them (their two tiers ran different card sets, so any `card_ids` list would have been invented), and rows that *look* like evidence while supporting no conclusion are a liability in a file whose whole purpose is to be quotable. What is kept instead is this entry.

**Two things fell out of the rework that are worth their own note:**

- **The system prompt was denormalized into every row** — 1.8 MB of opaque tracked binary for 45 rows, and the reason the resume query had to avoid `iter_runs` entirely. It belongs to the session; it is stored there once and joined back under the same name, so no reader changed.
- **The phase split was being thrown away.** `GenerationAttempt` reported `load_ms`/`prompt_eval_ms`/`eval_ms`, the runner used them to veto a suspended call, and then stored only `latency_ms` — so the one judgment that distinguishes slow inference from a sleeping host could never be re-made against stored rows. The doc had recorded that as a known limitation; it was three columns.

**Also:** with no ground truth, the sound judgment is a *relative* one, so scoring is now a per-card pick between the tiers rather than only a per-run `good`/`acceptable`/`wrong`. That brought its own trap, avoided deliberately: a 30-card win rate is ±18 points at 95% confidence, so the report prints a Wilson interval beside every rate and the projected full-corpus hours beside that. `17-13` is noise, and a bare percentage would have invited reading it as a result.

---

## One call per card → two, because the anchor ladders diluted their own prompt

**What happened.** Every metadata-generation call rendered all 66 anchor ladders into its system block — 330 reference cards, ~48,700 characters, ~14,000 tokens — against a card whose facts are ~300 tokens. Only one to four of those ladders were ever relevant to the card being rated. The cost was not the tokens; it was the ratings. Benchmark session S-001 — 30 hard-bucket cards put through each of the two local tiers — clustered `power_rating` at a **7.07 / 7.13 mean with nothing below 4** (`local_low` min 4.0, `local_high` min 5.0), though the ladders themselves define rungs at 1-2.

An A/B on one card isolated it. "Aang's Journey" — a weak two-mana land-tutor-to-hand — same model, seed and temperature, varying only the anchor block:

| System prompt | `power_rating` |
|---|---|
| Full, 66 ladders (~14,101 tokens) | 7 |
| The model's own two matched ladders (~1,735 tokens) | 6 |

**What replaced it.** Two calls per card, cut on `CardMetadataBlueprint`'s existing field order — which was already a reasoning chain, and already had the cut point in it: everything needed to *choose* a ladder is decided before the ladder is needed. Pass one produces `mechanics` → `game_stage` with no ladders in the prompt at all; pass two is handed the card, pass one's answers, and only the ladders for the roles and themes pass one actually assigned, and produces `power_rating` + `summary`. `anchors.ladders_for` does the selection by exact registry lookup, capped at three, roles before themes. `CardMetadataBlueprint` inherits pass one's seven fields rather than restating them, so the finished row's nine-field order is unchanged and cannot drift.

**Measured after.** 30 hard-bucket cards from the frozen sample, `qwen2.5:7b`, both arms on the same cards with the same seed, `num_ctx` and `num_predict`:

| | mean | median | range | below 4 | below 6 | modal value |
|---|---|---|---|---|---|---|
| One pass, all 66 ladders | 7.23 | 7.0 | 5–9 | **0** | 1 | **7, on 19 of 30 cards** |
| Two passes, 3 matched ladders | 5.57 | 6.0 | 3–7 | **1** | 14 | 5 and 6, 9 cards each |

The one-pass arm reproduced S-001's cluster closely — 7.23 against that session's 7.07 / 7.13 on the same 30 cards — which is the useful part: the flattening is a property of the prompt, not of one session. And it put **19 of 30 cards on the single value 7**. That is the real finding. The dilution was not making ratings somewhat worse; it was collapsing a ten-point scale onto roughly one point of it.

**Two honest caveats on that table.** The distribution did not only spread — it *shifted*: the two-pass arm produced nothing above 7, where one-pass produced eight 8s and a 9. On a hard bucket of 30 largely unremarkable cards a 5.57 mean is the more plausible of the two, but "the model now uses the scale" and "the model now rates everything lower" are different claims and 30 cards cannot separate them. And the bottom is still barely used: one card below 4, against a ladder set that defines rungs at 1-2. Settling either needs the hand-labeled ground truth the benchmark is still missing. Recorded rather than smoothed over, because a number that looks like success is also consistent with a new bias.

**Two design points that are easy to get backwards:**

- **The per-card ladders go in pass two's *user* message, not its system block.** The obvious reading of "pass two's prompt carries only the relevant ladders" is a per-card system block, and it is wrong twice over: it would miss Anthropic's prompt cache on every call, and it would break llama.cpp's KV-prefix reuse, which is what collapses local prefill from ~35 s to ~0.2 s. Both system blocks stay fixed strings built once per run. The ladders are per-card *data*; only the instruction for reading them is a standing rule.
- **`build_system_prompt()` was frozen, not edited.** It still renders the old one-call prompt byte-identically, because the benchmark records and fingerprints it and because a before/after that cannot still render "before" is not a comparison. The duplication between it and the two pass rubrics is deliberate and temporary, guarded by a golden SHA-256.

**The transferable rule:** *context that is merely irrelevant is not merely harmless.* The ladders were correct, carefully built, and individually exactly what the model needed — and supplying all of them at once produced worse calibration than supplying three. Retrieval of the right reference is a step; giving the model everything and trusting it to retrieve is not a way of skipping that step, it is a way of doing it badly. Where the selection can be done exactly in code — here, a dictionary lookup on tags the model had already committed to — it should be.

---

### Removing context is not free: a small model's repetition loop

**What happened.** Found while measuring the split above, on the first card of the run. With the anchor block gone from pass one's prompt, `qwen2.5:7b` at temperature 0 fell into a degenerate loop on the `themes` array — `"Big Mana", "Lands Matter", "Big Mana", "Lands Matter", …` — until it hit `num_predict` at 2,048 tokens, and the whole card failed to parse. A 2×2 over {one-pass prompt, pass-one prompt} × {`CardMetadataBlueprint`, `MetadataPassOne`} put the cause beyond doubt: **the prompt was the variable, not the schema.** With anchors present both schemas terminated cleanly in ~300 tokens; with anchors absent both looped to the ceiling.

The ~48,700 characters of ladder text had been doing a second, unnoticed job: holding a small model out of a repetition attractor. Shortening the prompt removed that by accident.

**What replaced it.** The fix is in the schema rather than the prompt, because the prompt is not where the bug lives. `list[Role]` / `list[Theme]` / `list[SynergyTag]` with no ceiling compile to a decoding grammar with no reason to ever close the array — the loop was *permitted*, and the only thing that had been preventing it was the model's own disposition. `max_length` puts `maxItems` in the schema, so the grammar itself forbids it; a `field_validator` then drops duplicates, keeping first-seen order because `ladders_for` reads that order. The ceilings sit well above any real card, so they bound garbage without truncating an answer.

**The transferable rule:** *with constrained decoding, every unbounded repetition in the schema is a latent infinite loop, and a large prompt can mask it indefinitely.* The guard belongs in the grammar, where it is structural, not in the prompt, where it is a request. And more generally — a change that makes a prompt smaller should be checked for what the removed text was incidentally doing, not only for what it was there to do.

---

### Open: the low end of the scale is still not being used

Recorded as an open issue, not a fix, and deliberately left out of the change above.

The diagnostic case is "Aang's Journey". Restricting the ladders inside a single call moved it 7 → 6; the full two-pass path moved it 7 → 5. Both are the right direction, and neither is far enough: the card sits below the **band-1-2 rungs of the ladders it is shown**, so the defensible answer is 1-2, not 4. Showing the right reference points makes the model willing to go lower, but not willing to go to the bottom.

So there are two problems and only one of them is fixed. The second decomposes into three distinct things, and **only the third is about the anchors at all.**

**The diagnostic: ask the model to rate a card that is itself a rung of the ladder it is shown.** The prompt then literally contains `1-2  Jayemdae Tome (power_rating=1)` while the model is asked to rate Jayemdae Tome. Reproducing the printed answer is the easiest possible version of the task — a lower bound on calibration, not evidence of it — so a failure here is damning in a way a disagreement about a novel card would not be.

| Rung shown in the prompt | `qwen2.5:7b` | `qwen2.5:14b` |
|---|---|---|
| Jayemdae Tome = 1 | **3** | 1 ✓ |
| Healing Salve = 1 | **3** | **3** |
| Divination = 3 | 3 ✓ | 3 ✓ |
| Mind's Eye = 5 | — | 5 ✓ |
| Blue Sun's Zenith = 7 | — | 7 ✓ |
| Sol Ring = 8 | 8 ✓ | 8 ✓ |
| | 2/4 | **5/6** |

**1. The ladder mechanism is sound, and this is the evidence for it.** The 14B reproduces the scale almost exactly, bottom included. The 7B floors at 3 — it will not emit 1 or 2 even when the correct answer is printed verbatim in its context about the exact card it is rating. That is not an information problem, and no amount of anchoring fixes it; it is the central-tendency bias of a model too small for the task. **The effective range of the local-low tier is 3-10, not 1-10.** Production routes to a frontier model, so this is a benchmark finding about tier viability (`benchmarking-and-testing.md`) rather than a defect in the ladders.

**2. The fields feeding `power_rating` are themselves unanchored, and on both local tiers they are nearly constant.** `game_stage` has no ladder, and the 7B returned **7/8/6** for Jayemdae Tome, Healing Salve and "Aang's Journey" alike; the 14B varies only a little more (7/7/6, 7/7/6, 8/7/6, 6/7/5). Pass two is handed those numbers as settled fact before it rates anything. An anchored final number computed from unanchored premises inherits their error — the ladder is a correct ruler held against a mis-measured object.

**3. Tag quality became load-bearing in a way it was not before.** `roles`/`themes` stopped being only stored output and became the *control input* that selects which ladder `power_rating` is measured against. The old design put all 66 ladders in the prompt and let the model pick, so a wrong tag cost one wrong field and the right ladder was still there to be used; now the right ladder is physically absent from pass two's prompt and the error is unrecoverable. This change *moved* that failure as well as shrinking the dilution, and the honest accounting says so — though it is not a robustness the old design actually had, since it demonstrably was not using any ladder at all.

The cap sharpens it, and the two vocabularies misbehave in **opposite** directions as the model gets bigger. Eight hard cards, same cards both tiers:

| | 7B roles | 7B themes | 14B roles | 14B themes |
|---|---|---|---|---|
| mean | 1.8 | **6.8** | **2.9** | 2.2 |
| max (ceiling 8) | 3 | **8** | **8** | 6 |
| at ceiling | 0/8 | **6/8** | **1/8** | 0/8 |
| cap discarded matched ladders | 7/8 | | 5/8 | |

**Theme inflation is largely a small-model artifact** — 6.8 → 2.2, and the ceiling stops being hit at all. **Role inflation is not: it gets worse.** "Lost Jitte" produced *eight* roles on the 14B, hitting the ceiling, where the 7B produced three. So `max_length` on `roles` is not dead weight that happens never to bind; it binds on the better model. Either vocabulary can run away, and measuring only the smaller tier would have concluded the opposite.

The pathological card for both is the same and the reason is legible: **Lost Jitte is modal** — one activated ability with three modes (untap a land / can't block / +1/+1 counter) on an Equipment. Both models tag roughly one role per mode and then overreach, the 7B to `Artifact/Enchantment Removal, Mana Rock, Token Generator` and the 14B to `Mana Rock, Reanimation, Clone`. Neither reached `Theme.EQUIPMENT`, which is what the card actually is. Modal text invites a tag per mode; the vocabulary has no way to say "these are alternatives, not a list."

Two things would de-risk this, neither done here: ordering the cap by an explicit strongest-effect field rather than emission order, and reducing the over-tagging itself, which would stop the cap binding at all. The cap still discarded matched ladders on 5 of 8 cards even on the better tier. *Both were done afterwards — see [`primary_role`, and a ceiling that was never low enough to bind](#primary_role-and-a-ceiling-that-was-never-low-enough-to-bind) for what each actually bought, which was not the same amount.*

A 2×2 on "Aang's Journey" — a 2-mana sorcery that fetches a basic land *to hand* and gains 2 life, which the 7B tagged `Card Draw` + `Lifegain Source` with `strengths: ["Mana Rock", "Card Draw", "Lands Matter", "Lifegain Source"]` — separates it from the description problem:

| | model's tags | correct tag (`Tutor`) |
|---|---|---|
| model's pass one | **5** | 3 |
| hand-written honest pass one | 3 | 3 |

Either repair alone moves it 5 → 3; both together add nothing. The two are substitutes, which is the tell that **the ladder was never the binding constraint on this card** — the inputs were.

**The tagging half is visibly bad on the local tier, and one attempted fix backfired.** `qwen2.5:7b` tagged Shivan Dragon with eight themes including Infect, Auras and Superfriends — hitting the `max_length` ceiling with nonsense. Since pass two's ladders are selected from those tags, this now has a consequence it did not have before: a wrong tag is a wrong reference card. The obvious prompt fix made it worse. A pass-one rubric bullet explaining that its `roles`/`themes` choose the calibration ladders — intended to encourage *complete* tagging — read as pressure to add tags: Sol Ring went from one correct theme (`Big Mana`) to five, then seven, across two rewordings of it. Deleting the bullet restored the single correct theme. The bullet is gone; pass one's rubric says nothing about pass two.

*The narrower rule:* telling a model that a field has downstream consequences is not neutral information, it is emphasis, and emphasis on a list field reads as "produce more of these." The existing rubric already said to leave a list empty rather than force an inexact match, and that instruction worked better alone than with a justification attached to it.

Worth separating because the two have different fixes and different evidence. The first was a context problem, provable by varying the context and nothing else. The second is not, and the thing that would settle it is the hand-labeled ground truth the benchmark is still missing (`benchmarking-and-testing.md#what-the-harness-cannot-decide-yet`) — without it there is no way to tell a model that under-rates bad cards from a scale whose bottom genuinely is empty for this corpus.


---

## `game_stage` was unanchored, and the two tiers failed it in opposite directions

**2026-10-23.** The previous entry closed by noting that `power_rating`'s inputs are themselves unanchored — "an anchored final number computed from unanchored premises inherits their error". This is what measuring that premise found.

**The metric, and why it needed no ground truth.** `game_stage.early` asks when a card first does something. A card you cannot cast until turn seven is not doing much on turn two, so `early` should track mana value *downward* — which makes it the rare metadata field with a free, objective check. Computed over S-001's 60 rows:

| | `corr(mana value, early)` |
|---|---|
| all rows pooled | −0.13 |
| plain-cost rows only | **−0.03** |
| `qwen2.5-coder:7b` alone, plain-cost | **−0.37** |
| `qwen2.5-coder:14b` alone, plain-cost | **+0.41** |

**Pooling hid the defect twice over, in two different ways.** The first pooling was across cost-exception cards; removing them made the number *worse* (−0.13 → −0.03), which was the opposite of the expected direction and the first sign something was wrong. The second pooling was across tiers, and it was hiding a sign flip:

| mana value | 7B `early` | 14B `early` |
|---|---|---|
| 1 | 6.3 | 6.0 |
| 3 | 3.9 | 5.9 |
| 7 | **3.0** | **8.0** |

The 7B declines correctly. **The 14B is inverted** — it rates a seven-drop as doing *more* on turn two than a one-drop. Averaged together, a correct negative and an inverted positive very nearly cancel, and the pooled figure reads as "no relationship" rather than "one tier has it backwards."

*The narrower rule, and it is the same shape as the throughput error recorded above:* an aggregate over groups that may differ in **sign** is not a weak version of the per-group number, it is a different and potentially meaningless number. Averaging magnitudes loses precision; averaging across a sign flip loses the finding. Disaggregate before concluding "no effect" — a null result is exactly where cancellation is most likely and least visible.

**Two causes, and only one of them was the anchors.**

**1. The scale had no stated meaning.** Neither the rubric nor `data-model.md` ever said what "early" was in turns. "How much is this card doing in that phase of a Commander game" is not answerable without that, and the models supplied their own reading. Early/mid/late are now defined as turns 1–4 / 5–8 / 9+, in the prompt.

**2. Mana value is not cost to function for 19.7% of the corpus** (6,278 of 31,830 Commander-legal cards), and no deterministic signal said which. That is now `knowledge_pipeline/cost_profile.py`, classified by *direction of error* rather than by keyword — a keyword list gets two important cases backwards, since `Delve` pays with a graveyard the early game does not have and `{X}` is counted as zero, so both are *later* than their mana value suggests rather than cheaper.

Three things that surfaced only by building it, each of which would have been silent:

- **Reminder text spoofs the oracle patterns.** Cascade and Suspend both *describe* casting something "without paying its mana cost" in their parenthesised reminder text. Matched against raw oracle text, the free-cast pattern fired on 543 cards; with reminder text stripped, 312. Bloodbraid Elf was being told its own cost was discounted. Reminder text is now removed before any pattern runs.
- **A name heuristic misclassified 388 werewolves as split cards.** Both layouts print `A // B` as the name. The mana cost separates them exactly — split 123/123, adventure 146/151, transform 0/388 — and that is what the prompt-side classifier keys on now.
- **"Starts with `(`" is not the same as "is a placeholder."** The renderer prints `(none)` for a card with no mana cost, so the parser treats a leading parenthesis as absence — which silently discarded the entire rules text of every Class and Saga, because plenty of real oracle text *opens* with reminder text. The test is now "one parenthesised line and nothing else."

**The anchors themselves are one global set, not one per tag** — 14 profiles, against `power_rating`'s 66 ladders and 330 rungs. `power_rating` needs a ladder per tag because "good" is only meaningful inside a category; `game_stage` asks where on the game's clock a card works, and that clock is the same for every card in the format. A per-tag ladder would teach the opposite. The payoff beyond size is that a card-independent set lives in pass one's *fixed* block, so it has no selection step and therefore cannot be mis-selected by a bad tag — the failure the two-pass split introduced for `power_rating` does not exist here.

**Writing the set reproduced the bias it was built to remove.** The first draft was nine rising profiles out of thirteen. The honest curve for most *cost-exception* cards is back-loaded — equip costs, `{X}`, Delve and suspend all delay a card — so a set chosen to teach the cost rule drifts, by construction, toward demonstrating the monotonic ramp that half of all generated profiles already had. Balancing it meant deliberately adding front-loaded plain cards (Lightning Bolt, Duress). `tests/test_anchors.py` now asserts the shape balance, because the set is a worked example and a worked example teaches whatever it happens to contain.

**A second set means a second thing the sample must exclude, and nothing connected the two.** `sample.anchor_card_names()` is what keeps anchor cards out of the 300-card benchmark draw, but the sample is a frozen file drawn once — widening the exclusion does not re-filter it. `Argentum Armor` landed in both, which would have scored a model on a card whose answer was printed verbatim in its own system prompt. A test now asserts the shipped sample and the calibration sets are disjoint; the anchor was swapped for `Colossus Hammer`, which is a better teacher anyway at mana value 1 and Equip {8}.

**What is not yet measured, and why.** The benchmark runner still generates in **one pass** against the frozen `build_system_prompt`. The cost note reaches it (it rides in `render_card_facts`), but the rubric change and the `game_stage` profiles live in `build_pass_one_system_prompt` and do not. So the numbers above are a baseline and a diagnosis, not a before/after — closing that needs the harness migrated to two-pass generation, which also needs the backends to take an output schema per call rather than hardcoding `CardMetadataBlueprint`.

---

## `primary_role`, and a ceiling that was never low enough to bind

**2026-10-24.** The two de-risking items left open by *One call per card → two*: rank the ladder cap by something the model was actually asked for, and stop the over-tagging that makes the cap bite. Both were done in one change. They did not pay out equally, and the measurement is the interesting part.

### What changed

`CardMetadata` gains **`primary_role`** — the one `Role` the card is most defined by, written in pass one directly after `roles` and before `themes`, so the model commits while the list it is choosing from is still the last thing it wrote. `MetadataPassOne.ladder_tags()` then orders the ladder selection `primary_role` → other roles → themes, and pass two's rubric tells the model the first ladder shown is the primary's. `roles` and `themes` came down from a ceiling of 8 to **5** (`schema.MAX_TAGS_PER_CARD`).

### The ceiling paid out, and not in the way a ceiling is supposed to

A `maxItems` ceiling is normally a backstop: it clips a tail and leaves the distribution alone. This one did not.

| `qwen2.5:14b`, 8 hard cards | ceiling 8 | ceiling 5 |
|---|---|---|
| roles mean | 2.9 | **2.0** |
| roles max | **8** | 4 |
| "Lost Jitte" roles | **8** | 3 |

The mean fell by nearly a third and the worst card fell from eight roles to three — far below the new ceiling, which never bound at all on these cards. That is not clipping. `maxItems` reaches the model as a **decoding grammar it generates under**, not as a filter applied to finished output, so lowering it changes what the model produces everywhere in the range, not only above the line. A ceiling set "well above any real answer so it can never truncate a good one" is therefore not the conservative choice it looks like — 8 was loose enough that the inflation fit comfortably underneath it and never tripped it once.

*The transferable rule:* **under constrained decoding, a schema bound is a prior, not a filter.** Set it to the honest maximum, not to a safe-looking multiple of it; the slack is not free, and its cost is invisible because nothing ever reports a bound that did not fire.

### `primary_role` bought almost nothing on the local tiers, and that is the finding

The field works: 30/30 cards on the 7B and 8/8 on the 14B named one, no nulls, no refusals. But it **agreed with the model's own first-emitted role on 28 of 30** (7B) and **8 of 8** (14B). Where it disagreed — *The Horus Heresy* (Mass Pump over Card Draw) and *Nicol Bolas, the Deceiver* (Alternate Win Condition over Removal) — both corrections were right, and in both cases **the same three ladders were selected and only their order changed.** Not one card in the sample had a ladder cut off by the cap that naming a primary would have rescued.

So the premise the open issue was written on — that emission order is an unreliable proxy for importance — is not visibly true of these two models. They put their strongest read first, consistently, without being asked. The field is still worth having: it makes the ranking explicit rather than inferred, it is a real stored column a reader can group by instead of a convention that position zero is privileged, it is what pass two is told to calibrate against, and the frontier tier that production actually routes to is the model most likely to distinguish the two. But it is insurance, not a measured improvement, and the change should not be written up as one.

*The narrower rule:* an inferred signal and an explicit one agreeing is a result, and it is worth the hour it costs to find out. The fix for a suspected bad proxy is to measure the proxy against the thing it stands for — not to replace it and assume the replacement helped.

### And the rubric bullet cost something, as the last one did

Asking for the field needs a rubric entry, and rubric entries about tagging have a record here. This one inflated `themes` on the 7B: **2.7 → 3.6 mean** across the same 30 cards, with the bullet as the only variable (the schema field was present in both arms, so the model answered it either way — from an unexplained enum in the control, and it still answered `roles[0]` every time). Roles were unmoved at 2.2 in both. An 8-card pilot had read as `+0.4 roles`; at 30 cards that vanished and the theme effect appeared, which is the second time this week a reading taken at eight cards has not survived thirty.

The bullet is **kept**, because without it the field degenerates into a duplicate of `roles[0]` and the 2/30 disagreements — the only thing it buys — disappear entirely (0/30 in the control arm). The trade is explicit: ~+0.9 themes per card on the weak tier, for a ranking the model was actually asked to make. Theme inflation is also the cheaper error to carry here, since roles fill the three ladder slots first and themes rarely reach the calibration at all.

*The rule, now confirmed twice:* **a rubric bullet about a tag field raises the count of tags near it.** The first time it was the bullet explaining downstream consequences, and it was deleted. This one is paid for rather than free, and the entry says what it cost.

### What this does not fix

Neither half touches the open issue above it. The 7B still floors at 3, the unanchored `game_stage` premises still feed pass two, and nothing here was measured against ground truth, because there still is none. Re-running the `power_rating` distribution is the next measurement and it is not in this change.

## The harness measured a prompt production had stopped using

**What changed:** the metadata benchmark made one model call per card for its first three sessions, while production had moved to two. Converting it (schema version 3: a `pass_name` on every run, two fingerprinted system blocks per session, and the output schemas fingerprinted beside them) is what this entry is about — and so is the fact that three sessions were recorded in the meantime whose prompt-shaped findings measure nothing production does.

**Why it took three sessions to matter.** The gap was known and written down as a tracked follow-up, on the reasoning that the one-call prompt was "deliberately kept byte-identical" so the harness stayed a stable baseline. That reasoning quietly stopped being true. The retired one-call prompt still carried **all 66 anchor ladders**, which is the exact defect splitting the call existed to remove, and it carried **none** of `game_stage`'s turn ranges or its 14 reference profiles, which live only in pass one's block. So:

- Its `power_rating` reproduced the flat band around 7 that the split was built to fix — 26 of 30 cards on exactly 7 for the 7B in S-003, means of 7.07 / 7.13 / 7.20 / 7.00 / 7.10 / 7.13 across three sessions, two model families and three vocabularies. That is a clean finding about *the one-pass prompt* and tells you nothing about the shipped path, whose own distribution (5.57 mean, range 3-7) had to be measured by a throwaway script instead.
- Its `game_stage` was scored by `castability r` against a field the prompt never defines. The metric assumes `early` means "can you deploy it", which is pass one's rubric; the one-call prompt instead asks "how much is the card doing in that phase", which is a question about impact. The 14B answered the asked question consistently — `early` pinned near 6 regardless of cost — and the metric read that as failure, while the 7B's cruder "cheap card, high early" heuristic scored *better*. **A proxy metric silently graded the two tiers on which one ignored the prompt more usefully.**

**The lesson, and it is not "convert the harness sooner".** It is that *"the baseline is frozen"* is a claim with a shelf life. A frozen artifact stays comparable to itself and drifts away from the thing it was a proxy for, and nothing in the harness could report that drift, because every check it had was about internal consistency: same cards, same prompt hash, same decoding knobs. There was no check for *is this prompt the one production sends*. The conversion adds none either — what it does instead is remove the second prompt, so the question cannot be asked wrong.

**A second hazard, caught being real rather than predicted.** The store's comparability key was `system_prompt_sha256` and nothing else, and a run is determined by the prompt *and the output schema*, because under constrained decoding the schema becomes the grammar. S-002 and S-003 share a prompt **byte for byte** and differ only in whether the tag fields were `required` — which was the difference between a tier emitting `roles` on every card and on none. A `run --resume` across them would have been accepted. `output_schemas_sha256` now sits beside the prompt fingerprint, and both are checked on resume.

**What the conversion cost, concretely.** One row per `(card, tier)` became one per `(card, tier, pass)`, so `recorded_cells`/`missing_cells` key on the triple — without the pass in the key, a card whose rating call was interrupted reads as finished and resume skips the call that is actually missing. `parsed_output` stopped being a whole blueprint, which made every card-level view in `report.py` wrong until `merge_passes` was put in front of them; the merge is an explicit step at the CLI boundary rather than something `build_report` does to its input, so the aggregates can state their unit as "a card's answer" and mean it. And one view deliberately does *not* merge: throughput, because a pass-one call prefills ~2,100 tokens to generate seven fields while a pass-two call prefills ~1,400 to generate two, and a blended rate across them is the same category of mistake as blending prefill into decode.

**An unplanned win.** `DEFAULT_NUM_CTX` had climbed to 16,384 to fit the one-call prompt's ~14,100 tokens. The two passes need under 3,500, so it came down to 8,192 — and the KV cache is VRAM. At 16k the 14B sat at 11.5 GB of 12.4 GB, which is the condition under which the driver evicted ~1 GB mid-session and dropped its decode rate to 2.7 tok/s, costing S-002 its entire latency column. At 8,192 the same model on the same machine measures **37 tok/s** — above the 30 tok/s previously recorded as its uncontended rate. The contention finding above was correct about the mechanism and wrong to treat 16,384 as a fixed cost.

**The sessions were archived, not migrated.** `benchmark-v2-sessions-001-003.db`, tracked in git beside the live store, readable only with the version-2 code from git history. The store has no migration path by design, and that bill comes due at every schema bump; what makes it payable is writing the conclusions into the docs rather than leaving them in the file. Twice now.
