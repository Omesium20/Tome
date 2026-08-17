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
| [docs/knowledge-pipeline.md](docs/knowledge-pipeline.md) | Scryfall Importer — bulk data API, card schema → `Card` field mapping, card faces/DFCs, legalities, rate limits |
| [docs/frontend.md](docs/frontend.md) | routing & app shell · mock backend layer & `VITE_USE_MOCKS` switch · working-deck vs. saved-deck handoff · shared collection filter logic/UI · drag-and-drop contract · design tokens · dev gotchas |

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
pytest            # test suite
```

Dev server — `fastapi dev` takes a direct file path, so run it from `backend/api/` instead:
```
cd backend/api
fastapi dev main.py   # local dev server on :8000 (routes are stubs)
```

**Knowledge Pipeline (offline; all steps are stubs)** — from `backend/`:
```
python -m knowledge_pipeline.scryfall_importer
python -m knowledge_pipeline.metadata_generator
python -m knowledge_pipeline.document_generator
python -m knowledge_pipeline.embeddings
```

---

## Docker

Three containers (`frontend` nginx :3000 · `backend` FastAPI :8000 · `db` Postgres :5432), defined in `Dockercompose.yaml` at the repo root. Postgres data persists in the named volume `pgdata`.

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
```

`VITE_*` values are **build args, not runtime env** — Vite inlines them at build time, so changing them requires `--build` to take effect (see `docs/frontend.md`). Compose sets `VITE_USE_MOCKS=false`, so the containerized frontend talks to the real backend, unlike `npm run dev`.

---


