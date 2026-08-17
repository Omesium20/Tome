# Knowledge Pipeline

Detailed technical reference for the Knowledge Pipeline introduced in `docs/architecture.md#knowledge-pipeline`. That document is the source of truth for the pipeline's shape and stage order; this file exists to hold the implementation-level detail for each stage as it's built, so `architecture.md` doesn't accumulate detail it wasn't meant to hold.

Pipeline (offline, run from `backend/`):

```
Scryfall → Card Import → Claude Metadata Generation → Normalized Database
  → Knowledge Document Generation → Hugging Face Embeddings → ChromaDB
```

| Stage | Module | Status |
|---|---|---|
| Card Import | `knowledge_pipeline/scryfall_importer.py` | stub (`NotImplementedError`) |
| Claude Metadata Generation | `knowledge_pipeline/metadata_generator.py` | stub |
| Knowledge Document Generation | `knowledge_pipeline/document_generator.py` | stub |
| Embeddings | `knowledge_pipeline/embeddings.py` | stub |

The output of Card Import is the `Card` entity described in `docs/data-model.md#card` — imported directly from Scryfall, never AI-generated. The remaining stages are undocumented until implemented.

---

## Scryfall Importer

Reference notes for `knowledge_pipeline/scryfall_importer.py`, gathered from the official Scryfall API docs (via Context7, `/websites/scryfall_api`) ahead of implementation.

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
| id | `id` | Scryfall's own UUID for this printing |
| oracle_id | `oracle_id` | stable across reprints/editions — the identity we key strategic metadata to |
| name | `name` | for multi-faced cards, the two face names joined with `" // "` |
| mana_cost | `mana_cost` | may be absent at card root for some multi-faced layouts — see below |
| mana_value | `cmc` | Scryfall's field is literally named `cmc` |
| oracle_text | `oracle_text` | may be absent at card root for multi-faced cards — see below |
| colors | `colors` | may be absent at card root for multi-faced cards — see below |
| color_identity | `color_identity` | always present at card root, even for multi-faced cards — union of both faces. This is the field Commander legality checks depend on |
| type_line | `type_line` | for multi-faced cards this is both faces joined with `" // "` |
| keywords | `keywords` | array of strings, e.g. `["Lifelink"]` |
| image_url | `image_uris.normal` | absent at card root for multi-faced cards — see below |

Source: https://scryfall.com/docs/api/cards, https://scryfall.com/docs/api/cards/search

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
- `mana_cost`, `oracle_text`, `colors`, `type_line`, and `image_uris` may be **missing or incomplete at the card root** on multi-faced cards — check `card_faces` first when `layout != "normal"` and fall back to the root object otherwise.
- Since our `Card` model is single-valued (one `oracle_text`, one `image_url`, etc.), the importer needs an explicit merge rule for multi-faced cards — e.g. concatenate face oracle texts, or store the front face only. Not yet decided; flag this as an open question for `document_generator.py` too, since knowledge documents are generated from these same fields.

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

MVP scope is Commander-only (`CLAUDE.md`), so the importer only needs `legalities.commander`. Not currently a field on `Card` in `docs/data-model.md` — worth a decision on whether the importer filters out `commander: "not_legal"` cards at import time (smaller, cleaner pool) or imports everything and defers filtering to deck validation. `docs/data-model.md` will need a `legalities` (or `commander_legal`) field added to `Card` once that's decided — flagging here rather than guessing.

Source: https://scryfall.com/docs/api/cards/search

### Rate limits

Only relevant if the importer ever falls back to per-card endpoints (e.g. `/cards/named` for a manual lookup/backfill) rather than the bulk file:

- Most endpoints: 10 requests/second.
- `/cards/search`, `/cards/named`, `/cards/random`, `/cards/collection`: 2 requests/second.
- Exceeding a limit returns `429` and a 30-second restriction; repeated overloading risks a ban. Back off on `429` rather than retrying immediately.

Source: https://scryfall.com/docs/api/rate-limits
