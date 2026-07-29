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

A generated deck.

| Field | Notes |
|---|---|
| id | |
| user_id | |
| commander_id | FK to Card |
| created_at | |

## DeckCard

Cards inside a generated deck.

| Field | Notes |
|---|---|
| deck_id | FK to Deck |
| card_id | FK to Card |
| owned | whether the user already owns this card |
| proxy | whether this card is recommended as a proxy |

---

Keep this file in sync with the actual ORM models/migrations once the database layer is implemented — this is the intended shape, not necessarily the literal schema.
