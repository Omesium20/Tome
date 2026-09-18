# Knowledge API

The hosted read service in front of the shared card knowledge base. It is the **only** path from a client to the cloud Postgres — clients hold no database credentials and open no database connections.

Architectural context: `architecture.md#knowledge-plane`. This file is the source of truth for the contract itself.

---

## Why a service instead of a connection string

Handing every install a read-only Postgres URL would have been less code to write. It was rejected for three reasons that all get worse with more users:

- **A distributed credential is a credential you can't rotate.** A read-only role shipped to every machine is in every config file, every screenshot, every support thread. Revoking it breaks every install at once, so in practice it never gets revoked.
- **Direct DB access welds every client to the schema.** Renaming a column, re-tuning the HNSW index, or changing embedding dimension would break installs still running last month's build. A versioned HTTP contract lets the schema move underneath it.
- **The database is the wrong place to enforce limits.** Postgres connection slots are a scarce, badly-behaved rate limiter — a few dozen clients with pooling misconfigured can exhaust a managed instance. An HTTP service rate-limits per IP and keeps one healthy pool.

The service also keeps the **embedding model server-side**, which is a bigger deal than it first looks. Retrieval embeds the query, and if clients did that they would each download and run the sentence transformer — hundreds of megabytes per install, and a hard version coupling, since a query embedded by a different model version than the corpus returns nonsense. Embedding server-side means the client sends *text and filters* and the embedding model is an implementation detail we can change.

---

## Shape

FastAPI in `backend/knowledge_api/`, in front of the cloud Postgres. Stateless, horizontally scalable, read-only — it holds no user data and issues no writes. All responses are cacheable; the card corpus changes on a weekly-ish pipeline cadence, not per request.

**No user accounts and no user data.** Requests are about cards. The service never learns what a user owns or what they built — collection and decks never leave the machine (`architecture.md#data-boundary-two-databases-one-join-key`). Rate limiting is per IP.

---

## Endpoints

All under `/v1`.

### `GET /v1/meta`

Describes the current knowledge snapshot. The client calls this at startup to decide whether its local card cache is stale.

```json
{
  "snapshot_version": "2026-09-14",
  "card_count": 31830,
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_dimension": 384,
  "schema_version": 1,
  "scryfall_updated_at": "2026-09-14T09:12:00Z"
}
```

A changed `snapshot_version` means cached card rows may be outdated — the client refreshes lazily, not by invalidating the whole cache. A `schema_version` the client doesn't know means the client is too old; it should say so plainly rather than mis-parse responses.

### `GET /v1/cards/{oracle_id}`

One card: the full `Card` fields plus its `CardMetadata` if generated. 404 if the oracle ID is unknown.

### `POST /v1/cards/batch`

Resolve many oracle IDs in one round trip — how the client fills its `card_cache` after a restored backup or a collection import. Capped per request (page beyond it); unknown IDs come back in a separate `missing` array rather than failing the call.

```json
{ "oracle_ids": ["dd21cbaa-…", "56ea32e5-…"] }
```

### `POST /v1/cards/resolve`

Card **names** to oracle IDs. This is what makes CSV collection import work: an exported collection has names, and every local row needs an oracle ID (`architecture.md#data-boundary-two-databases-one-join-key`). Handles the messy parts centrally — punctuation and case, `//` split-card names, and ambiguity — returning `matched`, `ambiguous`, and `unmatched` so the UI can ask about the rows that genuinely need a human.

### `GET /v1/cards/search`

Typeahead for build-around card selection. Prefix/substring name search with the same filters as retrieve. Not vector search — a user typing "Hardened" wants `Hardened Scales`, not cards thematically similar to it.

### `POST /v1/retrieve`

The retrieval step of the Deck Generation Pipeline. Vector similarity **and** hard filters in one query — the thing that motivated pgvector (`architecture.md#why-pgvector-and-not-chromadb`).

```json
{
  "query": "counters matter, +1/+1 synergy, proliferate payoffs",
  "seed_oracle_ids": ["dd21cbaa-…"],
  "filters": {
    "format": "commander",
    "color_identity": ["G", "W"],
    "exclude_oracle_ids": ["…"]
  },
  "limit": 150
}
```

- `query` is embedded server-side. `seed_oracle_ids` lets the caller say "similar to these cards" without describing them — the service uses the stored embeddings directly, which is both cheaper and more faithful than round-tripping through prose.
- **`filters` are hard constraints, applied in SQL alongside the ANN search.** `color_identity` is a Commander legality rule, not a preference: a candidate outside the commander's identity is unplayable, so filtering it post-hoc on the client would just shrink the usable candidate list. Same for `format`.
- Response items carry the card fields, its metadata, and a similarity `score`, so the client can populate its cache and build the prompt from one response.

---

## Versioning

The path carries the major version. Within `/v1`, changes are **additive only** — new fields on responses, new optional request fields. Anything else gets `/v2`, with `/v1` kept alive through a deprecation window.

This is the whole point of the service boundary: installed clients are not upgraded on our schedule. A client must tolerate unknown response fields, and must not depend on field order or on the absence of a field.

`schema_version` in `/v1/meta` is the escape hatch for a client that is nonetheless too old to function — it can say so clearly and point at an upgrade instead of producing subtly wrong decks.

---

## Client behavior

Rules for the client side of this contract (`deck_pipeline/retrieval.py` and the card cache):

- **Cache aggressively.** Card data changes on a weekly pipeline cadence. Any card the client has seen goes in `card_cache` and is served locally; the API is for misses and refreshes, not for rendering.
- **Batch, never loop.** Rendering a 2,000-card collection is one `POST /v1/cards/batch` over the cache misses, not 2,000 calls.
- **Degrade, don't crash.** The knowledge plane being unreachable must not break the parts of the app that don't need it. Browsing the collection, editing decks by hand, and everything else backed by the local database keeps working from cache; only deck *generation* requires the service, and it should say that specifically.
- **Retry with backoff, and respect `Retry-After`.** Same discipline the Scryfall importer already applies upstream.

---

## Operating it

Two consumers of the same Postgres, with different credentials:

| | Knowledge Pipeline | Knowledge API |
|---|---|---|
| Runs | Offline, maintainer-triggered | Always, public |
| Credentials | Read-write | **Read-only role** |
| Failure impact | Stale card data until rerun | Clients can't generate decks |

The API's database role is read-only. Nothing it serves requires a write, and the blast radius of a bug in a public service should not include the corpus.

Deployment expectations: the managed Postgres needs the `pgvector` extension enabled (Neon, Supabase, and RDS all support it); the API needs the embedding model available in its image, since it embeds queries at request time.

### Running your own

Nothing about this is exclusive — a user who doesn't want to depend on the hosted service can run the whole knowledge plane themselves: their own Postgres with `pgvector`, the Knowledge Pipeline to populate it, the Knowledge API in front, and `KNOWLEDGE_API_URL` pointed at it. That path costs the full metadata-generation bill, which is exactly what the hosted service exists to spare people. See `self-hosting.md#running-your-own-knowledge-plane`.
