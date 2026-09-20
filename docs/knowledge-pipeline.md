# Knowledge Pipeline

Detailed technical reference for the Knowledge Pipeline introduced in `docs/architecture.md#knowledge-pipeline`. That document is the source of truth for the pipeline's shape and stage order; this file exists to hold the implementation-level detail for each stage as it's built, so `architecture.md` doesn't accumulate detail it wasn't meant to hold.

**This pipeline is operated by maintainers, not by users.** It runs offline against the shared cloud Postgres, and its output is the same for everyone — every client reads the result through the Knowledge API (`knowledge-api.md`) rather than building it locally. That is the point: metadata generation is one model call per card across ~33,000 cards, and asking each user to pay that bill before their first deck was the single largest barrier to using Tome.

Two consequences for anyone working in here:

- **`KNOWLEDGE_DATABASE_URL` points at the cloud Postgres**, with a read-write role. It is a field on `KnowledgeSettings` (`backend/config.py`) with no default and no fallback: an unset value is an error, never a quiet SQLite file. The Knowledge API uses a separate read-only role against the same database. The client's own `LOCAL_DATABASE_URL` is a different setting on a different class, and nothing in this pipeline can read it.
- **A run is a publish.** There is no per-user copy to roll forward independently, so a bad metadata prompt or a half-finished embed is visible to every user at once. Stage-by-stage, resumable, and idempotent are requirements, not niceties — which is already how the importer is built.

Pipeline (offline, run from `backend/`):

```
Scryfall → Card Import → Model Metadata Generation → Knowledge Document Generation
  → Hugging Face Embeddings → Cloud Postgres (pgvector)
```

| Stage                         | Module                                     | Writes                     | Status          |
| ----------------------------- | ------------------------------------------ | -------------------------- | --------------- |
| Card Import                   | `knowledge_pipeline/scryfall_importer/`    | `cards`, `import_runs`     | **implemented** |
| Metadata Generation           | `knowledge_pipeline/metadata_generator.py` | `card_metadata`            | stub            |
| Knowledge Document Generation | `knowledge_pipeline/document_generator.py` | `card_documents.document`  | stub            |
| Embeddings                    | `knowledge_pipeline/embeddings.py`         | `card_documents.embedding` | stub            |

The output of Card Import is the `Card` entity described in `docs/data-model.md#card` — imported directly from Scryfall, never AI-generated. The remaining stages are undocumented until implemented.

**The vector store is `pgvector` in the same Postgres, not ChromaDB.** Documents, their filter fields, and their embeddings live alongside the cards they describe, so a stage writes relational rows and vectors in one transaction and retrieval is a single query with both similarity and hard filters. Rationale and the column/index declaration: `architecture.md#why-pgvector-and-not-chromadb`; the schema: `data-model.md#carddocument`.

The embedding model is a property of the corpus, not of a run — a corpus embedded by mixed model versions returns nonsense. `card_documents.embedding_model` records which model produced each vector, and changing the model means re-embedding everything, not a partial pass.

---

## Scryfall Importer

Implemented in `backend/knowledge_pipeline/scryfall_importer/`. Reference notes below are from the official Scryfall API docs (via Context7, `/websites/scryfall_api`); numbers are from the 2026-08-17 `oracle_cards` snapshot.

### Module layout

Split by responsibility, with every stage a generator so the corpus is never held in memory:

```
catalog -> download -> stream -> filter -> map -> upsert
```

| Module | Responsibility |
|---|---|
| `bulk.py` | Catalog lookup, download to cache, streamed gzip/JSONL decode |
| `card_filter.py` | The one import filter: is this object a card? Plus `NON_CARD_LAYOUTS` |
| `mapping.py` | Scryfall JSON → `CardRow`, including the face-merge rules |
| `sink.py` | Batched upsert and an unguarded reset. No delete path inside an import |
| `pipeline.py` | `import_cards()` — wires the stages, writes an `ImportRun` |
| `__main__.py` | CLI: flags, and the typed confirmation on `--reset` |

### Commands

All of these write to `KNOWLEDGE_DATABASE_URL`.

```
python -m knowledge_pipeline.scryfall_importer                        # the whole card pool
python -m knowledge_pipeline.scryfall_importer --if-newer             # weekly refresh
python -m knowledge_pipeline.scryfall_importer --dry-run --limit 500  # no writes
python -m knowledge_pipeline.scryfall_importer --force-download       # ignore the cache
python -m knowledge_pipeline.scryfall_importer --reset                # start over (destructive)
```

**There are no format flags.** `--format` and `--prune` were removed, not defaulted — passing either exits 2 with "unrecognized arguments", because a flag that silently does nothing is worse than one that errors. The import takes the whole pool every time, so it is safe to run unattended: nothing to select, nothing to prompt for.

### The import is not format-scoped, and cannot be made so

One rule decides what gets imported: **is this object a card?** If so it lands, with its complete `legalities` map, whatever that map says. Legality is never consulted, and there is no flag to change that.

Format scoping existed for a deployment that no longer exists. When every user hosted the corpus themselves, importing only the cards they needed saved *their* disk and *their* time. The corpus is hosted centrally now, so breadth is paid once, by us — and three arguments then point the same way:

- **Filtering costs more than it saves.** Commander-legal cards are 91% of the card objects in the file. The expensive stages are `metadata_generator.py` (one model call per card) and `embeddings.py`; that is where a format filter belongs, and applying it there needs no re-import.
- **A collection is not a legal deck.** CSV import resolves *owned* card names through the Knowledge API. 1,945 paper-printed cards are legal in no format at all, 1,244 of them Un-set cards. Excluding them would turn each into a permanent placeholder in somebody's collection, in an app that is explicitly proxy-friendly.
- **Format belongs to the client.** Deck building filters on `Card.legalities`, which every row carries whole, so a future Brawl or Oathbreaker mode is a client-side predicate rather than a re-import every user waits on.

One nuance the client-side filter will need, recorded here because the deleted `formats.py` used to own it: a **restricted** card is legal in Vintage, limited to one copy. A naive `legalities[fmt] == "legal"` check wrongly discards Black Lotus.

### What still gets excluded

`NON_CARD_LAYOUTS` in `card_filter.py` is the entire filter: tokens, double-faced tokens, emblems, art series, vanguards, schemes, planes, augments, hosts, and `front_card` (Jumpstart-style product dividers — `type_line` is literally `"Card"`, `set_type` is `memorabilia`). That removes 4,075 of the 2026-09-20 snapshot's 38,906 objects.

Two layouts that look like they belong on that list but don't: `prepare` cards are genuine split-style spells, and 352 `normal` cards are vanilla creatures with legitimately empty oracle text.

The filter **fails open** — an unrecognized layout is treated as a card. A layout Scryfall adds after this list was written is far likelier to be a new card type than a new kind of token, and a missing card is worse than a stray one.

### Pool sizes

The corpus holds all of it. The counts below come from the imported 2026-09-20 snapshot and are what a *downstream* filter would select — the numbers that matter when budgeting the AI stages.

| Pool | Cards |
|---|---|
| **imported (every card object)** | **34,831** |
| `legal:commander` | 31,830 |
| `legal:vintage` | 31,690 |
| `legal:legacy` | 31,672 |
| `legal:modern` | 22,450 |
| `legal:pioneer` | 14,817 |
| `legal:pauper` | 10,803 |
| `legal:standard` | 4,887 |
| legal in no format at all | 2,079 |

Commander is 91% of what's imported, which is exactly why scoping the *import* to it wasn't worth the cost. Where pool size does bite is `metadata_generator.py` (one model call per card) and `embeddings.py`, both proportional to whatever pool is selected *at that stage* — and those 2,079 never-legal cards are the obvious first thing to exclude there.

### Re-running is safe

- **Upsert, never replace.** Writes are `INSERT ... ON CONFLICT (oracle_id) DO UPDATE` in batches of `IMPORT_BATCH_SIZE`, so a weekly refresh updates rows in place and cannot disturb a collection or a deck. Duplicate `oracle_id`s within a batch (reversible cards, some promos) are collapsed first, because Postgres rejects an `ON CONFLICT` statement that touches the same key twice.
- **An import cannot delete a row.** `--prune` is gone. It existed to clean up after a *narrowed* import — drop the cards that fell outside the new format scope — and nothing narrows the import any more. What would have been left is a way to delete rows from a corpus every client reads, guarded only by a `card_metadata` check that couldn't see the collections and decks it was really protecting, since those live on users' machines (`data-model.md#two-databases-one-join-key`). A card Scryfall drops upstream now lingers as an unreferenced row, costing bytes; deleting it would break whoever owns that card, costing an install.

- **`--reset` is the one destructive path, and its confirmation guards the wrong-database case.** It used to refuse while `collection`/`decks` held rows. Those tables are not in this database, so the check couldn't see what it protected and was removed rather than left returning a reassuring zero. What replaced it: `--reset` prints the target URL and the number of cards it would delete, then requires that database's own name typed back. There is no `--force` — the flag existed only to override the user-data refusal, and a guard an unattended process can waive is not a guard. A non-interactive `--reset` always refuses.

  Two things that follow regardless:

  - **A client must tolerate an unresolvable `oracle_id`.** This is the same case as a restored backup or a card from a newer snapshot. Batch-resolve through the Knowledge API, render a placeholder, never crash.
  - **Prefer an operational gate over the prompt.** On the shared corpus a reset is a full wipe visible to every user at once; credentials that simply don't permit it beat any confirmation.

- **`--if-newer`** compares Scryfall's `updated_at` against the newest completed `ImportRun` and exits early, making a scheduled refresh cheap. A `--dry-run` deliberately writes no `ImportRun`, so it can't cause the next real import to be skipped.

Downloads are cached under `SCRYFALL_CACHE_DIR` (default `backend/data/scryfall/`, gitignored) with Scryfall's snapshot timestamp in the filename, written via a `.partial` file so an interrupted download is never mistaken for a complete one.

### Bulk data, not per-card requests

Scryfall publishes pre-built data dumps for exactly this use case — importing the full card pool — rather than paginating `/cards/search`. Fetch the bulk data object, then download the file it points to:

```
GET https://api.scryfall.com/bulk-data/oracle-cards
```

```json
{
  "object": "bulk_data",
  "type": "oracle_cards",
  "updated_at": "2026-08-02T09:02:31.886+00:00",
  "name": "Oracle Cards",
  "description": "A JSON file containing one Scryfall card object for each Oracle ID on Scryfall...",
  "jsonl_download_uri": "https://data.scryfall.io/oracle-cards/oracle-cards-20260802090231.jsonl.gz",
  "compressed_size": 24408680
}
```

- `jsonl_download_uri` is a gzipped **JSON Lines** file (one card object per line) — stream/decompress it rather than loading the whole thing into memory.
- Use the **`oracle_cards`** bulk type, not `default_cards` or `all_cards`: it returns exactly one card object per Oracle ID (the most recognizable printing), which matches our normalized `Card` model — we don't track per-printing/collector-number variance.
- Bulk data is regenerated every 12–24 hours. Scryfall's own guidance: gameplay data (name, oracle text, legality) changes slowly enough that weekly refreshes are sufficient; price fields go stale after 24h and shouldn't be relied on (irrelevant to us — Card has no price field).
- File downloads from `data.scryfall.io` are **not rate-limited**. The `/bulk-data/*` API endpoint itself falls under the standard 10 req/sec limit, but the importer only calls it once per run to get the current download URI.

Source: https://scryfall.com/docs/api/bulk-data

### Card object → our `Card` fields

Mapping from the Scryfall card object to `docs/data-model.md#card`:

| Card field | Scryfall field | Notes |
|---|---|---|
| **oracle_id** (PK) | `oracle_id` | stable across reprints — see below for why this, not `id`, is the primary key. Falls back to `card_faces[].oracle_id` for reversible cards |
| scryfall_id | `id` | the printing Scryfall chose for this oracle id. Kept for images and permalinks; **not** an identity, and expected to change |
| name | `name` | for multi-faced cards, the two face names joined with `" // "` |
| mana_cost | `mana_cost` | nullable. Root first, else faces joined `" // "`. `""` is normalized to `NULL` so lands aren't a third case |
| mana_value | `cmc` | Scryfall's field is literally named `cmc`; always at the root |
| oracle_text | `oracle_text` | nullable — 352 vanilla creatures genuinely have none. Root first, else faces merged (see below) |
| colors | `colors` | root first, else the union across faces |
| color_identity | `color_identity` | always present at card root, even for multi-faced cards — union of both faces. This is the field Commander legality checks depend on |
| type_line | `type_line` | for multi-faced cards this is both faces joined with `" // "` |
| power | `power` | nullable — creatures/vehicles only. Root first, else the relevant face's value. Kept as Scryfall's raw string (`"*"`, `"1+*"` are real values) rather than parsed to a number |
| toughness | `toughness` | same nullability and root-then-face fallback as `power` |
| loyalty | `loyalty` | nullable — planeswalkers only. Same root-then-face fallback as `power`/`toughness` |
| defense | `defense` | nullable — battle cards only. Same fallback rule |
| keywords | `keywords` | array of strings, e.g. `["Lifelink"]` |
| image_url | `image_uris.normal` | nullable. Root first, else the **front** face's image |
| layout | `layout` | drives the face-merge rules and tells downstream stages a card has a back side |
| legalities | `legalities` | the full map, stored whole |
| updated_at | — | when the importer last wrote this row |

Source: https://scryfall.com/docs/api/cards, https://scryfall.com/docs/api/cards/search

### Why `oracle_id` is the primary key

`oracle_cards` returns whichever printing Scryfall currently considers "most recognizable" for each oracle id — and **that choice changes when a card is reprinted**. Keying `Card` on the printing `id` would therefore rotate the primary key on an ordinary weekly refresh, orphaning every `collection`, `deck_cards`, and `decks.commander_id` row pointing at it. `oracle_id` is stable across reprints, so all four foreign keys target `cards.oracle_id`.

### Card faces (double-faced / split / flip cards)

The `layout` field tells you the card's shape (`"normal"`, `"transform"`, `"modal_dfc"`, `"split"`, `"flip"`, etc.). For any multi-faced layout, per-face data lives in a `card_faces` array instead of at the card root:

```json
{
  "name": "Hinterland Hermit // Hinterland Scourge",
  "layout": "transform",
  "cmc": 2,
  "color_identity": ["R"],
  "card_faces": [
    { "object": "card_face", "name": "Hinterland Hermit", "mana_cost": "{1}{R}",
      "type_line": "Creature — Human Werewolf", "oracle_text": "...", "colors": ["R"],
      "power": "2", "toughness": "1", "image_uris": { "...": "..." } },
    { "object": "card_face", "name": "Hinterland Scourge", "...": "..." }
  ]
}
```

Implications for the importer:

- `color_identity` and `cmc` are always safe to read from the card root regardless of layout.
- `mana_cost`, `oracle_text`, `colors`, `type_line`, and `image_uris` may be **missing or incomplete at the card root** on multi-faced cards. The importer reads the root first and falls back to `card_faces` — testing the value rather than the layout, since some `normal` cards also have empty fields.
- `power`/`toughness`/`loyalty`/`defense` follow the same root-then-face rule, and matter more here than for text fields: a transform creature's two faces routinely have *different* stats (see `Norman Osborn // Green Goblin`, 1/1 front and 3/3 back), so falling back to the wrong face silently produces a wrong number rather than an obviously-missing one.

**Merge rule (decision): both faces are kept, labelled by face name.** `Card` is single-valued, so face oracle texts are concatenated as `"{face name}\n{text}"` joined by `"\n//\n"`. Storing the front face only would be actively wrong here — the back of a modal DFC land is usually the reason to run it, and the metadata generator and knowledge document would be reasoning about half a card. As imported:

```
Witch Enchanter
When this creature enters, destroy target artifact or enchantment an opponent controls.
//
Witch-Blessed Meadow
As this land enters, you may pay 3 life. If you don't, it enters tapped.
{T}: Add {W}.
```

Labelling matters: an unlabelled concatenation of two rules boxes reads as one card with contradictory text. `mana_cost` and `type_line` use a plain `" // "` join, so the separator itself distinguishes which merge produced which field. **This resolves the open question previously flagged here, and applies to `document_generator.py` as well.**

Source: https://scryfall.com/docs/api/layouts, https://scryfall.com/docs/api/cards/search

### Legalities

`legalities` is an object keyed by format name, valued `"legal" | "not_legal" | "restricted" | "banned"`:

```json
"legalities": {
  "standard": "not_legal",
  "commander": "legal",
  "oathbreaker": "legal",
  ...
}
```

**Decision: the whole map is stored, as a single `legalities` JSON column on `Card`.** Not a boolean per format — a new format appearing upstream then needs no migration — and not Commander-only, because this column is now the *only* thing that knows about formats. Every format question, on the client or at the AI stages, is answered from it. Postgres queries it directly:

```sql
select count(*) from cards where legalities->>'commander' = 'legal';
```

Note `"restricted"` is a legality *value*, not a separate state to ignore: a restricted card is legal in its format, limited to one copy. Nothing in the importer acts on that — it stores the map and moves on — but whatever filters by format on the client has to, or a `== "legal"` check silently discards Black Lotus from Vintage.

Source: https://scryfall.com/docs/api/cards/search

### Rate limits

Only relevant if the importer ever falls back to per-card endpoints (e.g. `/cards/named` for a manual lookup/backfill) rather than the bulk file:

- Most endpoints: 10 requests/second.
- `/cards/search`, `/cards/named`, `/cards/random`, `/cards/collection`: 2 requests/second.
- Exceeding a limit returns `429` and a 30-second restriction; repeated overloading risks a ban. Back off on `429` rather than retrying immediately.

Source: https://scryfall.com/docs/api/rate-limits
