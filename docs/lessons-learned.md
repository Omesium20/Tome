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

**What replaced it:** a 300-card hand-labeled benchmark, run before any full-corpus generation, that decides whether a local tier is viable at all, what triggers escalation to frontier, and what fraction of the real corpus that escalation rule would route to frontier. Full design: `benchmarking-and-testing.md`. Not yet run as of this writing.

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

**What replaced it:** all 51 ladders, 255 distinct cards. Candidates were proposed by corpus search (four per rung, each resolved against the `cards` table so no rung is a recollection), ranked mechanically, then chosen by hand.

**What the numbers say about that process:** the ranking left the top two candidates **exactly tied in 101 of 260 rungs**, and within 0.75 in 204. It reliably separates a clearly-worse candidate from the rest and rarely picks a winner among near-equals. That is the honest shape of the problem, not a defect in the scoring — which is why the selection is a human pass with machine triage, and why nothing was auto-applied.

**Two biases found in the selection machinery, both worth remembering because both favoured the newer work:**

- **An "obscurity bonus" that only new candidates could earn.** The brief argued that a low-profile card is often a *better* ruler than a famous one, because the model cannot substitute reputation for applying the rubric — so the scorer awarded a point for it. But only the second search pass recorded a `profile` field; the 500 earlier picks had none, so the signal was a structural one-point handicap on every older candidate. It moved the recommendation split from 163 new / 97 old to **129 / 131** once removed. A signal that one group cannot physically earn is not a signal, it is a thumb on the scale — check that every candidate *can* score on a criterion before weighting it.
- **A too-literal text test on the heaviest-weighted signal.** Tag membership was checked by matching each ladder's own SQL `LIKE` pattern as a substring, which scored *Cultivate* as having no evidence of being Ramp: the predicate says "basic land card" and the card says "basic land cards". Content-word overlap cut false negatives from 380 to 225. On the signal that carries the most weight, a false negative is worse than a loose match.

**Two whole-set rules the ladders obey, now enforced by tests rather than by comment** (`tests/test_anchors.py`): no card carries two different ratings across ladders, and no card anchors two ladders. The first is the cross-file check the previous entry said any future split of this work would need. The second is weaker but real — a shared rung is one fewer independent reference point, and on the `Role`/`Theme` pairs that share a word (Lifegain, Equipment) the same card on both sides anchors both halves of the distinction those tags exist to draw. Enforcing the second turned up three rungs whose *axis* was wrong as well: `Theme.SACRIFICE`'s top rung graded a payoff on a ladder that measures outlets, and `Theme.LIFEGAIN`'s graded a card that gains life on a ladder that measures cards triggered *by* gaining it.
