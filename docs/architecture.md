# Architecture

Full detail behind the high-level summary in `CLAUDE.md`. Source of truth for scope/requirements is `PRD.md`; this document is source of truth for how the system is structured technically.

## The split: shared knowledge, local generation

Tome runs as **two planes with different operators, different lifecycles, and different databases**.

| | Knowledge plane | Client plane |
|---|---|---|
| Who runs it | Tome maintainers (one hosted deployment) | Each user, on their own machine |
| What it holds | Every card, its AI-generated metadata, its knowledge document and embedding | One user's collection and decks |
| Storage | Cloud Postgres + `pgvector` | Local SQLite (default) or Postgres |
| Read/write | Written only by the offline Knowledge Pipeline; clients read | Read-write, private, never leaves the machine |
| Lifecycle | Rebuilt centrally on a schedule; every client sees the update at once | Changes whenever the user changes something |
| Cost profile | Metadata generation + embedding paid **once, by us** | Deck generation paid per-user, on their hardware or their API key |

```
                 -- KNOWLEDGE PLANE (hosted, shared, read-only to clients) --

  Scryfall --> Knowledge Pipeline -->  Cloud Postgres + pgvector  -->  Knowledge API
               (offline, maintainer)    cards . card_metadata           (thin HTTPS
                                        card_documents (+vector)         read service)
                                                                              |
 =============================================================================|=======
                                                                              | HTTPS
                 -- CLIENT PLANE (the user's machine) --                      |
                                                                              v
       Vite + React  -->  Local FastAPI  -->  Deck Generation Pipeline  --> retrieval
                                |                        |
                                |                        v
                                |               Model Provider interface
                                |               |- frontier (Anthropic, OpenAI-compatible)
                                |               |- local    (Ollama, LM Studio, vLLM)
                                v
                         Local user database
                         collection . decks . deck_cards . card_cache
```

### Why this shape

**One knowledge base, built once.** Metadata generation is one model call per card across ~33,000 cards, and embedding is a full pass over the corpus. Making every user run that was the single largest barrier to using Tome — hours of compute and a real API bill before the first deck. Building it centrally means the expensive work happens once, we fix a bad metadata prompt in one place, and every user gets the same retrieval quality on the same card pool. Card data is public Scryfall data plus our derived analysis, so there is nothing user-specific to isolate — it is the same for everyone by nature.

**Generation stays on the client.** Deck generation is the part that is per-user, bursty, and expensive in a way that scales with users rather than with the card pool. Keeping it client-side means we host no inference, hold no API keys on behalf of users, and see none of their collections or decks. It is also what makes local models possible at all: a user who wants to run a model on their own GPU and pay nothing can, and a user who wants frontier-model quality points the same interface at Anthropic.

**The user's data never leaves their machine.** Collection and decks stay in a local database. The client asks the Knowledge API about *cards*, never about *the user*.

---

## Knowledge plane

### Knowledge Pipeline

Transforms raw Scryfall data into retrievable card knowledge. Offline, run by maintainers, writes to the cloud Postgres. Detail: `knowledge-pipeline.md`.

```
Scryfall -> Card Import -> Model Metadata Generation -> Knowledge Document Generation
  -> Embeddings -> Cloud Postgres (pgvector)
```

1. **Card Import** — pull card data from the Scryfall Bulk Data API (oracle text, mana cost, color identity, types, legalities). Implemented; **not format-scoped, and not scopable** — one rule decides what lands (is the object a card?), so the corpus holds all 34,831 cards with their complete legality maps. Commander is 91% of that, so filtering at import time saved almost nothing while making every other format a re-import away; and a user's *collection* contains cards legal in no format at all, which still have to resolve. Format is a filter downstream — at the per-card AI stages, or on the client — see `knowledge-pipeline.md#the-import-is-not-format-scoped-and-cannot-be-made-so`.
2. **Metadata Generation** — a model analyzes each card and generates strategic metadata (roles, themes, game stage, power rating, strengths/weaknesses, synergy tags). See `data-model.md` for the `CardMetadata` shape. Run centrally against a frontier model: this stage sets the ceiling on retrieval quality for every user, so it is not a place to economize.
3. **Knowledge Document Generation** — a natural-language document is generated per card from its Card + CardMetadata (not hand-written Markdown).
4. **Embeddings** — a Hugging Face Sentence Transformer embeds each knowledge document.
5. **Persist** — document, structured filter fields, and embedding are written to `card_documents` in the same Postgres as `cards` and `card_metadata`.

### Why pgvector and not ChromaDB

The vector store used to be a separate ChromaDB instance on each user's disk. Centralizing the knowledge base removed its reason to exist:

- **One system to host instead of two.** Cards, metadata, documents, and vectors live in one managed Postgres. There is no second service to deploy, back up, or keep consistent with the first — and no window where the relational rows and the vector index disagree, because a pipeline run writes both in the same transaction.
- **Retrieval needs both halves anyway.** Every real query is a vector search *plus* hard filters (color identity is a Commander legality constraint, not a preference; also format legality, mana value, roles). In Postgres that is one query. Split across two stores it is a fetch-then-filter round trip that either over-fetches or drops good candidates.
- **`pgvector` is the boring choice and it fits.** ~33,000 vectors is small; an HNSW index over that is comfortably sub-millisecond, and every managed Postgres we would consider (Neon, Supabase, RDS) ships the extension.

Column type and index, per the official pgvector SQLAlchemy docs (Context7 `/pgvector/pgvector-python`):

```python
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Index

class CardDocument(Base):
    embedding = Column(VECTOR(384))          # dimension = the embedding model's

Index(
    "card_documents_embedding_hnsw",
    CardDocument.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
```

Use `vector_cosine_ops` with `.cosine_distance(...)`: the index operator class must match the distance function the query orders by, or Postgres silently falls back to a sequential scan.

### Knowledge API

A thin hosted FastAPI read service in front of the cloud Postgres. It is the **only** way a client reaches the knowledge base — clients never hold database credentials. Contract, endpoints, and versioning: `knowledge-api.md`.

Clients get a connection-stringless, rate-limitable, versioned boundary; we get freedom to change the schema, re-tune the HNSW index, or swap the embedding model without breaking installed clients.

---

## Client plane

### Deck Generation Pipeline

Runs on the user's machine when they request a deck.

1. **Receive selected cards** — the "build around" cards the user picked (e.g. `Hardened Scales`, `The Ozolith`).
2. **Generate retrieval query** — the local backend analyzes colors, themes, roles, and mechanics of the selected cards to build a retrieval query.
3. **Retrieve candidate cards** — `POST /v1/retrieve` on the Knowledge API returns ~100–200 synergistic candidates, already filtered by color identity and format legality server-side. The client caches the returned card data locally so later collection and deck views don't re-fetch it.
4. **Deck construction** — the configured **model provider** receives selected cards, candidates, user preferences (power level, collection preference), and Commander rules, and determines commander, strategy, win condition, ramp/removal/draw/protection packages, and lands. This is the only step that differs between a local and a frontier model, and it differs only in quality and latency — not in contract.
5. **Deck validation** — Python validates 100-card count, commander legality, color identity, and singleton rules **locally**, against the cached card data. On failure, errors are fed back to the model for repair (bounded retries, then surface a partial deck with the specific violations).
6. **Return result** — frontend displays commander, full deck list, owned vs. missing cards, proxy cards, and the model's explanation.

Steps 1, 2, 5 and 6 are pure local code and identical for every provider. Only step 4 crosses the provider boundary, and only step 3 leaves the machine.

### Model provider abstraction

Deck construction goes through a `ModelProvider` interface, not a vendor SDK. Full interface, supported backends, capability floor, and configuration: `model-providers.md`.

The pipeline depends on the interface only. Swapping Anthropic for a local Ollama model is a config change, never a code change:

```
MODEL_PROVIDER=anthropic   MODEL_NAME=claude-opus-5
MODEL_PROVIDER=openai      MODEL_NAME=<model>   MODEL_BASE_URL=...   # any OpenAI-compatible host
MODEL_PROVIDER=ollama      MODEL_NAME=<model>   MODEL_BASE_URL=http://localhost:11434
```

### Division of responsibility

The model **should**: understand strategies, recommend cards, build decks, explain decisions, select commanders.

The model **should not**: search the card database, validate rules, track collections, calculate legality — these are backend responsibilities, enforced in code, not trusted to the model.

This line was always the design, but centralizing knowledge and allowing weaker local models makes it load-bearing rather than merely tidy. Retrieval is a filtered SQL query in the knowledge plane; validation is deterministic Python in the client plane. A local model that hallucinates an illegal card cannot produce an invalid deck — it produces a validation failure and a repair round. **Correctness does not depend on model quality; only deck quality does.**

---

## Data boundary: two databases, one join key

`cards.oracle_id` is the join key across the plane boundary. Local rows reference cards by oracle ID, but the constraint **cannot be enforced by the database**, because the referenced table is in a different Postgres:

```
cloud:  cards.oracle_id  <----- logical reference -----+
                                                       |
local:  collection.card_id . deck_cards.card_id . decks.commander_id
```

Three consequences the code has to own, since no foreign key will catch them:

- **`oracle_id` stability is now load-bearing.** It was already the primary key precisely because printing IDs rotate on reprint (`data-model.md`). Now a rotation would orphan user data across a boundary where nothing can cascade. Never key user rows on anything else.
- **The client needs a local card cache.** Rendering a collection of 2,000 cards cannot mean 2,000 API calls. The client keeps a `card_cache` table of display fields for every card it has seen, refreshed opportunistically. It is a cache, not a source of truth — it can be deleted and rebuilt.
- **A local row may reference a card the cache doesn't have** (a restored backup, or a card printed since the last refresh). The client resolves misses through the Knowledge API in batch, and renders a placeholder rather than failing if the card is genuinely unknown.

---

## Project Structure

Frontend conventions (mock layer, shared filter code, DnD contract, design tokens) are documented in `docs/frontend.md`.

```
project/
  frontend/                        Vite 5, React 18, React Router 7, TypeScript, Tailwind
    index.html                     entry document: title/description, favicon
    vite.config.ts                 plugins, @/* alias, dev port 3000, vitest config
    nginx.conf                     container-only: SPA fallback for client-side routes
    src/
      main.tsx                     mounts <BrowserRouter>, global CSS, Inter font
      App.tsx                      app shell (sidebar) + the route table
      pages/                       one named-export component per route
        Home.tsx                   / — landing page
        Collection.tsx             /collection — Arena-style grid of owned cards
        DeckBuilder.tsx            /deck-builder — role-column deck board, AI generation
        Decks.tsx                  /decks — saved-deck grid, 100-deck cap, create/open/delete
      styles/
        globals.css                Tailwind layers, dark color-scheme, --font-inter
      components/
        collection/                CollectionGrid, CardTile, CollectionToolbar,
                                   CollectionFilterControls, CardPreviewModal,
                                   AddCardsDialog, CollectionUpload (stub: CSV import)
        deck-builder/              DeckToolbar, DeckColumn, CommanderSlot,
                                   SaveDeckDialog (first-save name prompt),
                                   CollectionPanel (drag-drop collection sidebar)
        decks/                     DeckTile (saved-deck card on /decks)
        card-selection/            CardSelector (stub: build-around selection)
        deck-display/              DeckView (stub: generated-deck display)
        ui/                        shared primitives: ManaPips, SideNav
      lib/
        types.ts                   TS mirror of the data model (docs/data-model.md)
        api.ts                     single gateway to the LOCAL backend; mock switch
        filter-cards.ts            shared collection filter/sort logic
        working-deck.ts            deck builder's in-progress deck (localStorage),
                                   shared with /decks for open/create handoff
        mock/                      mock backend: cards.ts, collection.ts, decks.ts,
                                   metadata.ts (CardMetadata stand-in), generate.ts
        tests/                     vitest suite

  backend/                         Python. Two deployables from one source tree.
    config.py                      all env-backed settings (pydantic-settings), loads backend/.env
    logging_config.py              configure_logging(), called once per entry point

    -- client plane (runs on the user's machine) --
    api/
      main.py                      app entry, CORS for localhost:3000, /health
      schemas.py                   response models
      routes/                      cards.py, collection.py, deck_builder.py, deck_collection.py
    deck_pipeline/
      retrieval.py                 HTTP client for the Knowledge API (not a DB query)
      prompt_builder.py            provider-agnostic prompt construction
      generator.py                 orchestration + the validation repair loop
      validator.py                 Commander rules, enforced locally in code
    ai/
      provider.py                  ModelProvider protocol + registry/factory
      providers/
        anthropic_provider.py      frontier: Anthropic Claude API
        openai_compatible.py       frontier + self-hosted: OpenAI, OpenRouter, vLLM, LM Studio
        ollama_provider.py         local: Ollama

    -- knowledge plane (hosted by maintainers) --
    knowledge_api/
      main.py                      thin read service; the only client-facing entry point
      routes/                      cards.py (lookup/resolve), retrieve.py (vector + filters)
    knowledge_pipeline/
      scryfall_importer/           implemented — Scryfall bulk import
        bulk.py                    catalog, download, streamed gzip/JSONL
        card_filter.py             the one import filter: is this a card?
        mapping.py                 Scryfall JSON -> CardRow, face merging
        sink.py                    batched upsert, reset. no delete in an import
        pipeline.py                import_cards() orchestrator
        __main__.py                CLI; no format flags
      metadata_generator.py
      document_generator.py
      embeddings.py

    -- shared --
    database/                      split by which database the entities live in
      knowledge/                   cloud plane
        models.py                  Card, CardMetadata, ImportRun (CardDocument pending)
        session.py                 lazy engine <- KNOWLEDGE_DATABASE_URL
      local/                       client plane
        models.py                  Collection, Deck, DeckCard (CardCache pending)
        session.py                 lazy engine <- LOCAL_DATABASE_URL, get_session()
    alembic.ini                    [local] and [knowledge] sections; no bare [alembic]
    alembic/
      knowledge/                   env.py + versions/ for the cloud schema
      local/                       env.py + versions/ for the client schema
    tests/                         pytest suite (pythonpath = backend/)
```

The two planes share a source tree and a `config.py` but are **separate deployables**: a client install never runs `knowledge_api/` or `knowledge_pipeline/`, and the hosted knowledge service never runs `deck_pipeline/` or `api/`. They also have separate Alembic version directories, because they migrate independently against different databases.

`config.py` is shared but not undivided: `KnowledgeSettings` and `LocalSettings` are siblings over a minimal `BaseAppSettings`, and there is deliberately no combined accessor. A caller names the plane it wants (`get_knowledge_settings()` / `get_local_settings()`) or gets nothing. The one exception is `get_base_settings()`, for code that runs in either deployable and needs neither database — logging setup, essentially.

**The boundary is an import rule, and it is checkable:** nothing under `api/`, `deck_pipeline/` or `ai/` may import `database.knowledge`, and nothing under `knowledge_pipeline/` or `knowledge_api/` may import `database.local`. A grep for either is the cheapest test this architecture has.
