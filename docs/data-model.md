# Normalized Data Model

The application treats Magic knowledge as structured data first, AI reasoning second. These are the core entities the backend persists. See `architecture.md` for how they flow through the pipelines.

## Card

Source of truth, imported directly from Scryfall. Never AI-generated. Written by the Scryfall importer — see `knowledge-pipeline.md#scryfall-importer` for the field-by-field mapping and the multi-faced-card merge rules.

| Field | Notes |
|---|---|
| **oracle_id** | **primary key.** Stable across reprints — see below |
| scryfall_id | the printing Scryfall picked for this oracle id. For images and permalinks; not an identity, and expected to change over time |
| name | multi-faced cards use both face names joined with `" // "` |
| mana_cost | **nullable** — absent at the card root on multi-faced layouts, and lands have none |
| mana_value | |
| oracle_text | **nullable** — vanilla creatures genuinely have none. Multi-faced cards store both faces, labelled by face name |
| colors | |
| color_identity | used for Commander legality checks |
| type_line | |
| keywords | |
| image_url | **nullable**. Front face for multi-faced cards |
| layout | Scryfall's shape discriminator (`normal`, `transform`, `modal_dfc`, `split`, …) |
| legalities | the full Scryfall legality map, e.g. `{"commander": "legal", "standard": "not_legal"}`. One column rather than a boolean per format, so a new format upstream needs no migration |
| updated_at | when the importer last wrote this row |

**The primary key is `oracle_id`, not Scryfall's printing `id`.** The `oracle_cards` bulk file returns whichever printing is currently "most recognizable" for each oracle id, and that choice changes when a card is reprinted. Keying on the printing id would rotate the primary key on an ordinary refresh and orphan every collection and deck row referencing it. All four foreign keys below therefore target `cards.oracle_id`.

## CardMetadata

AI-generated strategic information, produced once per card by the Knowledge Pipeline's metadata generation step.

| Field | Notes |
|---|---|
| card_id | FK to Card |
| summary | |
| roles | e.g. Ramp, Removal, Card Draw |
| themes | e.g. Big Mana, Landfall |
| game_stage | Early / Mid / Late |
| power_rating | |
| strengths | |
| weaknesses | |
| synergy_tags | |

Example (Cultivate): roles `[Ramp, Mana Fixing]`, themes `[Big Mana, Landfall]`, game_stage `Early`.

## Collection

Tracks the cards a user owns.

| Field | Notes |
|---|---|
| user_id | |
| card_id | FK to Card |
| quantity | |

## Deck

A user's saved deck — whether AI-generated or built by hand in the deck builder. Product rule: **a user may keep at most 100 saved decks** (`MAX_DECKS`, mirrored in `frontend/src/lib/types.ts`); the backend rejects creates past the cap, the UI disables its create button.

| Field | Notes |
|---|---|
| id | |
| user_id | |
| name | user-chosen on first save |
| commander_id | FK to Card; nullable — a work-in-progress deck may not have one yet |
| created_at | |
| updated_at | saving an existing deck updates in place rather than creating a duplicate |

## DeckCard

Cards inside a saved deck.

| Field | Notes |
|---|---|
| deck_id | FK to Deck |
| card_id | FK to Card |
| quantity | 1 for everything except basic lands (singleton format) |
| owned | whether the user already owns this card |
| proxy | whether this card is recommended as a proxy |

## ImportRun

One execution of the Scryfall importer. Exists so a scheduled refresh can skip a snapshot it has already consumed (`--if-newer`), and so an operator can see what a past import did.

| Field | Notes |
|---|---|
| id | autoincrement |
| bulk_type | which Scryfall bulk file, e.g. `oracle_cards` |
| format_profile | the pool this run imported, e.g. `all` or `commander` |
| source_updated_at | Scryfall's own timestamp for the snapshot consumed |
| cards_seen / cards_written / cards_skipped / cards_pruned | counts |
| started_at | |
| finished_at | nullable — still null while a run is in flight or if it failed |

A `--dry-run` deliberately writes no row, so it can't cause the next real import to be skipped.

---

Schema changes are managed by Alembic (`backend/alembic/`); `alembic upgrade head` applies them, and the backend container runs it on start. Keep this file in sync with `backend/database/models.py` and the migrations — where they disagree, the code is right and this file is the bug.

### Known drift

`backend/database/models.py` is still behind this document in three places, unrelated to the importer: `Deck` has no `name` and no `updated_at`, its `commander_id` is non-nullable, and `DeckCard` has no `quantity`. These want a migration when the deck routes are implemented.
