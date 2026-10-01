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

AI-generated strategic information, produced **once, centrally** per card by the Knowledge Pipeline's metadata generation step. This is the expensive stage that centralizing the knowledge base exists to spare every user from running.

**Generation is tiered, not single-model.** One model call per card across the whole corpus doesn't imply the *same* model for every card: a local model generates the majority, and only cards a benchmark shows it gets wrong escalate to a frontier model. Whichever tier produces a given row, the shape below is identical — the row doesn't record which model wrote it. Where that tiering line falls is decided by a 300-card benchmark before any full-corpus run (`benchmarking-and-testing.md`).

| Field        | Notes                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| card_id      | FK to Card                                                                                                                                                                                  |
| summary      |                                                                                                                                                                                             |
| roles        | closed vocabulary — see *Controlled vocabulary* below. Multi-valued                                                                                                                        |
| themes       | closed vocabulary, same mechanism as `roles`. Multi-valued                                                                                                                                 |
| game_stage   | `GameStageProfile` — independent `early`/`mid`/`late` scores, not a single label. See below                                                                                                |
| power_rating | 1–10, calibrated against the five-rung anchor ladder for the card's dominant role/theme — see *Anchor cards* below                                                                        |
| strengths    |                                                                                                                                                                                             |
| weaknesses   |                                                                                                                                                                                             |
| synergy_tags | closed vocabulary, same mechanism as `roles`                                                                                                                                               |
| updated_at   | when the metadata generator last wrote this row — lets a refresh detect metadata that predates the `Card.updated_at` it describes (e.g. after an oracle text errata) and needs regenerating |

### Controlled vocabulary: roles, themes, synergy_tags

`roles`, `themes`, and `synergy_tags` are each a **closed enum**, not free text. Nothing forces ~31,830 independent model calls to describe the same concept with the same string — "Ramp", "Mana Ramp", and "Mana Acceleration" would otherwise all land as distinct values, which silently breaks retrieval filtering and the deck builder's role-based grouping (`#carddocument`'s `filter_fields`, `#cardcache`'s `roles`). The model is shown the exact closed list it must choose from in the prompt itself; it free-generates `summary`/`strengths`/`weaknesses` but never these three fields.

Example (Cultivate): roles `[Ramp, Mana Fixing]`, themes `[Big Mana, Landfall]`.

#### A member has to be decidable from the card

Closing the vocabulary only buys consistency if every member is a question the model can actually answer from the one card in front of it. Generation is stateless and per-card: the prompt carries that card's facts and nothing about a deck, a curve, or a game plan. A member describing a property the card doesn't individually have can't be assigned correctly, only guessed at — and because the enum is closed, the guess comes back as a confident, valid-looking value that retrieval will filter on.

**`Theme.MIDRANGE` was removed for exactly this reason.** "Midrange" describes a deck's posture over a whole game — a curve that trades early and takes over in the middle. No printed card *is* midrange; a three-mana value creature is equally at home in an aggro, control, or combo shell. An anchor ladder was built for it during anchor selection and came back self-labeled as not honest: every rung would be graded by a property the card doesn't carry, so a rater reading only the card would tag each one by its real function instead. Dropping the member was the fix; anchoring it would have shipped five reference cards teaching the model to answer an unanswerable question.

**`Theme.FLYING` was removed too, but for a different reason** — worth recording because it is a second, subtler way a member fails. Flying is perfectly decidable from a card; the problem was the ladder. The theme is meant to collect cards *paid off* by evasion, and almost every card that reads as a strong flying card is one that *grants* it — so the rungs kept grading a different question than the tag asks, most visibly at the top of the scale, where the payoff side has nearly nothing. A member can be decidable and still be unanchorable. Evasion is better carried as a property of a card than as an archetype of its own.

Apply the same test before adding a member. `Theme.CONTROL` and `Theme.AGGRO` sit close to this line and were kept deliberately — a card can *support* those strategies in ways its text shows (a tax permanent, a pod-wide damage payoff), even though the archetype itself is a deck property. The distinction is whether the card's own text is evidence for the tag, not whether the tag names something real about decks.

### game_stage: GameStageProfile

Not a single Early/Mid/Late label — a card can be strong in more than one phase, and a single label forces a false choice for anything that stays live all game (a mana rock is early *and* mid *and* late). `game_stage` is a fixed-shape object with three independent 1–10 scores:

```python
class GameStageProfile(BaseModel):
    early: float  # 1-10
    mid: float    # 1-10
    late: float   # 1-10
```

The three scores are **not** a distribution — they don't sum to anything fixed, and a card can legitimately score high on all three (Sol Ring) or low on all three (a narrow, situational answer). Stored as JSON, validated against this fixed shape rather than an open dict, so generation can't invent extra keys. Example (Cultivate): `{early: 9, mid: 4, late: 1}` — a card that's only good for one thing, and that thing matters most early.

### Anchor cards

`power_rating` and the role/theme assignments are otherwise ungrounded: each generation call is stateless, so nothing keeps two calls using the 1–10 scale the same way or agreeing on the boundary between adjacent roles. Anchors are the fix — hand-picked, hand-labeled cards embedded directly in the generation prompt's cached system block, so every call, on every model tier, sees the same fixed reference points.

**Each `roles` and `themes` value gets a *ladder* of five anchor cards, not one.** One card per band of the 1–10 scale: 1-2, 3-4, 5-6, 7-8, 9-10.

A single labeled example per tag was the original design, and it doesn't work. Telling the model "Cultivate is a 6 for Ramp" pins exactly one point on the scale and says nothing about the rest of it: the model has no reference for what a 3 or a 9 looks like, so everything away from that one point is back to freehand judgment — which is the drift the anchors existed to stop, just displaced to the ends of the range. A labeled point is not a calibrated scale. Five graded points *are* one: they turn an abstract judgment ("how good is this, out of ten?") into a comparison against concrete cards, which is the kind of question a language model answers consistently.

The bands are two points wide rather than one so a ladder is five cards instead of ten. Five is enough to interpolate between and short enough to keep in a cached system block across all 51 tags; ten would double the block for a precision `power_rating` does not carry anyway.

#### The band rubric

What each band means. This is the fixed rubric anchor candidates are picked against, and the one a reviewer checks a proposed rung with — deliberately stated here in prose rather than baked into `PowerBand`'s member names in code, so it can be revised without a code change.

| Band | Meaning |
|---|---|
| 1-2 | Effectively unplayable in Commander — too slow, too small an effect, or strictly outclassed by a common |
| 3-4 | Filler: playable in a budget list or a very specific build, cut from most decks that want the effect |
| 5-6 | Solidly playable: a reasonable inclusion in any deck that wants this effect |
| 7-8 | Strong staple: most decks that can play it do |
| 9-10 | Format-defining: warps deckbuilding around itself, or wins the game on its own |

Two rules about reading that table. Both are easy to get wrong by default, and both change which cards belong in which band:

- **Universality is not power.** A cheap colorless card that every deck can play is a *staple*, not a format-definer — breadth of playability and magnitude of effect are different axes, and only the second one is what 9-10 measures. Sol Ring is the worked example: it is in more decks than any other card in the format and it belongs at **7-8**, not 10. It accelerates; it does not warp a game around itself or win one on the spot. The top band is reserved for effects that do.
- **Cards are judged in a four-player pod**, not in 1v1. Life totals are 40, games are long, and a card's value shifts accordingly: single-target removal is worth less when there are three opponents, symmetrical effects and board wipes are worth more, and a two-card combo that ends the game outright is worth far more than its 1v1 reputation suggests.

#### A ladder is complete or it is absent

`AnchorLadder` refuses to construct with fewer than five rungs, or with two rungs claiming the same band. A partial ladder is worse than no ladder at all: the missing band is precisely the region the model has to guess at, and the four rungs around it make that guess *look* calibrated — in the output there is nothing to distinguish an interpolated rating from an anchored one. No ladder at least fails visibly, and the prompt tells the model to fall back on its own consistent judgment when a tag has none. So a tag is either fully anchored or openly unanchored; there is no partially-calibrated state. `missing_tags()` reports which tags are in the second category, and a full-corpus run should not start before that list has been read.

#### How the model uses a ladder

The prompt instructs it not to score `power_rating` freehand. It finds a ladder for a role or theme the card it is rating shares, reads the card against those five rungs, and places it where it falls between them — if it is clearly better than the 5-6 rung and clearly worse than the 9-10 rung, it is a 7 or an 8. Where a card shares several tags that have ladders, it calibrates against the one its strongest effect belongs to. That is a comparison, not a judgment call against an abstract scale, and it is the difference between a rating that means the same thing in call 30,000 as it did in call 1.

Anchors are drawn from the "easy" bucket of the benchmarking sample (`benchmarking-and-testing.md#ground-truth`), or picked to that bucket's standard: cards whose role, theme, and power level are obvious enough that hand-labeling them isn't itself a judgment call. Five rungs across 51 tags is 255 cards, more than a 100-card bucket can supply, so the bucket sets the bar rather than the boundary. Changing a rung's assigned rating shifts the scale for every card sharing that role or theme, so anchors are a deliberate, reviewed edit, not a casual one.

**The registry now ships populated** — all 51 ladders, 255 distinct cards, in `backend/knowledge_pipeline/metadata_generator/anchors.py`. It shipped empty until a human had reviewed the candidates, because a guessed anchor is indistinguishable from a reviewed one in the generated corpus. How the 255 were settled, and why the shape of that process matters more than its output:

- **A corpus search proposed candidates, four per rung.** Every one was resolved against the `cards` table, so each rung's name, oracle text, and Commander legality are the corpus's own rather than a recollection — a misremembered anchor would be a silent, permanent error in every rating that reads it.
- **Candidates were ranked on ruler-mark properties, not card quality**: whether the tag is visible in the card's own oracle text (the only evidence the model gets), whether the rating sits unambiguously inside its band, how fast the text reads, and how much a rung differs from its neighbours.
- **A human chose every rung.** The ranking left the top two candidates exactly tied in 101 of the 260 rungs it ranked, so it narrowed the field and did not decide it. Treat the ranking as triage.
- **Two whole-set rules**, enforced by tests rather than by comment: no card carries two different ratings across ladders, and no card anchors two ladders. The first prevents one prompt from teaching that the scale depends on which ladder you read; the second keeps 255 independent reference points, and keeps the same card off both sides of the `Role`/`Theme` pairs that share a word.

Every step of that is a tool, not a one-off: `knowledge_pipeline/anchor_bench` (`benchmarking-and-testing.md#recalibrating-the-anchors`) validates a candidate pool against the corpus, ranks it, builds the review page, and renders the reviewed result back into `anchors.py`. Revising one rung or recalibrating all 255 starts there. The pool these ladders were chosen from is deliberately *not* kept — it would go stale against a later corpus, and a fresh one is cheap now that the tooling exists.

## CardDocument

The generated knowledge document for a card and its embedding — the retrieval surface. Produced by the document generation and embedding stages, and the table `POST /v1/retrieve` queries.

Document, filter fields, and vector live in the same Postgres as the card they describe, so retrieval is one query with both similarity and hard filters, and a pipeline run writes the relational rows and the vector together (`architecture.md#why-pgvector-and-not-chromadb`).

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

An import adds and updates; it never deletes. A `--dry-run` deliberately writes no row, so it can't cause the next real import to be skipped. `source_updated_at` is also what the Knowledge API reports as `scryfall_updated_at` in `/v1/meta`.

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
