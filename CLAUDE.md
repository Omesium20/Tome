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
| [docs/data-model.md](docs/data-model.md) | which entities live in which database · entity schemas — Card, CardMetadata, CardDocument, ImportRun (cloud) · Collection, Deck, DeckCard, CardCache (local) |
| [docs/knowledge-api.md](docs/knowledge-api.md) | the hosted read service — why a service not a connection string · endpoints (`/meta`, `/cards`, `/cards/resolve`, `/retrieve`) · filters as hard constraints · versioning & client compatibility · caching/degradation rules for clients · operating it |
| [docs/model-providers.md](docs/model-providers.md) | the `ModelProvider` interface · supported backends (Anthropic, OpenAI-compatible, Ollama) · Anthropic API specifics · capability floor & what weak models actually break · the validation repair loop · config vars · testing with a fake provider |
| [docs/knowledge-pipeline.md](docs/knowledge-pipeline.md) | who runs the pipeline & against which database · Scryfall Importer — module layout & CLI · format profiles registry · why the import isn't format-scoped by default · pool sizes · bulk data API · card schema → `Card` field mapping · card faces/DFCs & the merge rule · legalities · upsert semantics · **why prune/reset lost their safety net** · rate limits |
| [docs/frontend.md](docs/frontend.md) | routing & app shell · mock backend layer & `VITE_USE_MOCKS` switch · working-deck vs. saved-deck handoff · shared collection filter logic/UI · drag-and-drop contract · design tokens · dev gotchas |
| [docs/self-hosting.md](docs/self-hosting.md) | running the client — Docker vs. local setup · choosing a model (local vs. frontier) · bring-your-own database · full env var reference · running your own knowledge plane & what it costs · choosing a card pool · refreshing card data · upgrading · troubleshooting |

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

**ChromaDB is no longer in the stack** — centralizing the knowledge base replaced it with `pgvector` in the same Postgres. **LangChain is no longer in the stack** either: with retrieval behind an HTTP contract and generation behind our own provider interface, it sat between two abstractions we already own. See `docs/architecture.md#why-pgvector-and-not-chromadb` and `docs/model-providers.md`.

---

## Development Commands

Both sides are scaffolded. The frontend is a working UI running against a mock backend layer (see `docs/frontend.md`); the backend has real module structure but its route handlers and pipeline steps are still `NotImplementedError` stubs.

> **Status: the two-plane split is designed, not yet built.** The docs describe the target architecture and are the spec to build toward. What exists today: the Scryfall importer, a single `database/models.py` with all entities in one schema, a single Alembic config, and `ai/claude_client.py` (a bare Anthropic handle). Not yet written: `knowledge_api/`, the `ModelProvider` interface and its providers, the `card_documents` table and `pgvector` setup, the `knowledge_models.py`/`local_models.py` split, the two Alembic configs (`-n local` / `-n knowledge`), and `CardCache`. Commands below that reference those are the intended shape, not currently runnable — treat a mismatch as work to do, not as a doc bug.

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
alembic -n local revision --autogenerate -m "..."    # after editing database/local_models.py
alembic -n knowledge revision --autogenerate -m "..." # after editing database/knowledge_models.py
```

**The two databases migrate independently** — `-n local` against the user's machine, `-n knowledge` against the cloud Postgres. Picking the wrong one either fails or writes user-visible card data from a laptop. A client upgrade must never require a knowledge migration to land first; that's what the Knowledge API's versioning is for.

All settings are read through `backend/config.py` (pydantic-settings), which loads `backend/.env` itself — so `python -m ...` entry points get the same config the API does. Never read `os.environ` directly in new code; add a field to `Settings` instead.

Dev servers — `fastapi dev` takes a direct file path, so run it from the module's own directory:
```
cd backend/api            && fastapi dev main.py   # client backend on :8000 (routes are stubs)
cd backend/knowledge_api  && fastapi dev main.py   # knowledge read service on :8001
```

**Knowledge Pipeline (offline, maintainers only)** — from `backend/`, writing to the **cloud** Postgres. Step 1 is implemented; the rest are still stubs:
```
python -m knowledge_pipeline.scryfall_importer            # interactive format picker
python -m knowledge_pipeline.scryfall_importer --format commander
python -m knowledge_pipeline.scryfall_importer --format all --if-newer   # weekly refresh
python -m knowledge_pipeline.scryfall_importer --dry-run --limit 500     # no writes
python -m knowledge_pipeline.scryfall_importer --format standard --prune # reclaim space
python -m knowledge_pipeline.scryfall_importer --format all --reset      # start over

python -m knowledge_pipeline.metadata_generator   # stub
python -m knowledge_pipeline.document_generator   # stub
python -m knowledge_pipeline.embeddings           # stub
```

Three things to know before changing the importer:

- **The import is deliberately not format-scoped by default.** Commander-legal cards are 96.5% of the entire card pool (31,830 of 32,988), so filtering the import saves nothing. The full corpus is imported with the complete `legalities` map, and format becomes a filter on the *expensive* stages — metadata generation (one model call per card) and embedding. `--format` on the import exists for constrained hosts and local development.
- **`Card`'s primary key is `oracle_id`, not Scryfall's `id`.** `id` is a printing UUID that rotates when a card is reprinted. `oracle_id` is now the join key *across the plane boundary* — every local collection and deck row references it logically, with no foreign key able to enforce it — so a rotating key would orphan user data on machines we can't see. Imports remain idempotent upserts.
- **`--prune` and `--reset` lost their safety net.** Both guards worked by seeing `collection`/`decks` in the same database; those tables are now on users' machines. Neither can tell what a deletion would orphan. Don't prune a shared corpus, and gate `--reset` with credentials rather than trusting its prompt — see `docs/knowledge-pipeline.md#re-running-is-safe`.

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
# KNOWLEDGE_DATABASE_URL. --format is required here -- there's no terminal to
# prompt on, so the importer exits rather than hanging.
docker compose -f Dockercompose.yaml exec backend \
  python -m knowledge_pipeline.scryfall_importer --format all
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


