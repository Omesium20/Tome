# Normalized Data Model

The application treats Magic knowledge as structured data first, AI reasoning second. These are the core entities the backend persists. See `architecture.md` for how they flow through the pipelines.

## Two databases, one join key

The entities below live in **two different databases with different operators** (`architecture.md#the-split-shared-knowledge-local-generation`). Which one an entity belongs to determines who writes it, how it migrates, and whether its foreign keys are real.

| | Knowledge database (cloud) | Local database (client) |
|---|---|---|
| Entities | `Card` · `CardMetadata` · `CardDocument` · `ImportRun` | `Collection` · `Deck` · `DeckCard` · `CardCache` |
| Hosted by | Tome maintainers, one shared Postgres + `pgvector` | The user, on their machine (SQLite or Postgres) |
| Written by | The Knowledge Pipeline only | The user, through the local API |
| Read by | The Knowledge API (read-only role); clients reach it over HTTPS | The local backend, directly |
| Models | `backend/database/knowledge/models.py` | `backend/database/local/models.py` |
| Sessions | `backend/database/knowledge/session.py` | `backend/database/local/session.py` |
| Settings | `KnowledgeSettings` / `KNOWLEDGE_DATABASE_URL` | `LocalSettings` / `LOCAL_DATABASE_URL` |
| Migrations | `alembic -n knowledge` → `backend/alembic/knowledge/` | `alembic -n local` → `backend/alembic/local/` |

**`cards.oracle_id` is the join key across that boundary, and the boundary means no foreign key can enforce it.** `Collection.card_id`, `DeckCard.card_id`, and `Deck.commander_id` are *logical* references to a row in a database the local engine cannot see. Three rules follow, and nothing but code will enforce them:

- Never key user rows on anything but `oracle_id`. It is reprint-stable (see below); a printing ID would rotate and orphan user data across a boundary where nothing cascades.
- Treat a missing card as a normal case, not an error. A restored backup or a card printed since the last refresh will reference something the local cache doesn't have — resolve it through the Knowledge API in batch, render a placeholder if it's genuinely unknown.
- `CardCache` is a cache, never a source of truth. It can be deleted and rebuilt from the API at any time.

---

# Knowledge database (cloud)

Written once, centrally, by the Knowledge Pipeline. Identical for every user. Clients only ever read it, and only through the Knowledge API (`knowledge-api.md`).

## Card

Source of truth, imported directly from Scryfall. Never AI-generated. Written by the Scryfall importer — see `knowledge-pipeline.md#scryfall-importer` for the field-by-field mapping and the multi-faced-card merge rules.

**Shape of the source data.** The importer streams Scryfall's `oracle_cards` bulk file (one JSON object per Oracle ID) rather than paginating the search API — see `knowledge-pipeline.md#bulk-data-not-per-card-requests` for the endpoint and caching details. A representative object (trimmed to the fields `Card` actually maps, per Scryfall's card object docs, https://scryfall.com/docs/api/cards):

```json
{
  "object": "card",
  "id": "56ea32e5-b164-4a8d-9312-c35ae5cd8b2a",
  "oracle_id": "dd21cbaa-7537-414b-b720-3c7d6fc7c93d",
  "name": "Orcish Hellraiser",
  "layout": "normal",
  "mana_cost": "{1}{R}",
  "cmc": 2,
  "type_line": "Creature — Orc Warrior",
  "oracle_text": "Echo {R} ...\nWhen this creature dies, it deals 2 damage to target player or planeswalker.",
  "power": "3",
  "toughness": "2",
  "colors": ["R"],
  "color_identity": ["R"],
  "keywords": ["Echo"],
  "image_uris": { "normal": "https://cards.scryfall.io/normal/front/5/6/56ea32e5....jpg" },
  "legalities": { "commander": "legal", "standard": "not_legal", "...": "..." },
  "card_faces": null
}
```

Multi-faced cards (`layout` of `transform`, `modal_dfc`, `split`, …) instead carry per-face data in a `card_faces` array — each face can have its own `power`/`toughness`/`loyalty` as well as its own text and mana cost. Full merge rules: `knowledge-pipeline.md#card-faces-double-faced--split--flip-cards`.

| Field          | Notes                                                                                                                                                                            |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **oracle_id**  | **primary key.** Stable across reprints — see below                                                                                                                              |
| scryfall_id    | the printing Scryfall picked for this oracle id. For images and permalinks; not an identity, and expected to change over time                                                    |
| name           | multi-faced cards use both face names joined with `" // "`                                                                                                                       |
| mana_cost      | **nullable** — absent at the card root on multi-faced layouts, and lands have none                                                                                               |
| mana_value     |                                                                                                                                                                                  |
| oracle_text    | **nullable** — vanilla creatures genuinely have none. Multi-faced cards store both faces, labelled by face name                                                                  |
| colors         |                                                                                                                                                                                  |
| color_identity | used for Commander legality checks                                                                                                                                               |
| type_line      |                                                                                                                                                                                  |
| power          | **nullable** — creatures (and vehicles) only. Root first, else the relevant face's value for multi-faced creatures (e.g. a transform creature with different stats per side). Stored as Scryfall's string as-is (`"*"`, `"1+*"` are valid values, not just integers) — parsing to a number for combat math is a validation-layer concern, not import |
| toughness      | same nullability and root-then-face fallback as `power`                                                                                                                          |
| loyalty        | **nullable** — planeswalkers only. Same root-then-face fallback as `power`/`toughness`                                                                                           |
| defense        | **nullable** — battle cards only (a newer card type; `type_line` contains `Battle`). Same fallback rule                                                                          |
| keywords       |                                                                                                                                                                                  |
| image_url      | **nullable**. Front face for multi-faced cards                                                                                                                                   |
| layout         | Scryfall's shape discriminator (`normal`, `transform`, `modal_dfc`, `split`, …)                                                                                                  |
| legalities     | the full Scryfall legality map, e.g. `{"commander": "legal", "standard": "not_legal"}`. One column rather than a boolean per format, so a new format upstream needs no migration |
| updated_at     | when the importer last wrote this row                                                                                                                                            |

**The primary key is `oracle_id`, not Scryfall's printing `id`.** The `oracle_cards` bulk file returns whichever printing is currently "most recognizable" for each oracle id, and that choice changes when a card is reprinted. Keying on the printing id would rotate the primary key on an ordinary refresh and orphan every collection and deck row referencing it. All four foreign keys below therefore target `cards.oracle_id`.

Deliberately **not** stored, because it lives on the printing rather than the Oracle ID and this model doesn't track per-printing/collector-number variance: `set`, `collector_number`, `rarity`, `artist`, `released_at`, `prices`, `multiverse_ids`/`tcgplayer_id`/`cardmarket_id`. See `knowledge-pipeline.md#bulk-data-not-per-card-requests` for why `oracle_cards` (not `default_cards`/`all_cards`) is the right bulk type for that reason.

## CardMetadata

AI-generated strategic information, produced **once, centrally** per card by the Knowledge Pipeline's metadata generation step — one model call per card across the whole corpus. This is the expensive stage that centralizing the knowledge base exists to spare every user from running. Generated against a frontier model: it sets the ceiling on retrieval quality for everyone, so it is not a place to economize (`architecture.md#knowledge-pipeline`).

| Field        | Notes                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| card_id      | FK to Card                                                                                                                                                                                  |
| summary      |                                                                                                                                                                                             |
| roles        | e.g. Ramp, Removal, Card Draw                                                                                                                                                               |
| themes       | e.g. Big Mana, Landfall                                                                                                                                                                     |
| game_stage   | Early / Mid / Late                                                                                                                                                                          |
| power_rating |                                                                                                                                                                                             |
| strengths    |                                                                                                                                                                                             |
| weaknesses   |                                                                                                                                                                                             |
| synergy_tags |                                                                                                                                                                                             |
| updated_at   | when the metadata generator last wrote this row — lets a refresh detect metadata that predates the `Card.updated_at` it describes (e.g. after an oracle text errata) and needs regenerating |

Example (Cultivate): roles `[Ramp, Mana Fixing]`, themes `[Big Mana, Landfall]`, game_stage `Early`.

## CardDocument

The generated knowledge document for a card and its embedding — the retrieval surface. Produced by the document generation and embedding stages, and the table `POST /v1/retrieve` queries.

This replaces the per-user ChromaDB collection. Document, filter fields, and vector live in the same Postgres as the card they describe, so retrieval is one query with both similarity and hard filters, and a pipeline run writes the relational rows and the vector together (`architecture.md#why-pgvector-and-not-chromadb`).

| Field | Notes |
|---|---|
| card_id | FK to Card; primary key — one document per card |
| document | the generated natural-language knowledge document, built from Card + CardMetadata. Not hand-written Markdown |
| embedding | `pgvector` `VECTOR(n)`, where `n` is the embedding model's dimension (384 for `all-MiniLM-L6-v2`). Indexed HNSW with `vector_cosine_ops`; queries must order by `cosine_distance` or the index is silently skipped |
| embedding_model | which model produced the vector. A corpus embedded by mixed model versions returns nonsense, so a model change is a full re-embed, and this column is how that's detected |
| filter_fields | the structured fields retrieval filters on before/alongside similarity — color identity, format legality, mana value, roles, themes |
| updated_at | when the embedding stage last wrote this row; lets a refresh find documents older than the `Card` or `CardMetadata` they describe |

Column type and index follow the official pgvector SQLAlchemy docs (Context7 `/pgvector/pgvector-python`) — see `architecture.md#why-pgvector-and-not-chromadb` for the exact declaration.

## ImportRun

One execution of the Scryfall importer. Exists so a scheduled refresh can skip a snapshot it has already consumed (`--if-newer`), and so an operator can see what a past import did.

| Field | Notes |
|---|---|
| id | autoincrement |
| bulk_type | which Scryfall bulk file, e.g. `oracle_cards` |
| source_updated_at | Scryfall's own timestamp for the snapshot consumed |
| cards_seen / cards_written / cards_skipped | counts |
| started_at | |
| finished_at | nullable — still null while a run is in flight or if it failed |

`format_profile` and `cards_pruned` were dropped when format scoping was removed: every run now takes the whole pool, so the first could hold only one value, and the second counted deletions from a `--prune` that no longer exists. An import adds and updates; it never deletes. A `--dry-run` deliberately writes no row, so it can't cause the next real import to be skipped. `source_updated_at` is also what the Knowledge API reports as `scryfall_updated_at` in `/v1/meta`.

---

# Local database (client)

Private to one user, on their machine. Never transmitted anywhere — the client asks the Knowledge API about *cards*, never about the user (`architecture.md#data-boundary-two-databases-one-join-key`).

`user_id` is retained on these tables but is vestigial in the local-first model: a local database has exactly one user. It stays because it costs nothing and is the seam a future multi-user or sync deployment would need.

## Collection

Tracks the cards a user owns.

| Field | Notes |
|---|---|
| user_id | |
| card_id | **logical** reference to `cards.oracle_id` in the cloud database — not an enforced FK |
| quantity | |

Populated by CSV import, which arrives as card *names* and resolves them to oracle IDs through `POST /v1/cards/resolve` (`knowledge-api.md`). Names that are ambiguous or unmatched are surfaced to the user rather than guessed at.

## Deck

A user's saved deck — whether AI-generated or built by hand in the deck builder. Product rule: **a user may keep at most 100 saved decks** (`MAX_DECKS`, mirrored in `frontend/src/lib/types.ts`); the backend rejects creates past the cap, the UI disables its create button.

| Field | Notes |
|---|---|
| id | |
| user_id | |
| name | user-chosen on first save |
| commander_id | **logical** reference to `cards.oracle_id`; nullable — a work-in-progress deck may not have one yet |
| created_at | |
| updated_at | saving an existing deck updates in place rather than creating a duplicate |

## DeckCard

Cards inside a saved deck.

| Field | Notes |
|---|---|
| deck_id | FK to Deck — a real FK; both tables are local |
| card_id | **logical** reference to `cards.oracle_id` in the cloud database |
| quantity | 1 for everything except basic lands (singleton format) |
| owned | whether the user already owns this card |
| proxy | whether this card is recommended as a proxy |

## CardCache

Display data for every card this client has seen, so rendering a collection or a deck is a local read rather than one API call per card.

| Field | Notes |
|---|---|
| oracle_id | primary key; mirrors `cards.oracle_id` |
| *card display fields* | name, mana_cost, mana_value, colors, color_identity, type_line, oracle_text, image_url, legalities — the subset the UI and the local validator need |
| roles | the `CardMetadata` fields the deck builder groups its columns by |
| snapshot_version | the `/v1/meta` snapshot this row came from, so staleness is detectable per row |
| fetched_at | when the client last refreshed this row |

**This is a cache, not a source of truth.** It is populated opportunistically from `POST /v1/cards/batch` and `POST /v1/retrieve` responses, refreshed lazily when `/v1/meta` reports a new `snapshot_version`, and can be deleted wholesale without data loss. It is also what lets the local validator check color identity and legality without a network round trip — and what keeps collection browsing and hand-editing decks working while the Knowledge API is unreachable.

---

Schema changes are managed by Alembic. The two databases migrate independently: `backend/alembic/knowledge/` against the cloud Postgres (run by maintainers, after a schema change to the knowledge model), `backend/alembic/local/` against the user’s own database (run on client start, as the backend container already does). A client upgrade must never require a cloud migration to land first — that coupling is what the Knowledge API’s versioning exists to avoid (`knowledge-api.md#versioning`).

Keep this file in sync with `backend/database/knowledge_models.py` and `backend/database/local_models.py` and the migrations — where they disagree, the code is right and this file is the bug.
