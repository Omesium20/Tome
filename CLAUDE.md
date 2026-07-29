# CLAUDE.md

Guidance for Claude Code when working in this repository. Full requirements live in `PRD.md`; deeper technical detail lives in `docs/` (linked below). Keep this file itself short — it's a map, not the territory.

---

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

For the full system architecture (both pipelines, ChromaDB structure, Claude's responsibilities/boundaries) see **[docs/architecture.md](docs/architecture.md)**.
For the normalized entity model (Card, CardMetadata, Collection, Deck, DeckCard) see **[docs/data-model.md](docs/data-model.md)**.

---

## Stack

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | Next.js, React, TypeScript | UI, collection management, card selection, deck display |
| Backend | Python, FastAPI | API endpoints, AI orchestration, deck validation, collection management, pipeline execution |
| AI framework | LangChain | Retrieval, prompt construction, Claude API management, output parsing |
| LLM | Anthropic Claude API | Card analysis, deck strategy, commander selection, deck construction, explanations |
| Embedding model | Hugging Face Sentence Transformer | Converts card knowledge documents into vectors |
| Vector database | ChromaDB | Stores embeddings, similarity search, candidate retrieval |
| Card data source | Scryfall Bulk Data API | Card info, oracle text, mana cost, color identity, types, legalities |

---

## Development Commands

The project has not been scaffolded yet — no `package.json` or backend project files exist. This section is a placeholder based on the stack above; verify against actual config once scaffolding lands, and correct this section if commands differ.

**Frontend (`frontend/`, Next.js)**
```
npm install
npm run dev       # local dev server
npm run build     # production build
npm run lint
npm test
```

**Backend (`backend/`, FastAPI)**
```
pip install -r requirements.txt
uvicorn api.main:app --reload   # local dev server
pytest                          # test suite
```

**Knowledge Pipeline (offline, run manually / via script)**
```
python -m backend.knowledge_pipeline.scryfall_importer
python -m backend.knowledge_pipeline.metadata_generator
python -m backend.knowledge_pipeline.document_generator
python -m backend.knowledge_pipeline.embeddings
```

---

## Important

- **Keep this document updated.** Whenever a change introduces or alters an architectural or design decision — a new pipeline step, a changed data model field, a new service boundary, a stack swap — update `CLAUDE.md` and the relevant file in `docs/` as part of that same change, not as follow-up cleanup.
- **Keep `CLAUDE.md` itself under ~200 lines.** It should stay a high-level map: overview, stack, commands, and pointers. If something needs more than a few lines of explanation, it belongs in its own file under `docs/` with a link from here — don't let detail accumulate in this file.
- `PRD.md` is the source of truth for product scope and requirements; `docs/architecture.md` and `docs/data-model.md` are source of truth for technical design. If code and docs disagree, treat that as a bug to fix, not ambiguity to route around.
- Claude must never take on the backend's responsibilities (rule validation, legality checks, collection tracking, DB search) — those live in code per `docs/architecture.md`, not in prompts.
