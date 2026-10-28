# Knowledge Pipeline

Detailed technical reference for the Knowledge Pipeline introduced in `docs/architecture.md#knowledge-pipeline`. That document is the source of truth for the pipeline's shape and stage order; this file exists to hold the implementation-level detail for each stage as it's built, so `architecture.md` doesn't accumulate detail it wasn't meant to hold.

**This pipeline is operated by maintainers, not by users.** It runs offline against the shared cloud Postgres, and its output is the same for everyone — every client reads the result through the Knowledge API (`knowledge-api.md`) rather than building it locally. That is the point: metadata generation is two model calls per card across ~33,000 cards, and this way every user's first deck is free of that cost. (See `lessons-learned.md#knowledge-base-from-per-user-generation-to-a-hosted-knowledge-plane` for why this pipeline isn't run per-install.)

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
| Metadata Generation           | `knowledge_pipeline/metadata_generator/`   | `card_metadata`            | **implemented, single-tier, two calls per card** |
| Knowledge Document Generation | `knowledge_pipeline/document_generator.py` | `card_documents.document`  | stub            |
| Embeddings                    | `knowledge_pipeline/embeddings.py`         | `card_documents.embedding` | stub            |

The output of Card Import is the `Card` entity described in `docs/data-model.md#card` — imported directly from Scryfall, never AI-generated. The remaining stub stages are undocumented until implemented.

### Metadata Generation

Generates `CardMetadata` (`docs/data-model.md#cardmetadata`) for every `Card` that lacks it, or whose metadata predates the `Card` row it describes (an oracle text errata, say). Run:

```bash
python -m knowledge_pipeline.metadata_generator                    # every commander-legal card needing it
python -m knowledge_pipeline.metadata_generator --dry-run --limit 20  # smoke-test prompts; still spends real API calls
python -m knowledge_pipeline.metadata_generator --format all       # ignore legality entirely
```

#### Two calls per card

Each card takes two model calls, split on `CardMetadataBlueprint`'s existing field order:

| | Produces | System block | User message |
|---|---|---|---|
| Pass one | `MetadataPassOne` — `mechanics` → `game_stage`, including `primary_role` | `build_pass_one_system_prompt()`: rubric + closed taxonomy, **no anchors** (~4,200 chars) | the card (`render_card_facts`) |
| Pass two | `MetadataPassTwo` — `power_rating`, `summary` | `build_pass_two_system_prompt()`: how to read a ladder (~1,600 chars) | the card, pass one's answers, and **only the ladders for the roles/themes pass one assigned** (`render_rating_request`) |

`CardMetadataBlueprint.from_passes` merges the two, and a card is written only when both succeed — `sink.upsert_batches` refreshes every column, so a pass-one-only row would be real tags with no rating, which the staleness check would then treat as done.

**Why.** The single call it replaced rendered all 66 ladders into every system block: ~14,000 tokens of calibration against a ~300-token card, of which 1–4 ladders were ever relevant. Across benchmark session S-001 (30 hard cards × two local tiers) it flattened `power_rating` to a 7.07 / 7.13 mean with nothing below 4. The cut falls where it does because everything needed to *choose* a ladder — `roles` and `themes` — is decided by the end of pass one. `lessons-learned.md` has the A/B and the part of the problem this did **not** fix.

Three things to know before changing it:

- **The per-card ladders go in pass two's *user* message, not its system block.** Both system blocks stay fixed strings, built once per run. A system block that varied per card would miss Anthropic's prompt cache on every call and break llama.cpp's KV-prefix reuse, which is what collapses local prefill from ~35 s to ~0.2 s (`benchmarking-and-testing.md`).
- **At most three ladders, ordered `primary_role` → other roles → themes** (`prompt.MAX_LADDERS_PER_CARD`, `schema.MetadataPassOne.ladder_tags`, `anchors.ladders_for`). A card with four roles and three themes would otherwise be shown ~6,000 tokens of ladders — most of the way back to the problem. Because the cap bites, the order is what decides which three a card is calibrated against; `ladder_tags` owns that order and `ladders_for` only preserves it. A card matching none is shown none and told so, rather than falling back to all 66. See `data-model.md#which-ladder-a-card-is-shown` for why the primary role leads and what measuring it found.
- **`build_system_prompt()` is frozen, not dead.** It still renders the old single-call prompt, byte-identical, because `benchmark/runner.py` records and fingerprints it and it is how the pre-split baseline is reproduced. Nothing in `pipeline.py` calls it. Delete it when the harness generates in two passes too.

**Single-tier today, not the tiered design end state.** `benchmarking-and-testing.md` specifies a 300-card benchmark that decides *whether* a local model is viable at all and, if so, where the local/frontier escalation line falls — that benchmark's harness is now built but its full run, and the hand-labeling it would be scored against, are not. Until they are, every card routes through one backend: the frontier Anthropic model, called through `AnthropicMetadataBackend` (`knowledge_pipeline/metadata_generator/model_backend.py`). `MetadataModelBackend` is a small protocol, deliberately **not** the client-facing `ModelProvider` (`model-providers.md`) — see `benchmarking-and-testing.md#models-under-test` for why those are different interfaces.

A second implementation of that protocol now exists — `OllamaMetadataBackend` (`ollama_backend.py`), the local tier the benchmark measures — and the seam held: adding it changed nothing in `pipeline.py`. **Nothing routes to it.** `generate_metadata`'s default backend is still Anthropic, and only `benchmark/runner.py` constructs the Ollama one. Two notes if you touch it:

- It has **no settings and no env var.** Every knob is a constructor argument, and the benchmark CLI passes flags over module-constant defaults. A `KnowledgeSettings` field would configure something the pipeline does not use; that field arrives with the change that actually routes production locally.
- `AnthropicMetadataBackend.__init__` raising on an empty `METADATA_MODEL_API_KEY` must stay **inside** that backend. Hoisting the check to `pipeline.py` or the runner would require a key to use a local model that needs none — which is precisely the `MODEL_API_KEY` bug `lessons-learned.md` records, re-made on the knowledge side.

The protocol also gained a sibling, `ObservableMetadataBackend`, which adds `generate_observed` returning a `GenerationAttempt` — raw text, token counts, latency, and crucially a result rather than an exception when output fails to validate. `pipeline.py` neither needs nor uses it; the benchmark does, because `generate` raises the unparseable response away and "how often is output valid at all" is one of the metrics. `MetadataModelBackend` was left untouched.

#### Five themes the model is never asked about

Five `Theme` members — Treasure, Food, Clues, Energy, and Infect — are decidable straight off a `Card`'s own `oracle_text`/`keywords`: the three token themes and Energy from a regex on "create a `___` token" / the `{E}` symbol, Infect from a `keywords` lookup. `knowledge_pipeline/derived_tags.py` computes them; `schema.DERIVED_THEMES` names them.

They are still real `Theme` members — the column, the anchor ladder, and `anchors.ladders_for` all treat them exactly like any other theme. What changes is who asks: `prompt.build_pass_one_system_prompt` drops them from pass one's "Valid `themes` values" listing (and tells the model so, in the rubric), and `pipeline.py` merges `derive_themes`'s output into the card's themes right after pass one returns, via `MetadataPassOne.with_derived_themes` — before `ladder_tags` picks pass two's ladders and before the row is written. A card that creates a Treasure is still rated against the Treasure ladder; the model was simply never asked to notice the Treasure.

The point isn't the token savings. A closed vocabulary only helps if every live member is actually a judgment call — a tag the model never has to weigh is a tag it cannot mis-apply, and removing five mechanical lookups from the menu leaves more of the model's attention on the ones that genuinely require reading the whole card (Aristocrats, Voltron, and the like). `build_system_prompt()`'s frozen one-pass baseline is untouched by this — it still renders and asks about all 66 — because that prompt exists to stay byte-identical for the benchmark, not to reflect current practice.

Module layout (`knowledge_pipeline/metadata_generator/`, mirroring `scryfall_importer/`'s package shape):

| Module | Role |
|---|---|
| `__main__.py` | CLI: arg parsing, `python -m knowledge_pipeline.metadata_generator` |
| `pipeline.py` | orchestration, the needs-metadata query, batched writes |
| `prompt.py` | builds both passes' system blocks and both user messages — plus `build_system_prompt()`, the frozen one-pass baseline the benchmark still measures |
| `anchors.py` | the anchor-card registry (`data-model.md#anchor-cards`) — **populated: one five-rung ladder for each of the 66 `Role`/`Theme` members, 330 distinct cards**. `ladders_for` selects a card's own ladders by exact registry lookup and `render_ladders` renders that subset into pass two's user message. `generate_metadata` logs a warning rather than refusing to run if a tag is unanchored, which is what keeps the pipeline testable with a trimmed registry |
| `model_backend.py` | the `MetadataModelBackend` protocol, its observable sibling `ObservableMetadataBackend` + `GenerationAttempt`, and `AnthropicMetadataBackend` |
| `ollama_backend.py` | `OllamaMetadataBackend` — the local tier, for the benchmark only; nothing in the pipeline routes to it. Owns the `num_ctx` preflight that stops a truncated system prompt (`benchmarking-and-testing.md`) |
| `schema.py` | `MetadataPassOne` / `MetadataPassTwo` / `CardMetadataBlueprint` and the closed `Role`/`Theme`/`SynergyTag` vocabulary — the shapes generation must produce, **in the order it must produce them**: `mechanics` first, so the model restates the card before it judges it. The blueprint inherits pass one's seven fields rather than restating them, which is what keeps the finished order fixed |
| `sink.py` | batched `INSERT ... ON CONFLICT DO UPDATE` into `card_metadata`, mirroring `scryfall_importer/sink.py` |

**`mechanics` is generated first, and that is a property of the schema, not the prompt.** `MetadataPassOne` declares `mechanics` — a literal restatement of the card's rules text — ahead of every judgment field, because both backends decode against that schema and emit properties in declaration order: the Anthropic SDK builds the schema from the class, Ollama compiles it into a decoding grammar. So pass one writes what the card *says* before it decides what the card *is*, and pass two cannot revise either. Reordering the class silently removes that step while leaving the field in place, which is why `tests/test_metadata_schema.py` asserts the full nine-field order and `prompt.py`'s pass-one rubric opens with the matching instruction. Full rationale: `data-model.md#mechanics-the-model-reads-before-it-judges`.

**The closed-vocabulary lists are bounded (`max_length`) and deduplicated, and both guards are about decoding rather than modelling.** A `list[enum]` with no ceiling becomes a grammar with no reason to ever close the array: on a real card, with the anchor block gone from the prompt, the 7B emitted `"Big Mana", "Lands Matter", "Big Mana", …` until it hit `num_predict` and the whole card failed to parse. `maxItems` makes the grammar forbid that; the dedupe validator cleans up what a shorter loop leaves behind, keeping first-seen order because `ladder_tags` reads it.

`roles` and `themes` are capped at **5** (`schema.MAX_TAGS_PER_CARD`), down from the 8 they were first set at. Eight was picked to bound garbage without ever truncating a real answer, and it was too loose to do the first job: the 14B emitted a full eight roles for one modal Equipment and never hit the ceiling. Tightening it to 5 did more than clip the tail — on the same card the 14B's role count fell to 3, because the ceiling is a grammar constraint the model decodes under rather than a filter applied afterwards. `strengths`/`weaknesses` keep 8 and `synergy_tags` keeps 12; the rubric asks the model to be exhaustive about hooks and selective about judgments, and the ceilings follow that. See `lessons-learned.md`.

Two decisions worth knowing about:

- **Defaults to `commander`-legal cards, not the whole corpus.** `--format` accepts any Scryfall format name or `all`. Tome is Commander-only (`PRD.md`), so a card legal nowhere Tome plays is, by default, a model call spent on nothing — beyond the 2,079 never-legal-anywhere cards this rules out for free, it also skips everything legal only in some other format. Pass `--format all` to generate over the full imported pool anyway.
- **Reads and writes use separate query shapes on purpose.** The needs-metadata query pages with discrete `oracle_id > cursor LIMIT n` queries rather than one `yield_per` streaming cursor, because the same session commits a write batch between pages — on Postgres, that commit would close a server-side cursor still in use. See the docstring on `_cards_needing_metadata` for the full reasoning.

Configuration (`docs/self-hosting.md#knowledge-plane-settings`): `METADATA_MODEL_API_KEY` (required — a maintainer credential, deliberately separate from the client's `MODEL_API_KEY`), `METADATA_MODEL_NAME`, `METADATA_MODEL_MAX_TOKENS`, `METADATA_BATCH_SIZE`.

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

**There are no format flags.** Passing `--format` or `--prune` exits 2 with "unrecognized arguments" rather than silently doing nothing. The import takes the whole pool every time, so it is safe to run unattended: nothing to select, nothing to prompt for.

### The import is not format-scoped, and cannot be made so

One rule decides what gets imported: **is this object a card?** If so it lands, with its complete `legalities` map, whatever that map says. Legality is never consulted, and there is no flag to change that. (This wasn't always true — see `lessons-learned.md#scryfall-importer-format-scoped--whole-pool`.)

Breadth is paid once, centrally, by us, and three arguments support keeping the import unscoped:

- **Filtering costs more than it saves.** Commander-legal cards are 91% of the card objects in the file. The expensive stages are `metadata_generator/` (two model calls per card) and `embeddings.py`; that is where a format filter belongs, and applying it there needs no re-import.
- **A collection is not a legal deck.** CSV import resolves *owned* card names through the Knowledge API. 1,945 paper-printed cards are legal in no format at all, 1,244 of them Un-set cards. Excluding them would turn each into a permanent placeholder in somebody's collection, in an app that is explicitly proxy-friendly.
- **Format belongs to the client.** Deck building filters on `Card.legalities`, which every row carries whole, so a future Brawl or Oathbreaker mode is a client-side predicate rather than a re-import every user waits on.

One nuance the client-side filter will need: a **restricted** card is legal in Vintage, limited to one copy. A naive `legalities[fmt] == "legal"` check wrongly discards Black Lotus.

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

Commander is 91% of what's imported, which is exactly why scoping the *import* to it wasn't worth the cost. Where pool size does bite is `metadata_generator/` (two model calls per card) and `embeddings.py`, both proportional to whatever pool is selected *at that stage* — and those 2,079 never-legal cards are the obvious first thing to exclude there.

### Re-running is safe

- **Upsert, never replace.** Writes are `INSERT ... ON CONFLICT (oracle_id) DO UPDATE` in batches of `IMPORT_BATCH_SIZE`, so a weekly refresh updates rows in place and cannot disturb a collection or a deck. Duplicate `oracle_id`s within a batch (reversible cards, some promos) are collapsed first, because Postgres rejects an `ON CONFLICT` statement that touches the same key twice.
- **An import cannot delete a row.** There is no `--prune`. A card Scryfall drops upstream lingers as an unreferenced row, costing bytes; deleting it would break whoever owns that card, since collections and decks live on users' machines (`data-model.md#two-databases-one-join-key`) where the importer can't see what a deletion would orphan.

- **`--reset` is the one destructive path, and its confirmation guards the wrong-database case.** It prints the target URL and the number of cards it would delete, then requires that database's own name typed back. There is no `--force`, and a guard an unattended process can waive is not a guard. A non-interactive `--reset` always refuses.

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

Labelling matters: an unlabelled concatenation of two rules boxes reads as one card with contradictory text. `mana_cost` and `type_line` use a plain `" // "` join, so the separator itself distinguishes which merge produced which field. This rule applies to `document_generator.py` as well.

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
