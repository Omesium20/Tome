# Normalized Data Model

The application treats Magic knowledge as structured data first, AI reasoning second. These are the core entities the backend persists. See `architecture.md` for how they flow through the pipelines.

## Card

Source of truth, imported directly from Scryfall. Never AI-generated.

| Field | Notes |
|---|---|
| id | |
| oracle_id | |
| name | |
| mana_cost | |
| mana_value | |
| oracle_text | |
| colors | |
| color_identity | used for Commander legality checks |
| type_line | |
| keywords | |
| image_url | |

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

---

Keep this file in sync with the actual ORM models/migrations once the database layer is implemented — this is the intended shape, not necessarily the literal schema.
