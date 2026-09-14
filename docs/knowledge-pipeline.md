# Knowledge Pipeline

Detailed technical reference for the Knowledge Pipeline introduced in `docs/architecture.md#knowledge-pipeline`. That document is the source of truth for the pipeline's shape and stage order; this file exists to hold the implementation-level detail for each stage as it's built, so `architecture.md` doesn't accumulate detail it wasn't meant to hold.

Pipeline (offline, run from `backend/`):

```
Scryfall → Card Import → Claude Metadata Generation → Normalized Database
  → Knowledge Document Generation → Hugging Face Embeddings → ChromaDB
```

| Stage | Module | Status |
|---|---|---|
| Card Import | `knowledge_pipeline/scryfall_importer/` | **implemented** |
| Claude Metadata Generation | `knowledge_pipeline/metadata_generator.py` | stub |
| Knowledge Document Generation | `knowledge_pipeline/document_generator.py` | stub |
| Embeddings | `knowledge_pipeline/embeddings.py` | stub |

The output of Card Import is the `Card` entity described in `docs/data-model.md#card` — imported directly from Scryfall, never AI-generated. The remaining stages are undocumented until implemented.

---

## Scryfall Importer

Implemented in `backend/knowledge_pipeline/scryfall_importer/`. Reference notes below are from the official Scryfall API docs (via Context7, `/websites/scryfall_api`); numbers are from the 2026-08-17 `oracle_cards` snapshot.

### Module layout

Split by responsibility, with every stage a generator so the corpus is never held in memory:

```
catalog -> download -> stream -> filter -> map -> upsert -> [prune]
```

| Module | Responsibility |
|---|---|
| `bulk.py` | Catalog lookup, download to cache, streamed gzip/JSONL decode |
| `formats.py` | `FormatProfile` dataclass and the `PROFILES` registry — every format is live data, none scaffolded |
| `mapping.py` | Scryfall JSON → `CardRow`, including the face-merge rules |
| `sink.py` | Batched upsert, reference-safe prune, guarded reset |
| `pipeline.py` | `import_cards()` — wires the stages, writes an `ImportRun` |
| `__main__.py` | CLI and the (currently dormant) interactive format picker |

### Commands

```
python -m knowledge_pipeline.scryfall_importer                            # interactive format picker
python -m knowledge_pipeline.scryfall_importer --format commander
python -m knowledge_pipeline.scryfall_importer --format all --if-newer    # weekly refresh
python -m knowledge_pipeline.scryfall_importer --dry-run --limit 500      # no writes
python -m knowledge_pipeline.scryfall_importer --format standard --prune  # reclaim space
python -m knowledge_pipeline.scryfall_importer --format all --reset       # start over
```

`--format` defaults to `all` (every registered profile is enabled and importable — see below) and is chosen interactively when omitted on a real terminal. A non-interactive caller (Docker, CI, cron) must pass `--format` explicitly, since there's no terminal to prompt on and blocking on stdin would hang the process forever.

### The import is deliberately not format-scoped

Tome is a Commander deck builder, but the importer does not filter to Commander (or any format) by default. Commander-legal cards are 96.5% of the entire card pool (31,830 of 32,988), so scoping the *import* to Commander would save almost nothing while making every other format permanently unavailable without a full re-import. Instead, the full corpus is imported with its complete `legalities` map intact, and format becomes a filter applied later, at the stages where pool size actually costs something — `metadata_generator.py` (one Claude call per card) and `embeddings.py`. `--format` on the import exists mainly for constrained hosts that want a smaller table.

### Formats are data, not code

A format is a `FormatProfile` entry in `formats.PROFILES`, because nearly every format reduces to "`legalities[key]` is one of these values". Adding a new format is a dict entry, not a class. Every profile currently registered — `all`, `commander`, `vintage`, `legacy`, `oathbreaker`, `modern`, `duel`, `pioneer`, `pauper`, `paupercommander`, `brawl`, `standard` — is fully live; none are scaffolded or gated behind a flag.

| Piece | Stays format-generic |
|---|---|
| `PROFILES` / `resolve()` | resolves any registered profile by name, case-insensitively |
| CLI picker | lists every profile in `PROFILES`, largest pool first |
| `ImportRun.format_profile` | records which pool each run wrote |
| `Card.legalities` | the whole map is stored, so any format is answerable after the fact |

Nothing outside `formats.py` hardcodes a format name. Adding a new one is a `PROFILES` entry — not a pipeline change.

Per-format variance is expressible as a field: `accepted` widens the set of legality values that count as playable, which is what the `vintage` entry uses — a **restricted** card is legal there (limited to one copy), and a plain `== "legal"` check would wrongly discard Black Lotus.

`NON_CARD_LAYOUTS` is excluded from every profile: tokens, emblems, art series, vanguards, schemes, planes, augments, hosts, and `front_card` (Jumpstart-style product dividers — `type_line` is literally `"Card"`, `set_type` is `memorabilia`). That removes 3,770 of the snapshot's 38,626 objects. Two layouts that look like artifacts but are **not** excluded: `prepare` cards are genuine split-style spells, and 352 `normal` cards are vanilla creatures with legitimately empty oracle text.

### Pool sizes

Measured against Scryfall's search API, for context on what the profiles cost:

| Pool | Unique cards |
|---|---|
| every real card | 32,988 |
| `legal:commander` | 31,830 |
| `legal:modern` | 22,450 |
| `legal:pauper` | 10,793 |
| `legal:pioneer` | 13,000 |
| `legal:standard` | 4,887 |

Commander is 96.5% of the entire card pool, so scoping the *import* to it saves almost nothing. Where the pool size does bite is `metadata_generator.py` (one Claude call per card) and `embeddings.py`, which are proportional to whatever pool is selected at that stage.

### Re-running is safe

- **Upsert, never replace.** Writes are `INSERT ... ON CONFLICT (oracle_id) DO UPDATE` in batches of `IMPORT_BATCH_SIZE`, so a weekly refresh updates rows in place and cannot disturb a collection or a deck. Duplicate `oracle_id`s within a batch (reversible cards, some promos) are collapsed first, because Postgres rejects an `ON CONFLICT` statement that touches the same key twice.
- **`--prune` is reference-safe by construction.** It deletes cards outside the current import *and* not referenced by `collection`, `deck_cards`, `decks.commander_id`, or `card_metadata`. So it clears out cards banned since the last refresh (and anything left over from a wider earlier import) while keeping a banned card the user owns.
- **`--reset` refuses** while `collection`/`decks`/`deck_cards` hold rows, unless `--force` *and* a typed `delete` confirmation on a real terminal. Card data is re-downloadable; a collection is not.
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

**Decision: the whole map is stored, as a single `legalities` JSON column on `Card`.** Not a boolean per format — a new format appearing upstream then needs no migration — and not Commander-only, because the format model above depends on being able to ask about any format after the fact. Postgres queries it directly:

```sql
select count(*) from cards where legalities->>'commander' = 'legal';
```

Note `"restricted"` is a legality *value*, not a separate state to ignore: a restricted card is legal in its format, limited to one copy. `FormatProfile.accepted` is what encodes that.

Source: https://scryfall.com/docs/api/cards/search

### Rate limits

Only relevant if the importer ever falls back to per-card endpoints (e.g. `/cards/named` for a manual lookup/backfill) rather than the bulk file:

- Most endpoints: 10 requests/second.
- `/cards/search`, `/cards/named`, `/cards/random`, `/cards/collection`: 2 requests/second.
- Exceeding a limit returns `429` and a 30-second restriction; repeated overloading risks a ban. Back off on `429` rather than retrying immediately.

Source: https://scryfall.com/docs/api/rate-limits
