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

1. Uses Claude to understand the strategy behind those cards.
2. Retrieves synergistic candidate cards from a curated, embedded card knowledge base.
3. Has Claude construct a complete, legal 100-card Commander deck from those candidates.
4. Validates the deck against Commander rules in code (not trusted to the model).
5. Surfaces which recommended cards the user already owns vs. needs to acquire (proxy-friendly).

**MVP scope:** Commander format only, CSV collection import, AI deck generation with configuration questions (power level, collection preference), deck validation, and deck explanation.

**Explicitly out of scope for MVP:** marketplace/pricing, card purchasing, competitive tournament optimization, other formats, social features, trading, deck sharing, full collection-scanning integrations (e.g. live ManaBox sync).

The guiding design principle: **structured data first, AI reasoning second.** The Knowledge Pipeline builds reusable, normalized understanding of every card; the Deck Generation Pipeline uses that understanding to make strategic decisions. Claude is the expert deck builder; the backend owns retrieval, validation, and correctness.

## Table of Contents

Which doc to open for a given topic. `PRD.md`, `docs/architecture.md`, and `docs/data-model.md` are sources of truth; the rest hold implementation-level detail once a stage is built.

| Doc | Topics |
|---|---|
| [docs/PRD.md](docs/PRD.md) | product scope & MVP boundaries · user flow (import → build-around → config questions → generate) · both pipelines end-to-end · tech stack rationale · full normalized knowledge model · knowledge documents · ChromaDB structure · Claude responsibilities · success criteria · future enhancements |
| [docs/architecture.md](docs/architecture.md) | system overview diagram · Knowledge Pipeline stages · Deck Generation Pipeline stages · Claude vs. backend division of responsibility · project/module structure |
| [docs/data-model.md](docs/data-model.md) | entity schemas — Card, CardMetadata, Collection, Deck, DeckCard |
| [docs/knowledge-pipeline.md](docs/knowledge-pipeline.md) | Scryfall Importer — module layout & CLI · format profiles registry · why the import isn't format-scoped by default · pool sizes · bulk data API · card schema → `Card` field mapping · card faces/DFCs & the merge rule · legalities · upsert/prune/reset semantics · rate limits |
| [docs/frontend.md](docs/frontend.md) | routing & app shell · mock backend layer & `VITE_USE_MOCKS` switch · working-deck vs. saved-deck handoff · shared collection filter logic/UI · drag-and-drop contract · design tokens · dev gotchas |
| [docs/self-hosting.md](docs/self-hosting.md) | running Tome on your own machine — Docker vs. local setup · bring-your-own database & API key · full env var reference · choosing a card pool · refreshing card data · pruning/resetting safely · upgrading · troubleshooting |

---

## Stack

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | Vite, React, React Router, TypeScript, Tailwind CSS | UI, collection management, card selection, deck display |
| Backend | Python, FastAPI | API endpoints, AI orchestration, deck validation, collection management, pipeline execution |
| AI framework | LangChain | Retrieval, prompt construction, Claude API management, output parsing |
| LLM | Anthropic Claude API | Card analysis, deck strategy, commander selection, deck construction, explanations |
| Embedding model | Hugging Face Sentence Transformer | Converts card knowledge documents into vectors |
| Vector database | ChromaDB | Stores embeddings, similarity search, candidate retrieval |
| Card data source | Scryfall Bulk Data API | Card info, oracle text, mana cost, color identity, types, legalities |

---

## Development Commands

Both sides are scaffolded. The frontend is a working UI running against a mock backend layer (see `docs/frontend.md`); the backend has real module structure but its route handlers and pipeline steps are still `NotImplementedError` stubs.

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
pytest                  # test suite
alembic upgrade head    # create/update the schema (required before first run)
alembic revision --autogenerate -m "what changed"   # after editing database/models.py
```

All settings are read through `backend/config.py` (pydantic-settings), which loads `backend/.env` itself — so `python -m ...` entry points get the same config the API does. Never read `os.environ` directly in new code; add a field to `Settings` instead.

Dev server — `fastapi dev` takes a direct file path, so run it from `backend/api/` instead:
```
cd backend/api
fastapi dev main.py   # local dev server on :8000 (routes are stubs)
```

**Knowledge Pipeline (offline)** — from `backend/`. Step 1 is implemented; the rest are still stubs:
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

Two things to know before changing the importer:

- **The import is deliberately not format-scoped by default.** Commander-legal cards are 96.5% of the entire card pool (31,830 of 32,988), so filtering the import saves nothing. The full corpus is imported with the complete `legalities` map, and format becomes a filter on the *expensive* stages — metadata generation (one Claude call per card) and embedding. `--format` on the import exists for constrained hosts only.
- **`Card`'s primary key is `oracle_id`, not Scryfall's `id`.** `id` is a printing UUID that rotates when a card is reprinted, which would orphan every collection and deck row on an ordinary refresh. Imports are idempotent upserts; `--prune` never removes a card that a collection or deck references, and `--reset` refuses to run while user data exists.

---

## Docker

Three containers (`frontend` nginx :3000 · `backend` FastAPI :8000 · `db` Postgres :5432), defined in `Dockercompose.yaml` at the repo root. Postgres data persists in the named volume `pgdata`; downloaded Scryfall snapshots persist in `scryfall_cache`.

The backend image runs `alembic upgrade head` from its **`ENTRYPOINT`** before handing off to the command. That's ENTRYPOINT rather than CMD on purpose: `Dockercompose.dev.yaml` overrides `command:`, and an override replaces CMD but not ENTRYPOINT, so dev containers stay migrated too.

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

# Import card data into the containerized Postgres. --format is required here:
# there's no terminal to prompt on, so the importer exits rather than hanging.
docker compose -f Dockercompose.yaml exec backend \
  python -m knowledge_pipeline.scryfall_importer --format all
```

`VITE_*` values are **build args, not runtime env** — Vite inlines them at build time, so changing them requires `--build` to take effect (see `docs/frontend.md`). Compose sets `VITE_USE_MOCKS=true` for now, same as `npm run dev` — the real backend has no working `/collection` or `/decks` endpoints yet (no service layer, router paths don't match the frontend's `/api/*` calls, and the DB has no card data since the Scryfall importer hasn't run). Flip it to `"false"` once those are implemented.

**Dev mode (live reload in-container)** — the default containers above are production-style (`fastapi run`, a built static nginx bundle) so code edits need a rebuild to show up. `Dockercompose.dev.yaml` overrides both to their auto-reloading dev servers (`fastapi dev`, `vite`) with the source bind-mounted in, matching what `npm run dev`/`fastapi dev` already do outside Docker. It also adds an `adminer` service — a zero-config web GUI for the `db` service at `localhost:8080` (System: PostgreSQL, Server: `db`, credentials from `backend/.env`):

```
# Layer the dev override on top of the base file, base first:
docker compose -f Dockercompose.yaml -f Dockercompose.dev.yaml up --build

# Only needed again after a dependency change (requirements.txt/package.json) —
# code-only edits after that just need a save, same as local dev.
```

Windows bind mounts don't propagate native filesystem change events into the container, so both watchers are forced into polling mode (`WATCHFILES_FORCE_POLLING`/Vite's `usePolling`, gated behind `DOCKER_DEV` so local dev is unaffected) — otherwise saves would silently never trigger a reload.

---


