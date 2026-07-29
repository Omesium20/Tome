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

The collection is stored locally.

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

The system consists of two independent pipelines.

## Pipeline 1

Knowledge Pipeline

Purpose:

Prepare Magic card knowledge.

Runs offline.

---

## Pipeline 2

Deck Generation Pipeline

Purpose:

Build decks using retrieved knowledge.

Runs when users request decks.

---

# High-Level Architecture

```
                    Next.js
                       |
                       |
                   FastAPI
                       |
                  LangChain
                       |
        --------------------------------
        |                              |
 Knowledge Pipeline          Deck Generation Pipeline
        |                              |
        --------------------------------
                       |
                  ChromaDB
```

---

# 6. Technology Stack

## Frontend

- Next.js
- React
- TypeScript

Responsibilities:

- User interface.
- Collection management.
- Card selection.
- Deck display.

---

## Backend

- Python
- FastAPI

Responsibilities:

- API endpoints.
- AI orchestration.
- Deck validation.
- Collection management.
- Pipeline execution.

---

## AI Framework

LangChain

Responsibilities:

- Retrieval.
- Prompt construction.
- Claude API management.
- Output parsing.

---

## Large Language Model

Anthropic Claude API

Responsibilities:

- Card analysis.
- Deck strategy.
- Commander selection.
- Deck construction.
- Explanations.

---

## Embedding Model

Hugging Face Sentence Transformer model.

Responsibilities:

- Convert card knowledge documents into vectors.

---

## Vector Database

ChromaDB

Responsibilities:

- Store embeddings.
- Perform similarity searches.
- Retrieve relevant cards.

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

The pipeline runs offline.

---

# Pipeline Flow

```
Scryfall

↓

Card Import

↓

Claude Metadata Generation

↓

Normalized Database

↓

Knowledge Document Generation

↓

Hugging Face Embeddings

↓

ChromaDB
```

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

Tracks owned cards.

```
Collection

user_id

card_id

quantity
```

---

# Deck

Stores generated decks.

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

This document is embedded into ChromaDB.

---

# 10. ChromaDB Structure

Each card has:

## Document

The generated knowledge document.

## Metadata

Example:

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

Generated by Hugging Face.

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

ChromaDB returns:

- Similar cards.
- Synergistic cards.
- Supporting cards.

Target:

100-200 candidate cards.

---

## Step 4

Claude Deck Construction

Claude receives:

- Selected cards.
- Candidate cards.
- User preferences.
- Commander rules.

Claude determines:

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

- Send errors back to Claude.
- Regenerate.

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

# 12. Claude Responsibilities

Claude should:

- Understand strategies.
- Recommend cards.
- Build decks.
- Explain decisions.
- Select commanders.

Claude should not:

- Search the card database.
- Validate rules.
- Track collections.
- Calculate legality.

Those are backend responsibilities.

---

# 13. Project Structure

```
project/

frontend/

    Next.js
    React
    TypeScript


backend/

    api/

    knowledge_pipeline/

        scryfall_importer.py
        metadata_generator.py
        document_generator.py
        embeddings.py


    deck_pipeline/

        retrieval.py
        prompt_builder.py
        generator.py
        validator.py


    ai/

        claude_client.py


    database/
```

---

# 14. MVP Success Criteria

The MVP is successful if:

- A user can select cards.
- The system understands the strategy.
- The system retrieves relevant cards.
- Claude generates a complete Commander deck.
- The deck follows Commander rules.
- The system explains recommendations.
- The user can identify owned vs missing cards.

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

Claude is the expert deck builder.

The backend is responsible for retrieval, validation, and correctness.

This keeps the MVP simple while leaving a clear path toward a much larger AI-powered Commander assistant.