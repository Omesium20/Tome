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
