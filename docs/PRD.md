# Product Requirements Document (PRD)

## Version

MVP v1.0

---

# 1. Product Overview

## Objective

Build an AI-powered Magic: The Gathering Commander deck-building assistant that helps players create optimized Commander decks from cards they already own or selected cards they want to build around.

The application solves the problem of overwhelming deck construction by allowing users to:

1. Select one or more cards they want to build around.
2. Use AI to understand the strategy behind those cards.
3. Retrieve synergistic cards from a curated card database.
4. Generate a complete Commander deck.
5. Recommend missing cards that improve the deck, regardless of whether the user owns them.

The MVP should prove that AI can provide meaningful Commander deck-building assistance by acting as an experienced Magic player.

---

# 2. MVP Scope

## Included

- Commander format only.
- Standard Commander deck size:
    - 100 cards total.
    - 1 commander.
    - Singleton rule.
    - Color identity validation.
- Card import from Scryfall.
- User card collection tracking.
- Card selection interface.
- AI-powered deck generation.
- Card recommendations.
- Proxy-friendly recommendations.
- Deck validation.
- Deck explanation.
- A shared, hosted card knowledge base, built once and served to every user.
- Bring-your-own-model deck generation: local model or frontier API, user's choice.
- Local-only storage of the user's collection and decks.

---

## Not Included

The MVP will not support:

- Marketplace integration.
- Card purchasing.
- Card pricing.
- Competitive tournament optimization.
- Other Magic formats.
- Social features.
- Trading.
- Deck sharing.
- Full collection scanning integrations.

These can be future additions.

---

# 3. Core User Problem

Magic: The Gathering Commander has thousands of possible cards, making deck creation difficult.

Players commonly struggle with:

- Finding cards that synergize with their ideas.
- Understanding what strategy a group of cards supports.
- Knowing what cards are missing.
- Balancing:
    - Ramp
    - Removal
    - Card draw
    - Lands
    - Win conditions

The application should reduce this complexity by providing an AI deck-building assistant.

---

# 4. User Flow

## Step 1 — Import Collection

User imports their collection.

Source:

- ManaBox export (future)
- CSV upload (MVP)

The collection is stored locally, in a database on the user's own machine, and is never transmitted anywhere.

CSV rows arrive as card *names*; the client resolves them to stable oracle IDs through the Knowledge API, surfacing ambiguous or unmatched names to the user rather than guessing.

---

## Step 2 — Select Build Around Cards

User selects one or more cards.

Example:

```
Hardened Scales
The Ozolith
```

---

## Step 3 — AI Configuration Questions

The AI asks limited questions to understand user preference.

Example:

### Power Level

Options:

- Casual
- Focused
- High Power
- Competitive

### Collection Preference

Options:

- Build strongest possible deck
- Prefer cards I own
- Only use my collection

---

## Step 4 — Generate Deck

The system:

1. Retrieves relevant cards.
2. Determines strategy.
3. Builds Commander deck.
4. Validates legality.
5. Returns final deck.

---

# 5. System Architecture

The system consists of two independent pipelines, split across **two planes with different operators**.

## Pipeline 1

Knowledge Pipeline

Purpose:

Prepare Magic card knowledge.

Runs offline, **centrally, operated by maintainers**.

Output is one shared card knowledge base, identical for every user.

---

## Pipeline 2

Deck Generation Pipeline

Purpose:

Build decks using retrieved knowledge.

Runs **on the user's own machine**, when they request a deck.

Uses a model the user chooses — local or frontier.

---

# Deployment Model

| | Knowledge plane | Client plane |
|---|---|---|
| Operated by | Maintainers (one hosted deployment) | Each user, on their machine |
| Holds | Every card, its AI analysis, its embedding | One user's collection and decks |
| Storage | Cloud Postgres + `pgvector` | Local SQLite or Postgres |
| Written by | The Knowledge Pipeline | The user |

Rationale:

- **Build the knowledge base once.** Card analysis is one model call per card across ~33,000 cards. Making every user run that was the largest barrier to first use. Done centrally, the cost is paid once, prompt fixes land in one place, and every user gets identical retrieval quality.
- **Generate decks on the client.** Generation is per-user and bursty. Client-side means we host no inference, hold no keys on users' behalf, and see no collections. It is also what makes local models possible.
- **User data never leaves the machine.** The client asks the Knowledge API about *cards*, never about *the user*.

---

# High-Level Architecture

```
  -- KNOWLEDGE PLANE (hosted, shared, read-only to clients) --

  Scryfall --> Knowledge Pipeline --> Cloud Postgres     --> Knowledge API
               (offline, maintainer)  + pgvector             (thin read service)
                                                                    |
 ===================================================================|=====
                                                                    | HTTPS
  -- CLIENT PLANE (the user's machine) --                           v

  Vite + React --> Local FastAPI --> Deck Generation Pipeline --> retrieval
                        |                      |
                        |                      v
                        |            Model Provider interface
                        |            |- frontier (Anthropic, OpenAI-compatible)
                        |            |- local    (Ollama, LM Studio, vLLM)
                        v
                 Local user database
                 collection . decks . deck_cards . card_cache
```

Technical source of truth: `architecture.md`. The service contract: `knowledge-api.md`. The model interface: `model-providers.md`.

---

# 6. Technology Stack

## Frontend

- Vite
- React
- React Router
- TypeScript

Responsibilities:

- User interface.
- Collection management.
- Card selection.
- Deck display.

---

## Local Backend (client plane)

- Python
- FastAPI

Responsibilities:

- API endpoints.
- Deck pipeline orchestration.
- Deck validation.
- Collection management.

---

## Knowledge API (knowledge plane)

- Python
- FastAPI, read-only

Responsibilities:

- Card lookup and name resolution.
- Candidate retrieval (vector similarity + hard filters).
- Being the only client-facing entry to the corpus — clients hold no database credentials.

---

## Large Language Model

User's choice, behind a `ModelProvider` interface:

- Anthropic Claude API (frontier).
- Any OpenAI-compatible endpoint (OpenAI, OpenRouter, vLLM, LM Studio, gateways).
- Ollama (local).

Responsibilities:

- Card analysis (knowledge plane, frontier model, run centrally).
- Deck strategy, commander selection, deck construction, explanations (client plane, user's model).

Swapping providers is a configuration change, not a code change. Deck *correctness* does not depend on which model is used — validation is deterministic local code — only deck *quality* does.

**LangChain is not used.** With retrieval behind an HTTP contract and generation behind our own provider interface, it sat between two abstractions the project already owns.

---

## Embedding Model

Hugging Face Sentence Transformer model. Runs **server-side**, in the knowledge plane.

Responsibilities:

- Convert card knowledge documents into vectors.
- Embed incoming retrieval queries, so clients never download or run the model — and so a corpus and its queries can never drift to different model versions.

---

## Vector Storage

`pgvector`, in the same cloud Postgres as the cards.

Responsibilities:

- Store embeddings alongside the cards and metadata they describe.
- Perform similarity search **and** hard filtering (color identity, format legality) in a single query.

Chosen over a separate ChromaDB instance because centralizing the knowledge base made a second hosted service redundant, and because every real retrieval needs both the vector search and the filters at once.

---

## Card Data Source

Scryfall Bulk Data API

Responsibilities:

Provide:

- Card information.
- Oracle text.
- Mana cost.
- Color identity.
- Types.
- Legalities.

---

# 7. Knowledge Pipeline

## Purpose

Transform raw Magic card data into AI-readable knowledge.

The pipeline runs offline, centrally, operated by maintainers. Its output is written to the shared cloud Postgres and served to every client through the Knowledge API.

---

# Pipeline Flow

```
Scryfall

↓

Card Import

↓

Model Metadata Generation

↓

Knowledge Document Generation

↓

Hugging Face Embeddings

↓

Cloud Postgres + pgvector
```

Metadata generation is one model call per card across the whole corpus — the expensive stage that running this centrally exists to spare every user.

---

# 8. Normalized Knowledge Model

The application uses a normalized data model.

---

# Card

Source of truth.

Imported from Scryfall.

Example:

```
Card

id

oracle_id

name

mana_cost

mana_value

oracle_text

colors

color_identity

type_line

keywords

image_url
```

---

# Card Metadata

AI-generated strategic information.

Example:

```
CardMetadata

card_id

summary

roles

themes

game_stage

power_rating

strengths

weaknesses

synergy_tags
```

Example:

Cultivate:

```
Roles:
- Ramp
- Mana Fixing

Themes:
- Big Mana
- Landfall

Game Stage:
Early
```

---

# Collection

Tracks owned cards. **Local to the user's machine** — `card_id` is a logical reference to a card in the shared knowledge database, which no foreign key can enforce across the plane boundary.

```
Collection

user_id

card_id

quantity
```

---

# Deck

Stores generated decks. **Local to the user's machine**, like `Collection` and `DeckCard`.

```
Deck

id

user_id

commander_id

created_at
```

---

# Deck Cards

Cards inside generated decks.

```
DeckCard

deck_id

card_id

owned

proxy
```

---

# Card Cache

Local display data for cards the client has seen, so rendering a collection is a local read rather than one API call per card. A cache, not a source of truth — deletable and rebuildable from the Knowledge API at any time. It is also what keeps collection browsing and hand-editing decks working while the Knowledge API is unreachable.

```
CardCache

oracle_id

<card display fields>

snapshot_version

fetched_at
```

---

# 9. Knowledge Documents

Knowledge documents are generated from normalized data.

They are not manually created Markdown files.

A document is generated from:

- Card data.
- Card metadata.

Example:

```
Card:

Cultivate

Summary:

A reliable green ramp spell that fixes mana.

Roles:

Ramp
Mana Fixing

Themes:

Big Mana

Strengths:

Consistent acceleration.

Weakness:

Sorcery speed.

Oracle Text:

Search your library...
```

This document is embedded and stored in the shared knowledge database, alongside the card it describes.

---

# 10. Knowledge Storage Structure

Stored in the cloud Postgres, with `pgvector` for the embedding. Each card has:

## Document

The generated knowledge document.

## Filter Fields

Structured fields that retrieval filters on, in SQL, alongside the similarity search. Example:

```
{
"name":"Cultivate",
"colors":["G"],
"roles":["Ramp"],
"themes":["Big Mana"],
"mana_value":3
}
```

## Embedding

Generated by the Hugging Face model, stored as a `pgvector` column and indexed HNSW with `vector_cosine_ops`.

Because document, filter fields, and embedding live in one table, retrieval is a single query returning candidates that are both semantically similar **and** legal to play — color identity and format legality are hard constraints, not preferences, so filtering them afterward would only shrink the usable candidate list.

---

# 11. Deck Generation Pipeline

Runs when a user requests a deck.

---

## Step 1

Receive selected cards.

Example:

```
Hardened Scales
The Ozolith
```

---

## Step 2

Generate Retrieval Query

Backend analyzes:

- Colors.
- Themes.
- Roles.
- Mechanics.

---

## Step 3

Retrieve Candidate Cards

The client calls the Knowledge API (`POST /v1/retrieve`). The query is embedded server-side; the response returns:

- Similar cards.
- Synergistic cards.
- Supporting cards.

Already filtered server-side by color identity and format legality, which are Commander rules rather than preferences.

Target:

100-200 candidate cards.

This is the only step that leaves the user's machine, and it carries cards — never anything about the user.

---

## Step 4

Model Deck Construction

The configured model provider — local or frontier, the user's choice — receives:

- Selected cards.
- Candidate cards.
- User preferences.
- Commander rules.

The model determines:

- Commander.
- Strategy.
- Win condition.
- Ramp package.
- Removal package.
- Card draw.
- Protection.
- Lands.

---

## Step 5

Deck Validation

Python validates:

- 100 cards.
- Commander legality.
- Color identity.
- Singleton rules.

If invalid:

- Send errors back to the model.
- Regenerate.

Bounded retries. After the limit, return the best deck produced plus the specific unresolved violations — never loop forever, and never present an invalid deck as valid.

---

## Step 6

Return Result

Frontend displays:

- Commander.
- Full deck list.
- Owned cards.
- Missing cards.
- Proxy cards.
- Deck explanation.

---

# 12. Model Responsibilities

The model should:

- Understand strategies.
- Recommend cards.
- Build decks.
- Explain decisions.
- Select commanders.

The model should not:

- Search the card database.
- Validate rules.
- Track collections.
- Calculate legality.

Those are backend responsibilities, enforced in code.

This division is load-bearing now that users may bring a small local model. Retrieval is a filtered query in the knowledge plane; validation is deterministic Python in the client plane. A model that hallucinates an illegal card cannot produce an invalid deck — it produces a validation failure and a repair round.

**Correctness does not depend on model quality; only deck quality does.**

---

# 13. Project Structure

```
project/

frontend/

    Vite
    React
    React Router
    TypeScript


backend/

    -- client plane (the user's machine) --

    api/

    deck_pipeline/

        retrieval.py          (Knowledge API client)
        prompt_builder.py
        generator.py
        validator.py

    ai/

        provider.py           (ModelProvider interface)
        providers/
            anthropic_provider.py
            openai_compatible.py
            ollama_provider.py


    -- knowledge plane (hosted) --

    knowledge_api/

    knowledge_pipeline/

        scryfall_importer/
        metadata_generator.py
        document_generator.py
        embeddings.py


    -- shared --

    database/

        knowledge_models.py   (cloud)
        local_models.py       (client)
```

Full module layout: `architecture.md#project-structure`.

---

# 14. MVP Success Criteria

The MVP is successful if:

- A user can select cards.
- The system understands the strategy.
- The system retrieves relevant cards.
- The model generates a complete Commander deck.
- The deck follows Commander rules.
- The system explains recommendations.
- The user can identify owned vs missing cards.
- A new user can generate their first deck without building a knowledge base.
- A user can generate a deck with a local model and no API key, and the result is still a legal deck.
- The user's collection and decks never leave their machine.

---

# 15. Future Enhancements

After MVP validation:

- ManaBox API integration.
- Moxfield export.
- Archidekt export.
- EDHREC-style statistics.
- Marketplace integration.
- Deck refinement.
- Upgrade suggestions.
- Multi-format support.

---

# Final Design Principle

The application should treat Magic knowledge as structured data first and AI reasoning second.

The Knowledge Pipeline creates reusable understanding of cards.

The Deck Generation Pipeline uses that understanding to make strategic decisions.

The model is the expert deck builder.

The backend is responsible for retrieval, validation, and correctness.

This keeps the MVP simple while leaving a clear path toward a much larger AI-powered Commander assistant.