# Self-Hosting Tome

Tome runs on your own machine. Your collection and your decks live in a database you control and are never transmitted anywhere. Deck generation runs locally, against a model **you** choose — a local model on your own hardware, or a frontier API with your own key.

The one thing you don't have to build yourself is the card knowledge base. Analyzing ~33,000 cards is one model call per card plus a full embedding pass; that work is done once, centrally, and served to every install through the hosted **Knowledge API**. You can still run your own if you want to (see [Running your own knowledge plane](#running-your-own-knowledge-plane)) — you just don't have to.

```
   hosted by us                          your machine
   ------------                          ------------
   Knowledge API   <--- HTTPS ---        Tome client
   (cards, card analysis,                 - your collection
    embeddings)                           - your decks
                                          - your model
```

What crosses that line: card lookups and retrieval queries. What doesn't: anything about you.

---

## What you need

| | Required | Notes |
|---|---|---|
| A model | yes | Either a local model (free, no key, needs decent hardware) or a frontier API key. See [Choosing a model](#choosing-a-model) |
| Database | no setup needed | SQLite works out of the box. Postgres is supported and recommended if you run the backend in a container |
| Disk | ~200 MB | Just the app and a local card cache. The card corpus and the embedding model stay server-side |
| Network | for generation | Deck generation needs the Knowledge API. Browsing your collection and editing decks by hand work offline from cache |

No Scryfall account, key, or bulk download is needed for a normal install — card data reaches you through the Knowledge API.

---

## Docker

Everything (frontend, backend, Postgres) comes up with one command.

```bash
cp .env.example .env                  # Postgres credentials for compose
cp backend/.env.example backend/.env  # then choose your model
```

Edit `backend/.env` and set your model provider (see [Choosing a model](#choosing-a-model)). Change `POSTGRES_PASSWORD` in `.env` from the default.

```bash
docker compose -f Dockercompose.yaml up --build
```

The compose file is deliberately not named `compose.yaml`, so **every command needs `-f Dockercompose.yaml`**. The backend applies local database migrations automatically on start.

That's the whole setup — there's no card import step. The first time you browse cards, the client fills its local cache from the Knowledge API.

**If you're using a local model,** note that `localhost` inside a container is the container, not your host. Point `MODEL_BASE_URL` at `http://host.docker.internal:11434` (Docker Desktop on Windows/macOS) rather than `http://localhost:11434`.

---

## Local

Run from `backend/` — imports and `pytest.ini` assume it as the project root.

```bash
cd backend
python -m venv .venv
.venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # then choose your model
alembic -n local upgrade head # create/update your local schema
```

Then start the API (`fastapi dev` takes a file path, so run it from `backend/api/`):

```bash
cd api && fastapi dev main.py
```

### Pointing at your own database

Set `LOCAL_DATABASE_URL` in `backend/.env`:

```
# SQLite — zero setup, fine for one user
LOCAL_DATABASE_URL=sqlite:///./tome.db

# Postgres — anywhere you like: local, a LAN box, a managed host
LOCAL_DATABASE_URL=postgresql+psycopg://user:password@host:5432/tome
```

Run `alembic -n local upgrade head` after changing this, to create the schema in the new database.

This database holds **only your data** — collection, decks, and a cache of card display data. It does not hold the card corpus, so it stays small and is cheap to back up. Back up this database; the cache rebuilds itself and the card corpus is never yours to lose.

---

## Choosing a model

Deck generation goes through a provider interface, so all three options below are a config change, not a code change. Full detail — the interface, the capability floor, per-provider notes: `docs/model-providers.md`.

### Frontier API — best deck quality

```
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-opus-5
MODEL_API_KEY=sk-ant-...      # https://console.anthropic.com/settings/keys
```

You pay per deck generated. Nothing about your collection is stored by Tome; it goes to your chosen provider under your own account, subject to their terms.

### Local model — free, private, no key

```
MODEL_PROVIDER=ollama
MODEL_NAME=<a model you have pulled>
MODEL_BASE_URL=http://localhost:11434
```

No API key, no cost, no network call for generation. What you trade is deck quality, and the failure mode is worth knowing: **an undersized model produces bland decks, not broken ones.** Every deck is validated locally by the same deterministic rules regardless of which model built it, so a weak model can't hand you an illegal deck — it hands you a legal, generic one, and takes more repair rounds to get there.

Practical floor: a model that reliably emits JSON against a schema and has a **~32K context window**, because the candidate list is 100–200 cards of card knowledge plus rules and your preferences. Smaller models fail at following the many simultaneous constraints ("100 cards, singleton, this color identity, this curve"), not at the format.

### Any OpenAI-compatible endpoint

Covers OpenRouter, vLLM, LM Studio, and most corporate gateways — point `MODEL_BASE_URL` at the server:

```
MODEL_PROVIDER=openai
MODEL_NAME=<model>
MODEL_BASE_URL=https://your-gateway/v1
MODEL_API_KEY=...
```

Tome checks reachability and auth at startup, so a stopped Ollama or a bad key is reported immediately — not twelve minutes into a deck build.

---

## Configuration

All settings live in `backend/.env` and are read through `backend/config.py`. Real environment variables take precedence over the file, which is what makes the Docker overrides work.

The two tables below are two **separate settings classes**, not one list split for readability: `LocalSettings` and `KnowledgeSettings`, siblings over a shared base. A client process cannot read a knowledge-plane setting and vice versa — that is the point of the split. `DATABASE_URL`, the single pre-split setting, is now rejected at startup with a message naming its two replacements, rather than being ignored while a default quietly takes over.

> **Implemented today:** `LOCAL_DATABASE_URL`, `KNOWLEDGE_API_URL`, `MODEL_API_KEY`, `LOG_LEVEL`, and the `KNOWLEDGE_DATABASE_URL` / `SCRYFALL_*` / `IMPORT_BATCH_SIZE` rows. The `MODEL_PROVIDER` / `MODEL_NAME` / `MODEL_BASE_URL` / `MODEL_MAX_TOKENS` / `MODEL_TIMEOUT_SECONDS` and `EMBEDDING_MODEL` rows describe the target shape; they land with the `ModelProvider` interface and the embedding stage.

### Client settings — what a normal install uses

| Variable | Default | Purpose |
|---|---|---|
| `KNOWLEDGE_API_URL` | the hosted service | Where to fetch card data and candidates from. Point at your own if you run one |
| `LOCAL_DATABASE_URL` | `sqlite:///./tome.db` | Your collection and decks. Any SQLAlchemy URL |
| `MODEL_PROVIDER` | `anthropic` | `anthropic` · `openai` · `ollama` |
| `MODEL_NAME` | provider-specific | Which model to build decks with |
| `MODEL_API_KEY` | — | Required for hosted providers; **ignored for `ollama`** |
| `MODEL_BASE_URL` | provider default | For `openai` against a non-OpenAI host, or a non-default Ollama port |
| `MODEL_MAX_TOKENS` | `16000` | Output ceiling for deck construction |
| `MODEL_TIMEOUT_SECONDS` | `600` | Generous on purpose — local models on CPU are slow |
| `LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**`MODEL_API_KEY` is only required for hosted providers.** It used to be mandatory, when Anthropic was the only option. A local-model install needs no key at all, and validation is per-provider: startup fails only if the provider you actually selected is missing a credential it needs.

### Knowledge-plane settings — only if you run your own

| Variable | Default | Purpose |
|---|---|---|
| `KNOWLEDGE_DATABASE_URL` | — | The cloud Postgres holding the card corpus. Needs `pgvector` |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Must match what the corpus was embedded with |
| `SCRYFALL_USER_AGENT` | `Tome/<version>` | Scryfall requires a User-Agent identifying your app. Change it for a modified or public deployment |
| `SCRYFALL_API_BASE` | `https://api.scryfall.com` | Only worth changing against a mock/proxy in tests |
| `SCRYFALL_BULK_TYPE` | `oracle_cards` | Which bulk file to import |
| `SCRYFALL_CACHE_DIR` | `./data/scryfall` | Where downloaded snapshots are cached |
| `IMPORT_BATCH_SIZE` | `1000` | Rows per database batch during import |

---

## Running your own knowledge plane

You don't need this to use Tome. Do it if you want zero dependency on our service, a modified card pool, or your own card analysis.

It means running all four pieces yourself: a Postgres with `pgvector`, the Knowledge Pipeline to populate it, the Knowledge API in front of it, and `KNOWLEDGE_API_URL` pointed at your instance.

**Understand the cost first.** The Scryfall import is fast and cheap — about 10 seconds against a warm cache. The stages after it are neither: metadata generation is **one model call per card across ~33,000 cards**, plus a full embedding pass. That bill, in time and API spend, is the entire reason the hosted service exists.

```bash
# 1. A Postgres with pgvector enabled (Neon, Supabase, RDS, or your own).
#    Set KNOWLEDGE_DATABASE_URL to it, with a read-write role.
alembic -n knowledge upgrade head

# 2. Import the card corpus. No options: it imports every card.
python -m knowledge_pipeline.scryfall_importer

# 3. The expensive stages — analysis, documents, embeddings.
python -m knowledge_pipeline.metadata_generator
python -m knowledge_pipeline.document_generator
python -m knowledge_pipeline.embeddings

# 4. Serve it, and point your client at it.
#    KNOWLEDGE_API_URL=http://localhost:8001
```

Give the Knowledge API a **read-only** database role. It never writes, and a public read service shouldn't be able to damage the corpus.

### The card pool: there is no choice to make

The importer takes **every card** — 34,831 of the 38,906 objects in Scryfall's bulk file, roughly 40–60 MB in Postgres. The only thing excluded is objects that aren't cards: tokens, emblems, art series, vanguards, and similar. Legality is never consulted, so the corpus includes cards legal in no format at all.

```bash
python -m knowledge_pipeline.scryfall_importer
```

That's the whole interface. There is no `--format`, and passing one exits 2 — a hosted corpus is shared, so trimming it to one person's format would only make it useless to the next person. Every row carries its complete `legalities` map, which is what lets the client filter by format without a re-import.

Safe unattended: nothing to select means nothing to prompt for, so Docker and cron can't hang on a terminal that isn't there.

Two consequences worth knowing if you run your own:

- **The AI stages, not the import, are where pool size costs money.** Filter there. Excluding the 2,079 cards legal in no format is the obvious first cut; scoping to Commander (31,830) is the next.
- **Your corpus will contain cards nobody can legally play.** That is deliberate. CSV collection import resolves *owned* card names through the Knowledge API, and 1,945 paper-printed cards — 1,244 of them Un-set cards — are legal nowhere. Dropping them would make each one a permanent placeholder in somebody's collection.

### Keeping card data current

Scryfall regenerates its bulk files every 12–24 hours, but gameplay data changes slowly — a weekly refresh is plenty, or just run it after a set release.

```bash
python -m knowledge_pipeline.scryfall_importer --if-newer
```

`--if-newer` exits immediately if the current snapshot has already been imported, so this is safe to put on a schedule. Re-importing updates cards in place, never deleting and re-creating them, and the primary key is Scryfall's reprint-stable oracle ID rather than a printing ID that changes when a card is reprinted.

Re-run the AI stages afterward for cards whose oracle text actually changed — `CardMetadata.updated_at` and `CardDocument.updated_at` exist to find metadata older than the card it describes.

### `--reset` is destructive here, and `--prune` is gone

`--prune` was protected by checking for user data in the same database. **That guard is gone**, because collections and decks live on users' machines now and the importer can't see what a deletion would orphan — so `--prune` went with it rather than staying on with a check that returned a reassuring zero. It had also lost its purpose: it existed to clean up after a *narrowed* import, and nothing narrows the import any more.

- **An import cannot delete a row.** It adds and updates. A card Scryfall drops upstream lingers as an unreferenced row costing bytes; deleting it would break whoever owns that card.
- **`--reset` is the one destructive path, and it asks which database you meant.** It prints the target URL and the card count, then requires that database's name typed back exactly. There is no `--force`, and it always refuses without a terminal. On a shared database a reset is a full corpus wipe affecting every client pointed at it, so gate it with credentials rather than relying on the prompt.

Card data is always re-downloadable, but the AI stages are not cheap to redo — back up the knowledge database before either.

---

## Upgrading

```bash
git pull
pip install -r backend/requirements.txt   # if requirements changed
cd backend && alembic -n local upgrade head
```

That migrates **your** database only. The knowledge database is migrated by whoever operates it — and a client upgrade never requires a knowledge-plane migration to land first, because the Knowledge API is versioned (`docs/knowledge-api.md#versioning`). Docker runs the local migration for you on container start.

---

## Troubleshooting

**`MODEL_API_KEY is not set`** — you've selected a hosted provider without a key. Either add one to `backend/.env`, or switch `MODEL_PROVIDER=ollama` to run locally without a key.

**"Can't reach the model"** — for `ollama`, check the server is actually running and that `MODEL_BASE_URL` is right. In Docker, use `http://host.docker.internal:11434`, not `localhost`.

**"Can't reach the Knowledge API"** — deck generation needs it; browsing your collection and hand-editing decks don't, and keep working from your local cache. Check `KNOWLEDGE_API_URL` and your network.

**"This client is too old"** — the Knowledge API reported a `schema_version` your build doesn't understand. `git pull` and upgrade rather than trying to force it; a mismatched client can misread responses.

**Cards show as placeholders** — your local cache doesn't have them and the API couldn't be reached to fill it (common right after restoring a backup onto a fresh install). They resolve on their own once the API is reachable.

**Decks come out bland and generic** — almost always an undersized local model, not a bug. The deck is legal because validation is local and deterministic; it's the strategy the model isn't following. Try a larger model or a frontier API. See [Choosing a model](#choosing-a-model).

**Generation is very slow** — a local model on CPU can take many minutes; `MODEL_TIMEOUT_SECONDS` defaults to 600 for that reason. If you're hitting the timeout, use a smaller model, enable GPU, or switch providers.

### Running your own knowledge plane

**Importer writes to the wrong database** — check `KNOWLEDGE_DATABASE_URL` (the corpus) versus `LOCAL_DATABASE_URL` (your collection). These are different databases now; the pipeline writes the former.

**Retrieval returns nonsense** — almost always a mixed-embedding corpus: `EMBEDDING_MODEL` doesn't match what the documents were embedded with. Changing the embedding model requires re-embedding everything, not a partial pass.

**Retrieval is slow** — check the HNSW index exists and that queries order by `cosine_distance`. The index operator class must match the distance function, or Postgres silently falls back to a sequential scan.

**HTTP 429 from Scryfall** — the importer backs off automatically. Scryfall imposes a 30-second restriction, so it waits at least that long. Bulk file downloads themselves aren't rate limited; only the one catalog lookup per run is.
