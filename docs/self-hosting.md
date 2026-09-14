# Self-Hosting Tome

Tome is designed to run on your own machine, against your own database, with your own Anthropic API key. Nothing is shared with a hosted service — your collection and decks live in a database you control.

There are two supported setups: **Docker** (everything bundled) and **local** (bring your own Python and database).

---

## What you need

| | Required | Notes |
|---|---|---|
| Anthropic API key | yes | From [console.anthropic.com](https://console.anthropic.com/settings/keys). Used for card analysis and deck generation |
| Database | yes | SQLite works out of the box and needs no setup. Postgres is supported and recommended if you want to run the backend in a container |
| Disk | ~1 GB | ~24 MB per cached Scryfall snapshot, ~60 MB of card data, plus the embedding model and vector store |

Card data comes from [Scryfall](https://scryfall.com), which is free and needs no account or key.

---

## Docker

Everything (frontend, backend, Postgres) comes up with one command.

```bash
cp .env.example .env                  # Postgres credentials for compose
cp backend/.env.example backend/.env  # then add your MODEL_API_KEY
```

Edit `backend/.env` and set `MODEL_API_KEY`. Change `POSTGRES_PASSWORD` in `.env` from the default.

```bash
docker compose -f Dockercompose.yaml up --build
```

The compose file is deliberately not named `compose.yaml`, so **every command needs `-f Dockercompose.yaml`**. The backend applies database migrations automatically on start.

Then import the card pool:

```bash
docker compose -f Dockercompose.yaml exec backend \
  python -m knowledge_pipeline.scryfall_importer --format all
```

`--format` is required here — there's no terminal for the container to prompt on, so an unattended run without it exits rather than hanging. `all` imports every real card (~33,000); see [Choosing a card pool](#choosing-a-card-pool) for narrower options.

The download is cached in the `scryfall_cache` volume, so rebuilding containers doesn't re-fetch it.

---

## Local

Run from `backend/` — imports and `pytest.ini` assume it as the project root.

```bash
cd backend
python -m venv .venv
.venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # then add your MODEL_API_KEY
alembic upgrade head          # create/update the schema
python -m knowledge_pipeline.scryfall_importer --format commander
```

The import runs unattended and prints a summary when it finishes:

```
Import complete (commander): 38,626 read, 31,830 matched, 31,830 written, 94.2s
```

Then start the API (`fastapi dev` takes a file path, so run it from `backend/api/`):

```bash
cd api && fastapi dev main.py
```

### Pointing at your own database

Set `DATABASE_URL` in `backend/.env`:

```
# SQLite — zero setup, fine for one user
DATABASE_URL=sqlite:///./tome.db

# Postgres — anywhere you like: local, a LAN box, a managed host
DATABASE_URL=postgresql+psycopg://user:password@host:5432/tome
```

Run `alembic upgrade head` after changing this, to create the schema in the new database.

---

## Configuration

All settings live in `backend/.env` and are read through `backend/config.py`. Real environment variables take precedence over the file, which is what makes the Docker overrides work.

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_API_KEY` | — | **Required.** Your Anthropic API key |
| `DATABASE_URL` | `sqlite:///./tome.db` | Any SQLAlchemy URL |
| `CHROMA_PERSIST_DIR` | `./chroma_data` | Where the vector store keeps its files |
| `LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SCRYFALL_USER_AGENT` | `Tome/<version>` | Scryfall requires a User-Agent identifying your app. Change it if you run a modified or public deployment |
| `SCRYFALL_API_BASE` | `https://api.scryfall.com` | Scryfall API root. Only worth changing against a mock/proxy in tests |
| `SCRYFALL_BULK_TYPE` | `oracle_cards` | Which bulk file to import |
| `SCRYFALL_CACHE_DIR` | `./data/scryfall` | Where downloaded snapshots are cached |
| `IMPORT_BATCH_SIZE` | `1000` | Rows per database batch during import |

---

## Choosing a card pool

By default the importer pulls in **every real card** — about 33,000 objects, roughly 40–60 MB in Postgres — with each card's complete `legalities` map stored alongside it, regardless of which format(s) it's legal in. That's deliberate: Commander-legal cards are 96.5% of the entire pool, so narrowing the *import* saves almost nothing. Format only starts to matter downstream, at the AI stages (metadata generation, embedding) that cost real time and API calls per card — narrow there once those stages exist, not here.

```bash
python -m knowledge_pipeline.scryfall_importer                    # interactive picker (needs a real terminal)
python -m knowledge_pipeline.scryfall_importer --format commander # or any other registered pool
```

Registered pools come from `backend/knowledge_pipeline/scryfall_importer/formats.py`'s `PROFILES` registry: `all`, `commander`, `vintage`, `legacy`, `oathbreaker`, `modern`, `duel`, `pioneer`, `pauper`, `paupercommander`, `brawl`, `standard`. Naming one that isn't registered exits with code 2 and lists the valid choices. Tokens, emblems, and other non-card objects are excluded from every profile regardless of format.

Since Tome's deck builder UI, rules validator, and AI prompts all assume a 100-card Commander singleton deck, most self-hosters will still want `--format commander` (~31,800 cards) for a smaller local database — but every card keeps its full legality map either way, so switching pools later, or a future format being added to the registry, is never a re-import from scratch.

---

## Keeping card data current

Scryfall regenerates its bulk files every 12–24 hours, but gameplay data changes slowly — a weekly refresh is plenty, or just run it after a set release.

```bash
python -m knowledge_pipeline.scryfall_importer --if-newer
```

`--if-newer` exits immediately if you've already imported the current snapshot, so this is safe to put on a schedule.

**Re-importing never touches your collection or your decks.** Cards are updated in place, never deleted and re-created, and the primary key is Scryfall's reprint-stable oracle ID rather than a printing ID that changes when a card is reprinted.

### Reclaiming space

To clear out cards that are no longer in the pool — banned since your last import, or left over from an older version of Tome that imported every card:

```bash
python -m knowledge_pipeline.scryfall_importer --prune
```

`--prune` removes cards outside the imported pool **except** any card in your collection, in a deck, or with generated metadata. A banned card you physically own stays in your collection.

### Starting over

```bash
python -m knowledge_pipeline.scryfall_importer --reset
```

`--reset` empties the card table before importing. It **refuses to run** if you have any collection or deck data, because deleting cards would orphan it. To go ahead anyway you need `--force` *and* to type `delete` at the prompt — there is no way to do this unattended by accident.

Card data is always re-downloadable. Your collection is not — so back up your database before any `--force`.

---

## Upgrading

```bash
git pull
pip install -r backend/requirements.txt   # if requirements changed
cd backend && alembic upgrade head
```

`alembic upgrade head` applies any schema changes to your existing database without losing data. Docker does this for you on container start.

---

## Troubleshooting

**`MODEL_API_KEY is not set`** — copy `backend/.env.example` to `backend/.env` and add your key. The importer doesn't need it; deck generation does.

**Importer writes to SQLite when you configured Postgres** — check `DATABASE_URL` is in `backend/.env` (not the repo-root `.env`, which only holds Postgres credentials for compose).

**`No --format given and no terminal to ask on`** — the importer refuses to hang waiting for input it can't get (Docker `exec`, cron, CI all have no TTY). Pass `--format` explicitly; see [Choosing a card pool](#choosing-a-card-pool).

**Import seems slow** — the first run downloads ~24 MB. Later runs reuse the cached file in `SCRYFALL_CACHE_DIR`; a full import takes about 10 seconds after that. `--force-download` re-fetches if you suspect a corrupt cache.

**HTTP 429 from Scryfall** — the importer backs off automatically. Scryfall imposes a 30-second restriction, so it waits at least that long. Bulk file downloads themselves aren't rate limited; only the one catalog lookup per run is.
