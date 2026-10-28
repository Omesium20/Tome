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

**Generation takes two calls per card, and the cut is deliberate.** Pass one produces `mechanics` through `game_stage`; pass two is handed the card, pass one's answers, and *only* the anchor ladders for the roles and themes pass one actually assigned, and produces `power_rating` and `summary`. The single call they replaced carried all 66 ladders every time, and that dilution is measured, not suspected — see *Anchor cards* below and `lessons-learned.md`. The row written is identical either way; `CardMetadataBlueprint` remains the one shape that reaches this table.

**Generation is also tiered, not single-model.** Two calls per card across the whole corpus doesn't imply the *same* model for every card: a local model generates the majority, and only cards a benchmark shows it gets wrong escalate to a frontier model. Whichever tier produces a given row, the shape below is identical — the row doesn't record which model wrote it, or which pass wrote which field. Where that tiering line falls is decided by a 300-card benchmark before any full-corpus run (`benchmarking-and-testing.md`).

| Field        | Notes                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| card_id      | FK to Card                                                                                                                                                                                  |
| mechanics    | a literal, plain-language restatement of the card's rules text — what it costs, when it can be used, what triggers it, what it does, and every condition on that. The model's **first** field, and the one with no judgment in it. See *mechanics* below |
| synergy_tags | closed vocabulary, same mechanism as `roles`. Answered right off `mechanics`, before `roles`/`themes`                                                                                      |
| roles        | closed vocabulary — see *Controlled vocabulary* below. Multi-valued, at most **5**                                                                                                        |
| primary_role | the single `roles` member the card is most defined by, asked for directly rather than read off `roles[0]`. Nullable — a card can honestly have no role. Decides which ladder pass two calibrates against; see *Which ladder a card is shown* below |
| themes       | closed vocabulary, same mechanism as `roles`. Multi-valued, at most **5**                                                                                                                 |
| strengths    | grounded in the `roles`/`themes` just assigned, not the oracle text directly                                                                                                               |
| weaknesses   |                                                                                                                                                                                             |
| game_stage   | `GameStageProfile` — independent `early`/`mid`/`late` scores, not a single label. See below                                                                                                |
| power_rating | 1–10, calibrated against the five-rung anchor ladder for the card's dominant role/theme — see *Anchor cards* below. Pass two's first field                                               |
| summary      | the **last** field written — once every tag and `power_rating` are already decided, so it restates that judgment rather than anticipating it. In pass two for that reason                |
| updated_at   | when the metadata generator last wrote this row — lets a refresh detect metadata that predates the `Card.updated_at` it describes (e.g. after an oracle text errata) and needs regenerating |

### mechanics: the model reads before it judges

`mechanics` is a restatement, not an analysis: the card's rules text in plain language, including the costs, timing windows, triggers and restrictions, with the keywords expanded ("Landfall — whenever a land enters the battlefield under your control"). Nothing in it is a claim about whether the card is good, which deck wants it, or what it combos with. Every other field in the row is that kind of claim.

**It is the first field generated, and that ordering is the point.** Both model backends decode against a Pydantic model's JSON Schema — the Anthropic SDK builds the schema from the class, Ollama turns it into a decoding grammar — and a JSON object is emitted in property order. Declaring `mechanics` first means the model has written out what the card actually says *before* it reaches `roles`, `themes` or `game_stage`, so those answers are made against a reading it committed to rather than against the card's name. Declared last, the same field would restate conclusions already drawn and change nothing about how they were reached. `metadata_generator/schema.py` and `tests/test_metadata_schema.py` both pin the position for that reason.

The full field order is a reasoning chain, not just `mechanics` first: `mechanics` → `synergy_tags` (the narrowest mechanical hooks, read straight off the restatement) → `roles` → `primary_role` (which of those roles defines the card, chosen while the list is still the last thing written) → `themes` → `strengths`/`weaknesses` (qualitative, grounded in the roles/themes just assigned) → `game_stage` → `power_rating` (calibrated against the anchor ladder for whichever role/theme the card was just given) → `summary` last, once every tag and the rating are already decided. `summary` is the one field built to be a trailing restatement — nothing downstream is answered against it the way everything is answered against `mechanics`.

**The two generation passes cut this chain on a step boundary, which strengthens it rather than breaking it.** `MetadataPassOne` is `mechanics` → `game_stage`; `MetadataPassTwo` is `power_rating` → `summary`. Everything needed to *choose* an anchor ladder is decided by the end of pass one, which is exactly why the cut falls there — pass two's ladders are selected from the `roles` and `themes` pass one returned. Within each pass, property order still does its work; across the two, pass two additionally *cannot* reach back and revise a tag its own calibration ladders were chosen from. `CardMetadataBlueprint` inherits the first seven fields from `MetadataPassOne` rather than restating them, so the finished row keeps the exact nine-field order above and the two definitions cannot drift.

This is also the field that makes a bad row diagnosable. A `power_rating` that looks wrong is otherwise an unexplained number; next to a restatement, it is either a misreading of the card (visible in `mechanics`) or a miscalibration against the anchor ladder (`mechanics` right, rating wrong) — and those have different fixes. It matters most for the local tier, where the benchmark (`benchmarking-and-testing.md`) is trying to find out *which* cards a small model gets wrong: a model that cannot restate a card has no business scoring it, and that failure is now legible in the row instead of inferred from the score.

It is stored rather than discarded after generation for the same reason, plus one more: the document generator builds `CardDocument` text from this row, and a human-readable rules reading is better embedding input than oracle text full of symbol notation.

Deliberately **not** derived in code. A regex over oracle text can find a trigger, but "every condition attached to that effect, in plain language" is exactly the kind of reading an LLM is good at and a parser is not — and unlike `roles`/`themes`, a near-duplicate phrasing across two cards costs nothing, because nothing filters on this field.

### Controlled vocabulary: roles, themes, synergy_tags

`roles`, `themes`, and `synergy_tags` are each a **closed enum**, not free text. Nothing forces ~31,830 independent model calls to describe the same concept with the same string — "Ramp", "Mana Ramp", and "Mana Acceleration" would otherwise all land as distinct values, which silently breaks retrieval filtering and the deck builder's role-based grouping (`#carddocument`'s `filter_fields`, `#cardcache`'s `roles`). The model is shown the exact closed list it must choose from in the prompt itself; it free-generates `mechanics`/`summary`/`strengths`/`weaknesses` but never these three fields.

Example (Cultivate): roles `[Ramp, Mana Fixing]`, themes `[Big Mana, Landfall]`.

#### A member has to be decidable from the card

Closing the vocabulary only buys consistency if every member is a question the model can actually answer from the one card in front of it. Generation is stateless and per-card: the prompt carries that card's facts and nothing about a deck, a curve, or a game plan. A member describing a property the card doesn't individually have can't be assigned correctly, only guessed at — and because the enum is closed, the guess comes back as a confident, valid-looking value that retrieval will filter on.

**`Theme.MIDRANGE` was removed for exactly this reason.** "Midrange" describes a deck's posture over a whole game — a curve that trades early and takes over in the middle. No printed card *is* midrange; a three-mana value creature is equally at home in an aggro, control, or combo shell. An anchor ladder was built for it during anchor selection and came back self-labeled as not honest: every rung would be graded by a property the card doesn't carry, so a rater reading only the card would tag each one by its real function instead. Dropping the member was the fix; anchoring it would have shipped five reference cards teaching the model to answer an unanswerable question.

**`Theme.FLYING` was removed too, but for a different reason** — worth recording because it is a second, subtler way a member fails. Flying is perfectly decidable from a card; the problem was the ladder. The theme is meant to collect cards *paid off* by evasion, and almost every card that reads as a strong flying card is one that *grants* it — so the rungs kept grading a different question than the tag asks, most visibly at the top of the scale, where the payoff side has nearly nothing. A member can be decidable and still be unanchorable. Evasion is better carried as a property of a card than as an archetype of its own.

**`Theme.CONTROL` and `Theme.AGGRO` went the same way as `MIDRANGE`**, after a period of being kept on the argument that a card can *support* those strategies in ways its text shows. It didn't survive contact with the ladders: both are unions of members the vocabulary already has — Control is `COUNTERSPELL` + `BOARD_WIPE` + `REMOVAL`, Aggro is haste + attack-trigger + anthem cards — so every rung either duplicated another ladder's rung or graded a deck property. Both are now computed at the deck level from role counts rather than tagged on a card. `Theme.SACRIFICE` was dropped alongside them for an adjacent reason: `ARISTOCRATS` plus `SynergyTag.SACRIFICE_OUTLET` and `DEATH_TRIGGER` already cover it, and a small model tags all of them every time.

Apply the same test before adding a member: the distinction is whether the card's own text is evidence for the tag, not whether the tag names something real about decks. `schema.py` records each exclusion next to the vocabulary it was excluded from, including which ones are judgment calls open to revisiting.

### game_stage: GameStageProfile

Not a single Early/Mid/Late label — a card can be strong in more than one phase, and a single label forces a false choice for anything that stays live all game (a mana rock is early *and* mid *and* late). `game_stage` is a fixed-shape object with three independent 1–10 scores:

```python
class GameStageProfile(BaseModel):
    early: float  # 1-10
    mid: float    # 1-10
    late: float   # 1-10
```

The three scores are **not** a distribution — they don't sum to anything fixed, and a card can legitimately score high on all three (Sol Ring) or low on all three (a narrow, situational answer). Stored as JSON, validated against this fixed shape rather than an open dict, so generation can't invent extra keys. Example (Cultivate): `{early: 7, mid: 5, late: 2}` — a card that's only good for one thing, and that thing matters most early.

**The phases are defined in turns, in the prompt.** Early is about turns 1–4, mid 5–8, late 9 onward. That was left unstated until 2026-10, and "how much is this card doing in that phase of a Commander game" is not answerable without it — see `lessons-learned.md` for what the scores looked like while it was missing.

#### early is scored on cost to function, not mana value

The first turn a card can realistically do something is **not** its mana value for about a fifth of the corpus, and the gap runs both ways: Convoke can make a five-drop castable on turn three, while a one-mana Equipment with an equip cost does nothing until turn four or later. Two cases invert the intuition and are worth stating outright — `Delve` looks like cost reduction but pays with a graveyard the early game does not have, so it is a cheap *late* card; and `{X}` is counted as zero, so `{X}{B}{B}` reports mana value 3 while being a late-game finisher.

The deterministic half of that is decided in code. `knowledge_pipeline/cost_profile.py` classifies a card from fields `Card` already carries (`keywords`, `oracle_text`, `mana_cost`, `type_line`, `layout`) into one of four directions — cheaper, has-a-cheap-early-mode, later, or structurally not-a-cost — and `render_card_facts` appends one factual line for the 19.7% of Commander-legal cards (6,278 of 31,830) that match:

```
Cost note: mana value understates the cost to function — an Equip cost must be paid before it does anything.
```

That line states a fact about the card and says nothing about how to score it. The rubric owns the scoring rule, and the separation is deliberate: `lessons-learned.md` records a pass-one bullet that backfired the moment it read as emphasis rather than information. The same classifier scopes the benchmark's `castability` metric (`benchmarking-and-testing.md`), which is why it lives at the package root rather than under `metadata_generator/` — that package's `__init__` would drag pydantic and the Anthropic SDK into the reporting layer.

#### game_stage anchors: one set, not one per tag

`game_stage` has its own calibration set, and unlike `power_rating` it is **one global set of 14 reference profiles**, not one ladder per `Role`/`Theme`.

That is a structural difference rather than a saving. `power_rating` needs a ladder per tag because "good" is only meaningful inside a category — a 7 for `Mana Fixing` and a 7 for `Removal` are claims about different axes, and the ladder is what makes them commensurable. `game_stage` asks where on the game's clock a card does its work, and that clock is the same for every card in the format. A per-tag ladder would teach the opposite: that turn three means something different depending on the archetype.

The consequence that matters more than the token count is that a card-independent set rides in pass one's **fixed** system block. There is no `ladders_for` lookup, no three-ladder cap and no selection step, so the mis-selection failure that the two-pass split introduced for `power_rating` (`lessons-learned.md`) cannot occur here at all.

**Profiles, not five-rung ladders per axis.** Each entry is one card with all three scores and the reason its curve bends that way:

```
Llanowar Elves            9 /  4 /  2   Turn-one mana; by turn nine it is a 1/1 that does nothing...
Command Tower             5 /  5 /  5   A land: no mana cost, played on turn one, and exactly as...
Colossus Hammer           1 /  3 /  6   Mana value 1 and Equip {8}: castable on turn one and does...
Torment of Hailfire       1 /  5 / 10   Its {X} makes a mana value of 2 a lie...
```

Three rules the set obeys, all checked by `tests/test_anchors.py`:

- **Each axis spans the scale**, reaching both outer thirds. A set clustered in the middle teaches the clustering it exists to break.
- **The curve shapes are balanced** — five fall, six rise, two are flat, one peaks. This one is load-bearing: a draft was nine rising out of thirteen, which would have demonstrated the monotonic-ramp habit the set exists to correct. The honest profile for most *cost-exception* cards is back-loaded, so balancing meant deliberately adding front-loaded plain cards.
- **Six of the fourteen are cost exceptions**, one per direction above, teaching that rule by example rather than by instruction.

Reusing a card that is already a `power_rating` rung is allowed, and four are. The ladders' no-reuse rule exists so one card cannot carry two different *power ratings*; timing and power are different fields on different scales, so Sol Ring at `power_rating=8` and Sol Ring at 10/8/6 state nothing contradictory. Both sets are excluded from the benchmark's 300-card sample by `sample.anchor_card_names()`, and a test asserts the shipped sample contains neither — adding these profiles put `Argentum Armor` in both at once, which would have scored a model on a card whose answer was printed in its own prompt.

### Anchor cards

`power_rating` and the role/theme assignments are otherwise ungrounded: each generation call is stateless, so nothing keeps two calls using the 1–10 scale the same way or agreeing on the boundary between adjacent roles. Anchors are the fix — hand-picked, hand-labeled cards embedded directly in the generation prompt, so every card, on every model tier, is rated against the same fixed reference points.

**A card is shown its own ladders, not all of them.** Pass two's prompt carries at most three ladders, selected by exact registry lookup from the `roles` and `themes` pass one assigned (`anchors.ladders_for`). The registry is still one shared, fixed set — two cards sharing a tag see byte-identical rungs for it — but a card is not shown the other 63 ladders it has nothing to do with.

#### Which ladder a card is shown

The cap is three and a heavily tagged card matches more than that, so the *order* the tags are offered in decides what survives it. That order is `MetadataPassOne.ladder_tags`: **`primary_role` first, then the remaining roles, then themes.**

Roles precede themes because a role is what the card does, which is the closest thing to the "strongest effect" the rubric asks the model to calibrate against. `primary_role` precedes the other roles because it is the only ranking the model was ever actually asked for. Before it existed the lead was emission order — the model's own output sequence, read as importance, which nothing in the prompt had asked it to honour.

Pass two is also *told* the order means something: its rubric names the first ladder as the primary role's and tells it to calibrate there unless another shown ladder plainly fits better. The field is therefore load-bearing twice — once in code, choosing the three, and once in the prompt, weighting them.

**What it bought, measured.** On 30 hard-bucket cards through `qwen2.5:7b`, the model named a `primary_role` on 30/30 and it differed from its own first-emitted role on 2 (*The Horus Heresy*, *Nicol Bolas, the Deceiver* — both corrections, Mass Pump and Alternate Win Condition promoted over Card Draw and Removal). In both cases the same three ladders were selected and only their order changed; no card in the sample had a ladder cut off that the primary would have saved. On `qwen2.5:14b`, 8/8 named and 0 disagreements. The honest read is that the two local tiers reproduce emission order almost exactly, so this is insurance and an explicit stored field rather than a measured improvement on them — see `lessons-learned.md` for the cost side of the same measurement.

Selecting at all — rather than the ordering within the selection — was not the original design, and the reason it changed is measured. Every call used to carry all 66 ladders in its cached system block: ~14,000 tokens of calibration data against a ~300-token card. Across benchmark session S-001 — 30 hard-bucket cards through each of two local tiers — it flattened `power_rating` to a 7.07 / 7.13 mean with **nothing below 4**, though the ladders themselves define rungs at 1-2. See `lessons-learned.md` for the A/B and for the second, unfixed half of the problem.

**Each `roles` and `themes` value gets a *ladder* of five anchor cards, not one.** One card per band of the 1–10 scale: 1-2, 3-4, 5-6, 7-8, 9-10.

A single labeled example per tag was the original design, and it doesn't work. Telling the model "Cultivate is a 6 for Ramp" pins exactly one point on the scale and says nothing about the rest of it: the model has no reference for what a 3 or a 9 looks like, so everything away from that one point is back to freehand judgment — which is the drift the anchors existed to stop, just displaced to the ends of the range. A labeled point is not a calibrated scale. Five graded points *are* one: they turn an abstract judgment ("how good is this, out of ten?") into a comparison against concrete cards, which is the kind of question a language model answers consistently.

The bands are two points wide rather than one so a ladder is five cards instead of ten. Five is enough to interpolate between and short enough that the three ladders a card is shown stay a small part of its prompt; ten would double them for a precision `power_rating` does not carry anyway.

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

The prompt instructs it not to score `power_rating` freehand. The ladders it is shown are already the ones for this card's own tags, so it reads the card against those five rungs and places it where it falls between them — if it is clearly better than the 5-6 rung and clearly worse than the 9-10 rung, it is a 7 or an 8. Where more than one ladder is shown, it calibrates against the one its strongest effect belongs to. That is a comparison, not a judgment call against an abstract scale, and it is the difference between a rating that means the same thing in call 30,000 as it did in call 1.

The *finding* of a ladder is no longer the model's job — `anchors.ladders_for` does it in code, by exact registry lookup on the tags pass one assigned. That moves a step out of the prompt that the model was doing badly: with 66 ladders in front of it, "find a ladder for a tag this card shares" is a retrieval problem, and the evidence is that it was answered by regression toward the middle of the scale rather than by retrieval. Code does exact lookup reliably; the model is left with the comparison, which is the part it is good at.

Two consequences worth stating. A tag pass one fails to assign is a ladder pass two never sees, and a tag it assigns loosely is a *wrong* ladder pass two is calibrated against — the selection is only ever as good as the tagging, which is the open problem `lessons-learned.md` records. Note that telling pass one about that dependency makes it worse rather than better: a rubric bullet explaining that its tags choose the ladders was tried and measurably inflated tagging (Sol Ring went from one correct theme to seven), so it was removed. And a card whose tags are all unanchored is shown no ladders at all and told so, rather than being shown the whole registry: falling back to all 66 would reintroduce the dilution precisely on the cards with the weakest tagging.

Anchors are drawn from the "easy" bucket of the benchmarking sample (`benchmarking-and-testing.md#ground-truth`), or picked to that bucket's standard: cards whose role, theme, and power level are obvious enough that hand-labeling them isn't itself a judgment call. Five rungs across 66 tags is 330 cards, far more than a 100-card bucket can supply, so the bucket sets the bar rather than the boundary. Changing a rung's assigned rating shifts the scale for every card sharing that role or theme, so anchors are a deliberate, reviewed edit, not a casual one.

**The registry ships populated** — all 66 ladders, 330 distinct cards, in `backend/knowledge_pipeline/metadata_generator/anchors.py`. It shipped empty until a human had reviewed the candidates, because a guessed anchor is indistinguishable from a reviewed one in the generated corpus. How the 330 were settled, and why the shape of that process matters more than its output:

- **A corpus search proposed candidates, four per rung.** Every one was resolved against the `cards` table, so each rung's name, oracle text, and Commander legality are the corpus's own rather than a recollection — a misremembered anchor would be a silent, permanent error in every rating that reads it.
- **Candidates were ranked on ruler-mark properties, not card quality**: whether the tag is visible in the card's own oracle text (the only evidence the model gets), whether the rating sits unambiguously inside its band, how fast the text reads, and how much a rung differs from its neighbours.
- **A human chose every rung in the original 51 ladders.** The ranking left the top two candidates exactly tied in 101 of the 260 rungs it ranked, so it narrowed the field and did not decide it. Treat the ranking as triage.
- **The 2026-10 recalibration to 66 ladders did not work that way, and the registry should be read with that in mind.** The vocabulary revision orphaned 9 ladders and added 24 unanchored members; 31 ladders (155 rungs) were re-proposed four-candidates-deep and the remaining 35 were carried over verbatim. Faced with 155 rungs to review, the maintainer delegated the pick to the ranking and then overrode it on 6 rungs by hand. So 149 rungs were chosen by a triage scorer that is explicitly documented as not a verdict — and on **48 of those 149 the top two candidates were exactly tied**, meaning the winner was settled by the tie-break (shorter oracle text, then alphabetical name) rather than by any signal about the card. Those 48 are the registry's softest rungs. They were read over and judged reasonable, which is not the same as chosen; `lessons-learned.md` records the trade.
- **A verification pass read those rungs back against the corpus, and the two readings did not agree.** Eleven notes misdescribed the card they sit on: a -1/-1 counter engine documented as shrinking one creature at a time when it puts a counter on every other creature, an energy sink written as costing twice what it prints, a Food outlet whose real cost is the artifact itself rather than a Food, three wrong body sizes, and a floor rung whose stated reason for being a floor was the inverse of what the card does. All eleven were rewritten from the corpus's own text. Eight rungs were also found grading a different property than their ladder's axis comment claimed, and were replaced across six ladders — `Role.ANTHEM`, `Role.MASS_PUMP`, `Role.MANA_DORK`, `Role.LAND_RAMP`, `Theme.VEHICLES`, `Theme.MINUS_COUNTERS`. Two of the eight were a single pair of cards sitting in each other's ladder, each forbidden there by that ladder's own boundary sentence. The replacements were chosen against the axis and the band rubric and verified in the corpus; the rungs not named here have been read, which is still not the same as chosen.
- **Two whole-set rules**, enforced by tests rather than by comment: no card carries two different ratings across ladders, and no card anchors two ladders. The first prevents one prompt from teaching that the scale depends on which ladder you read; the second keeps 330 independent reference points, and keeps the same card off both sides of the `Role`/`Theme` pairs that share a word.

Revising one rung or recalibrating all 330 starts at `anchor-recalibration.md`. The rungs are written straight into `anchors.py` and checked by two gates: `verify_anchors` confirms every rung is a real, Commander-legal card in the corpus, and `tests/test_anchors.py` holds the structural and whole-set rules. The candidate pools these ladders were chosen from are deliberately *not* kept — they would go stale against a later corpus, and proposing a fresh set is now the cheap part.

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
