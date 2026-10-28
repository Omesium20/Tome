# CLAUDE.md

Guidance for Claude Code when working in this repository. Full requirements live in `PRD.md`; deeper technical detail lives in `docs/` (linked below). Keep this file itself short — it's a map, not the territory.

---
## Important

- **Keep this document updated.** Whenever a change introduces or alters an architectural or design decision — a new pipeline step, a changed data model field, a new service boundary, a stack swap — update `CLAUDE.md` and the relevant file in `docs/` as part of that same change, not as follow-up cleanup.
- **Keep `CLAUDE.md` itself under ~200 lines.** It should stay a high-level map: overview, stack, commands, and pointers. If something needs more than a few lines of explanation, it belongs in its own file under `docs/` with a link from here — don't let detail accumulate in this file.
- **Keep the Table of Contents below in sync.** Any time a doc file is added, removed, or gets a new/renamed top-level topic (a `##` heading), update its row in the table below in the same change — this table is how Claude decides which file to open, so a stale row sends future Claude down the wrong path.
- `PRD.md` is the source of truth for product scope and requirements; `docs/architecture.md` and `docs/data-model.md` are source of truth for technical design. If code and docs disagree, treat that as a bug to fix, not ambiguity to route around.
- Claude must never take on the backend's responsibilities (rule validation, legality checks, collection tracking, DB search) — those live in code per `docs/architecture.md`, not in prompts.
- Make sure that when making code changes for a particular tool that you are using official documentation whenever you are writing code. Use the Context7 MCP if possible. I want you to be able to cite your sources from context when prompted.


## Overview

Tome is an AI-powered Magic: The Gathering **Commander deck-building assistant**. A user selects one or more cards they want to build around (e.g. `Hardened Scales`, `The Ozolith`), and the app:

1. Uses a model to understand the strategy behind those cards.
2. Retrieves synergistic candidate cards from a curated, embedded card knowledge base.
3. Has the model construct a complete, legal 100-card Commander deck from those candidates.
4. Validates the deck against Commander rules in code (not trusted to the model).
5. Surfaces which recommended cards the user already owns vs. needs to acquire (proxy-friendly).

**Tome runs as two planes** (full rationale: `docs/architecture.md`):

- **Knowledge plane** — hosted by maintainers. The Knowledge Pipeline builds card analysis and embeddings **once, centrally**, into a cloud Postgres with `pgvector`; a thin read-only **Knowledge API** serves it to every client. One card corpus, updated in one place, identical for all users.
- **Client plane** — runs on the user's machine. Frontend, local FastAPI, the Deck Generation Pipeline, and a local database holding only that user's collection and decks. Generation goes through a **model provider interface**, so a user can point it at a local model or a frontier API.

The line between them: the client asks the Knowledge API about *cards*, never about *the user*. Collections and decks never leave the machine.

**MVP scope:** Commander format only, CSV collection import, AI deck generation with configuration questions (power level, collection preference), deck validation, and deck explanation.

**Explicitly out of scope for MVP:** marketplace/pricing, card purchasing, competitive tournament optimization, other formats, social features, trading, deck sharing, full collection-scanning integrations (e.g. live ManaBox sync).

The guiding design principle: **structured data first, AI reasoning second.** The Knowledge Pipeline builds reusable, normalized understanding of every card; the Deck Generation Pipeline uses that understanding to make strategic decisions. The model is the expert deck builder; the backend owns retrieval, validation, and correctness.

That division is now load-bearing rather than merely tidy: because validation is deterministic local code, **correctness doesn't depend on model quality — only deck quality does.** A weak local model yields a blander deck or more repair rounds, never an illegal one. That is what makes "bring any model" a safe offer.

## Table of Contents

Which doc to open for a given topic. `PRD.md`, `docs/architecture.md`, and `docs/data-model.md` are sources of truth; the rest hold implementation-level detail once a stage is built.

| Doc | Topics |
|---|---|
| [docs/PRD.md](docs/PRD.md) | product scope & MVP boundaries · user flow (import → build-around → config questions → generate) · **deployment model (knowledge vs. client plane)** · both pipelines end-to-end · tech stack rationale · full normalized knowledge model · knowledge documents · knowledge storage structure · model responsibilities · success criteria · future enhancements |
| [docs/architecture.md](docs/architecture.md) | **the knowledge/client plane split & why** · system diagram · Knowledge Pipeline stages · why pgvector not ChromaDB · Deck Generation Pipeline stages · model vs. backend division of responsibility · the two-database boundary & logical refs · project/module structure |
| [docs/data-model.md](docs/data-model.md) | which entities live in which database · entity schemas — Card, CardMetadata, CardDocument, ImportRun (cloud) · Collection, Deck, DeckCard, CardCache (local) · **`CardMetadata.mechanics` — a literal restatement of the rules text, generated *first* so the model reads the card before it judges it** · **the two generation passes and where the reasoning chain is cut** · **`primary_role`, and which of a card's ladders survive the cap of three** · **CardMetadata's closed role/theme vocabulary, `GameStageProfile`, and anchor-card calibration — how the 66 shipped ladders were chosen, which of the 330 rungs a human actually picked, and why a card is now shown at most three of them rather than all 66** · **`game_stage`'s own calibration — turns 1-4/5-8/9+, `early` scored on cost to function rather than mana value, and why its 14 anchor profiles are one global set where `power_rating` needs 66 ladders** |
| [docs/knowledge-api.md](docs/knowledge-api.md) | the hosted read service — why a service not a connection string · endpoints (`/meta`, `/cards`, `/cards/resolve`, `/retrieve`) · filters as hard constraints · versioning & client compatibility · caching/degradation rules for clients · operating it |
| [docs/model-providers.md](docs/model-providers.md) | the `ModelProvider` interface · supported backends (Anthropic, OpenAI-compatible, Ollama) · Anthropic API specifics · capability floor & what weak models actually break · the validation repair loop · config vars · testing with a fake provider |
| [docs/knowledge-pipeline.md](docs/knowledge-pipeline.md) | who runs the pipeline & against which database · Scryfall Importer — module layout & CLI · **why the import takes every card and has no format flags** · what still gets excluded · pool sizes per format · bulk data API · card schema → `Card` field mapping · card faces/DFCs & the merge rule · legalities · upsert semantics · `--reset` and its confirmation · rate limits · metadata generation: module layout, **the two calls per card and why the anchor ladders are selected per card rather than all rendered every time**, **the five `Theme` members derived from the card itself (`derived_tags.py`) rather than asked of the model**, why it's **single-tier today**, and the `MetadataModelBackend` / `ObservableMetadataBackend` seam the local tier plugs into |
| [docs/frontend.md](docs/frontend.md) | routing & app shell · mock backend layer & `VITE_USE_MOCKS` switch · working-deck vs. saved-deck handoff · shared collection filter logic/UI · drag-and-drop contract · design tokens · dev gotchas |
| [docs/self-hosting.md](docs/self-hosting.md) | running the client — Docker vs. local setup · choosing a model (local vs. frontier) · bring-your-own database · full env var reference · running your own knowledge plane & what it costs · choosing a card pool · refreshing card data · upgrading · troubleshooting |
| [docs/benchmarking-and-testing.md](docs/benchmarking-and-testing.md) | **harness built & runnable; full run still gated on ground truth + the frontier tier** · the 300-card sample (easy/medium/hard) that gates tiered metadata generation, and **how it's selected** · hand-labeled ground truth & anchor-card sourcing · local (Qwen 7B/14B via Ollama) vs. frontier comparison · **paired sessions: one frozen card set through every tier, a required `--bucket`, and the resume/pairing guards that make a comparison like-for-like** · human-reviewed scoring, why `schema_valid` is **enforced not measured** under constrained decoding, and **the per-card pairwise pick (plus its Wilson interval and projected corpus hours) that scores a session without ground truth** · the standalone SQLite store — `benchmark_sessions` / `benchmark_runs` / `benchmark_pairs`, and the `user_version` refusal that keeps "delete the file" honest · **running it: `sample`/`run --bucket`/`sessions`/`pair-verdict`, GGUF-vs-Ollama model names, and the four operational facts — the `num_ctx` truncation trap, phase-separated throughput, VRAM contention, and the wall-clock timing guard** · **the two-pass conversion: a `pass_name` on every run, two fingerprinted system blocks plus the output schemas beside them, `merge_passes` for every card-level view and the per-pass table that must not be merged, and what S-001 to S-003 are evidence for now that they are known to have measured the retired one-call prompt** · **`castability r` — the one accuracy metric that needs no ground truth, why it is computed over plain-cost cards only, and why it must be read per tier (pooled it read -0.03; per tier, -0.37 and +0.41)** · what decides the local/frontier escalation rule and production split |
| [docs/anchor-recalibration.md](docs/anchor-recalibration.md) | the maintainer runbook for the anchor ladders — when a recalibration is needed · what makes a card a good *ruler mark* rather than a good card · the ladder's **axis comment** and why a fine card can be a bad rung · the two whole-set rules · **the two gates: `verify_anchors` (the cards are real and Commander-legal) and `test_anchors.py` (the set is coherent)** · **the second set: `game_stage` profiles, their shape-balance rule, and why widening a calibration set does not re-filter the frozen benchmark sample** · prompt size as a budget the vocabulary spends · what else to update once the ladders are in |
| [docs/lessons-learned.md](docs/lessons-learned.md) | decisions that were tried, scoped, or built one way and later changed, and why — the two-plane redesign, ChromaDB → pgvector, LangChain removal, format-scoped → whole-pool importer, direct SDK → `ModelProvider`, frontier-only → tiered metadata generation, **one anchor card → a five-rung ladder (and the `StrEnum` key collision it surfaced)**, **`Theme.MIDRANGE` dropped rather than anchored**, **`Theme.FLYING` dropped for being unanchorable rather than undecidable**, **anchors empty → populated and the two biases found in the selection machinery**, **the review step not surviving 330 rungs — 149 picks delegated to a scorer documented as triage, 48 of them exact ties settled by a tie-break built for reproducibility, and the prompt-size budget a growing vocabulary spends**, **the guards learned building the local-model benchmark (a context default that truncates the anchors, a throughput metric that invented the GPU fault it was built to detect — recorded as a correction, since it shipped the wrong conclusion first, a stratified draw that couldn't reach the end of the alphabet, and a `num_ctx` ceiling check that passed vacuously from the day it was written because it read `context_length` off the one endpoint that never returns it)**, **one pooled run log → paired sessions: a smoke-test flag that silently made every recorded row easy-only, two tiers that never covered the same cards, and why those 45 measured rows were discarded rather than migrated**, **`anchor_bench` deleted: ~2,800 lines of pool/rank/review scaffolding for a human pass that had stopped happening, and the two deterministic gates kept in its place**, **one call per card → two: all 66 anchor ladders in every prompt flattened `power_rating` to a 7.07 mean with nothing below 4, and the ladders a card is shown are now selected in code from the tags it was just given**, **the repetition loop that removing them exposed — an unbounded `list[enum]` is a latent infinite loop under constrained decoding, masked for as long as the prompt was big**, **the half of the problem left open: restricting context did not make the model willing to use the bottom of the scale**, **the harness measuring a prompt production had stopped using — "the baseline is frozen" as a claim with a shelf life, a proxy metric grading two tiers on which ignored the prompt more usefully, a comparability key that omitted the decoding grammar and was caught by two sessions differing only in a `required` list, and `num_ctx` coming down from 16,384 to 8,192 as an unplanned 2.7 → 37 tok/s**, **and `primary_role` — a schema ceiling is a prior and not a filter (8 → 5 took the 14B's worst card from eight roles to three, below a bound that then never fired), an explicit ranking that agreed with emission order on 36 of 38 cards, and a rubric bullet whose cost was paid rather than free** |

---

## Stack

| Layer | Technology | Plane | Responsibility |
|---|---|---|---|
| Frontend | Vite, React, React Router, TypeScript, Tailwind CSS | client | UI, collection management, card selection, deck display |
| Local backend | Python, FastAPI | client | API endpoints, deck pipeline orchestration, deck validation, collection management |
| Model providers | Anthropic SDK · OpenAI-compatible · Ollama, behind one interface | client | Deck strategy, commander selection, deck construction, explanations — user's choice of local or frontier |
| Local database | SQLite (default) or Postgres | client | The user's collection, decks, and card cache. Never leaves the machine |
| Knowledge API | Python, FastAPI (read-only) | hosted | Card lookup, name resolution, candidate retrieval; the only client-facing entry to the corpus |
| Knowledge database | Cloud Postgres + `pgvector` | hosted | Cards, AI metadata, knowledge documents, embeddings — one shared corpus, similarity + filters in one query |
| Embedding model | Hugging Face Sentence Transformer | hosted | Embeds knowledge documents and incoming retrieval queries |
| Card data source | Scryfall Bulk Data API | hosted | Card info, oracle text, mana cost, color identity, types, legalities |

See `docs/architecture.md#why-pgvector-and-not-chromadb` and `docs/model-providers.md` for why `pgvector` and the `ModelProvider` interface were chosen; `docs/lessons-learned.md` covers what they replaced.

---

## Development Commands

Both sides are scaffolded. The frontend is a working UI running against a mock backend layer (see `docs/frontend.md`); the backend has real module structure but its route handlers and pipeline steps are still `NotImplementedError` stubs.

> **Status: the data layer is split; the services on top of it are not built yet.** What exists today: the Scryfall importer, the metadata generator (single-tier, **two model calls per card** — `docs/knowledge-pipeline.md#two-calls-per-card`) with its anchor registry **fully populated against the revised vocabulary** (66 ladders / 330 cards in `metadata_generator/anchors.py`, so `power_rating` has a stated meaning for every `Role`/`Theme` member — but see `docs/data-model.md#anchor-cards` for which of those rungs a human actually chose, and `#which-ladder-a-card-is-shown` for how `primary_role` picks the three a given card is calibrated against), the `database/knowledge/` and `database/local/` packages, per-plane settings (`KnowledgeSettings` / `LocalSettings`, no combined accessor), the two Alembic lineages (`-n local` / `-n knowledge`), the **metadata benchmark harness** (`knowledge_pipeline/benchmark/` — a three-table store, a tabbed HTML report, 300-card sample selection, and a runner driving the two local Qwen tiers through Ollama via `metadata_generator/ollama_backend.py` as one **paired session**: one frozen card set, every tier, in serial succession, **making production's two calls per card**), and `ai/claude_client.py` (a bare Anthropic handle for the client plane's future `ModelProvider`, unrelated to the metadata generator's own `AnthropicMetadataBackend`). Every command below is runnable.
>
> Not yet written: `knowledge_api/`, the `ModelProvider` interface and its providers, the `card_documents` table and `pgvector` setup, `CardCache`, and the collection/deck service layer — so the local database currently has a schema and no readers. Treat a mismatch between these docs and the code as work to do, not as a doc bug.

**Frontend (`frontend/`, Vite + React SPA)** — all verified working:
```
npm install
npm run dev       # local dev server on :3000
npm run build     # type-check + production build to dist/
npm run start     # preview the production build
npm run lint
npm test          # vitest (jsdom)
npx tsc --noEmit  # type-check only
```

**Backend (`backend/`, FastAPI)** — run from `backend/` (imports and `pytest.ini` assume it as root; a `.venv` lives there):
```
pip install -r requirements.txt
pytest                          # test suite
alembic -n local upgrade head   # the user's own DB — required before first run
alembic -n knowledge upgrade head                    # the shared corpus (maintainers only)
alembic -n local revision --autogenerate -m "..."    # after editing database/local/models.py
alembic -n knowledge revision --autogenerate -m "..." # after editing database/knowledge/models.py
```

There is no unnamed `[alembic]` section in `alembic.ini` — a bare `alembic upgrade head` fails rather than guessing a plane. Autogenerate against an **empty** database; pointed at one that already holds the other plane's tables it will emit drops instead of creates.

**The two databases migrate independently** — `-n local` against the user's machine, `-n knowledge` against the cloud Postgres. Picking the wrong one either fails or writes user-visible card data from a laptop. A client upgrade must never require a knowledge migration to land first; that's what the Knowledge API's versioning is for.

All settings are read through `backend/config.py` (pydantic-settings), which loads `backend/.env` itself — so `python -m ...` entry points get the same config the API does. Never read `os.environ` directly in new code; add a field to the right settings class instead — `KnowledgeSettings` or `LocalSettings`, never both. There is deliberately no combined `get_settings()`: a caller names the plane it wants (`get_knowledge_settings()` / `get_local_settings()`), or uses `get_base_settings()` for the handful of values that belong to neither database.

Dev servers — `fastapi dev` takes a direct file path, so run it from the module's own directory:
```
cd backend/api            && fastapi dev main.py   # client backend on :8000 (routes are stubs)
cd backend/knowledge_api  && fastapi dev main.py   # knowledge read service on :8001
```

**Knowledge Pipeline (offline, maintainers only)** — from `backend/`, writing to `KNOWLEDGE_DATABASE_URL`. Steps 1–2 are implemented (step 2 single-tier and **two calls per card**, pending the escalation benchmark — `docs/benchmarking-and-testing.md`); steps 3–4 are still stubs:
```
python -m knowledge_pipeline.scryfall_importer                        # the whole card pool
python -m knowledge_pipeline.scryfall_importer --if-newer             # weekly refresh
python -m knowledge_pipeline.scryfall_importer --dry-run --limit 500  # no writes
python -m knowledge_pipeline.scryfall_importer --reset                # start over (destructive)

python -m knowledge_pipeline.metadata_generator                       # commander-legal cards needing metadata
python -m knowledge_pipeline.metadata_generator --dry-run --limit 20  # smoke-test prompts; still calls the API
python -m knowledge_pipeline.metadata_generator.verify_anchors        # every rung and game_stage profile is a real, legal card
python -m knowledge_pipeline.document_generator   # stub
python -m knowledge_pipeline.embeddings           # stub
```

**One maintainer-only tool lives under `knowledge_pipeline/`, and it is not a pipeline stage.** It writes to neither database, and nothing in the pipeline imports it.

- `benchmark/` — the metadata benchmark (`docs/benchmarking-and-testing.md`). **A session is the unit:** one `run` draws `--cards N` from each `--bucket`, freezes that list before the first call, and puts every tier through it in serial succession — so the tiers are known to have answered the same cards. The pooled log it replaced could not show that, and didn't: it had the 7B on 15 easy cards and the 14B on 30 (`docs/lessons-learned.md`). Hence `--bucket` **required and repeatable**, `--cards` (default 30/bucket) in place of `--limit`, resume scoped to one session (`run --resume S-003`), and a `complete` status that requires every (card, tier, **pass**) cell — anything less is `partial`, which the report refuses to quote a win rate over. **Two calls per card, because production makes two** (`docs/benchmarking-and-testing.md#measuring-the-two-pass-path`): pass one, `derive_themes` merged in, then pass two against only the ladders pass one's tags selected. A pass one that fails its grammar gets no pass two — there is nothing to rate — so that card is simply missing from the comparison. `report.merge_passes` rebuilds a card's answer from its two rows for every view **except** the per-pass throughput table, which reads the raw rows on purpose: pass one prefills ~2,100 tokens for seven fields, pass two ~1,400 for two, and a blended rate describes neither. Store: one SQLite file, **three** tables (`benchmark_sessions` / `benchmark_runs` / `benchmark_pairs`), stdlib `sqlite3`, **no Alembic migration, no SQLAlchemy model, no settings**; it stamps `PRAGMA user_version` and refuses a file from an older schema rather than half-applying its `IF NOT EXISTS` DDL. A session's comparability key is **both** `system_prompts_sha256` (a map, one block per pass) and `output_schemas_sha256` — under constrained decoding the schema *is* the grammar, and the store was caught treating two sessions as comparable when only that had changed (`docs/lessons-learned.md`). `store.py`/`report.py` reach neither database nor model; only `sample.py`/`runner.py` touch `KNOWLEDGE_DATABASE_URL`, only `runner.py` calls a model — keep that split, since a reporting layer that can't reach a model can't score one model against another. Local tiers only; `--tier frontier` is refused, not silently skipped. Scoring is two judgments: the per-run verdict (`review`/`set-verdict`, "was this any good?") and the **per-card pairwise pick** (`pair-verdict`, "which tier was better?") — the one that works without ground truth, made in the report's session tab and copied out as JSON. Every win rate prints a Wilson interval and projected corpus hours beside it, because 30 cards is ±18 points. **Read the doc's *four operational facts* first:** `num_ctx` must fit the larger arm, which is pass two's *user* message (the ladders ride there), and Ollama's default of 4096 truncates it; judge throughput on decode rate alone, per pass; the 14B's rate drops with VRAM contention, which is why the default `num_ctx` is now 8192 rather than 16384 and why the same model measures 37 tok/s rather than 2.7; `latency_ms` is wall clock, so `timing_anomaly` discards a call the host slept through. `benchmark.db` and `sample.json` are **tracked in git**, carved out of the root `*.db` rule on purpose — the evidence and the exact cards stay reviewable; the system blocks live once per session, not per row, which is what keeps that file small. So is `benchmark-v2-sessions-001-003.db`: the schema bump retires a file rather than migrating it, and those three sessions measured the retired single-call prompt, so what they established is written into the docs and the file itself needs the version-2 code from git history to read. Both default to living beside the package (`sample.DEFAULT_SAMPLE_PATH`).

```
python -m knowledge_pipeline.benchmark    init | seed-demo | sample | run | sessions | report | review | set-verdict | pair-verdict

# One session = one card set through both tiers, one at a time (7B and 14B
# don't co-reside on a 12GB card), resumable, two calls per card. 30 hard
# cards measured ~2 min of 7B + ~5 min of 14B at num_ctx=8192; far longer if
# desktop apps hold VRAM.
python -m knowledge_pipeline.benchmark sample --out --seed 20261001
python -m knowledge_pipeline.benchmark run --bucket hard --cards 3 --dry-run  # no calls
python -m knowledge_pipeline.benchmark run --bucket hard --cards 30   # both tiers, same cards, 2 calls/card
python -m knowledge_pipeline.benchmark sessions                      # paired? complete?
python -m knowledge_pipeline.benchmark report --html out.html        # tab per session
```
Both print `--help` per subcommand; the docs above carry the arguments and the reasoning.

Three things to know before changing the importer:

- **Every card is imported, and there is no format flag.** One rule decides: is the object a card (`card_filter.py`)? Legality is never consulted, so the corpus holds all 34,831 cards including the 2,079 legal in no format at all — a collection is not a legal deck, and CSV import has to resolve cards people actually own. **Filter by format downstream** — at the AI stages, or on the client.
- **`Card`'s primary key is `oracle_id`, not Scryfall's `id`.** `id` is a printing UUID that rotates when a card is reprinted. `oracle_id` is the join key *across the plane boundary* — every local collection and deck row references it logically, with no foreign key able to enforce it — so a rotating key would orphan user data on machines we can't see. Imports remain idempotent upserts.
- **An import can add and update, never delete.** `sink.py` has no delete path and never queries `collection`/`decks` — those tables live on users' machines, not this one. `--reset` is the one destructive path: it prints the target database and card count, then requires that database's name typed back, and always refuses without a terminal. Gate it with credentials rather than the prompt — see `docs/knowledge-pipeline.md#re-running-is-safe`.

---

## Docker

Three containers (`frontend` nginx :3000 · `backend` FastAPI :8000 · `db` Postgres :5432), defined in `Dockercompose.yaml` at the repo root. Postgres data persists in the named volume `pgdata`; downloaded Scryfall snapshots persist in `scryfall_cache`.

**This compose stack is the client plane.** The `db` service holds the user's own collection and decks — not the card corpus, which lives in the hosted knowledge plane and is reached over HTTPS via `KNOWLEDGE_API_URL`. A normal setup has no card-import step; the `scryfall_cache` volume and the importer command below matter only if you run your own knowledge plane (`docs/self-hosting.md#running-your-own-knowledge-plane`).

The backend image runs `alembic -n local upgrade head` from its **`ENTRYPOINT`** before handing off to the command — the *local* schema only; the shared corpus is never migrated by a user's container. That's ENTRYPOINT rather than CMD on purpose: `Dockercompose.dev.yaml` overrides `command:`, and an override replaces CMD but not ENTRYPOINT, so dev containers stay migrated too.

**Two things to know before the first run:**
- The compose file is **not** a name Docker auto-discovers (it looks for `compose.yaml`/`docker-compose.yaml`), so **every command needs `-f Dockercompose.yaml`**.
- Copy `.env.example` → `.env` at the repo root first; compose reads the `POSTGRES_*` values from it.

```
# Normal start — keeps existing database data. The default day-to-day command.
docker compose -f Dockercompose.yaml up --build

# Clean start — destroys the pgdata volume first, for testing against an empty DB.
docker compose -f Dockercompose.yaml down -v && docker compose -f Dockercompose.yaml up --build

# Stop (containers removed, volume kept)
docker compose -f Dockercompose.yaml down

# Rebuild/run one service
docker compose -f Dockercompose.yaml build frontend
docker compose -f Dockercompose.yaml up -d backend

# Inspect
docker compose -f Dockercompose.yaml ps
docker compose -f Dockercompose.yaml logs -f backend
docker compose -f Dockercompose.yaml exec db psql -U tome -d tome
docker compose -f Dockercompose.yaml config --quiet   # validate the file; silence = valid

# Only when running your own knowledge plane: import the card corpus into
# KNOWLEDGE_DATABASE_URL. Safe without a terminal -- there is nothing to
# select, so the importer never prompts.
docker compose -f Dockercompose.yaml exec backend \
  python -m knowledge_pipeline.scryfall_importer
```

`VITE_*` values are **build args, not runtime env** — Vite inlines them at build time, so changing them requires `--build` to take effect (see `docs/frontend.md`). Compose sets `VITE_USE_MOCKS=true` for now, same as `npm run dev` — the real backend has no working `/collection` or `/decks` endpoints yet (no service layer, and router paths don't match the frontend's `/api/*` calls). Flip it to `"false"` once those are implemented. Note that the frontend's gateway (`src/lib/api.ts`) talks only to the **local** backend — the Knowledge API is the local backend's dependency, never the browser's.

**Dev mode (live reload in-container)** — the default containers above are production-style (`fastapi run`, a built static nginx bundle) so code edits need a rebuild to show up. `Dockercompose.dev.yaml` overrides both to their auto-reloading dev servers (`fastapi dev`, `vite`) with the source bind-mounted in, matching what `npm run dev`/`fastapi dev` already do outside Docker. It also adds an `adminer` service — a zero-config web GUI for the `db` service at `localhost:8080` (System: PostgreSQL, Server: `db`, credentials from `backend/.env`):

```
# Layer the dev override on top of the base file, base first:
docker compose -f Dockercompose.yaml -f Dockercompose.dev.yaml up --build

# Only needed again after a dependency change (requirements.txt/package.json) —
# code-only edits after that just need a save, same as local dev.
```

Windows bind mounts don't propagate native filesystem change events into the container, so both watchers are forced into polling mode (`WATCHFILES_FORCE_POLLING`/Vite's `usePolling`, gated behind `DOCKER_DEV` so local dev is unaffected) — otherwise saves would silently never trigger a reload.

---


