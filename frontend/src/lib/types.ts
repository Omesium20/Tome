// Mirrors backend/database/models.py and docs/data-model.md.
// Keep in sync when the backend schema changes.

export interface Card {
  id: string;
  oracleId: string;
  name: string;
  manaCost: string;
  manaValue: number;
  oracleText: string;
  colors: string[];
  colorIdentity: string[];
  typeLine: string;
  keywords: string[];
  imageUrl: string;
}

export interface CardMetadata {
  cardId: string;
  summary: string;
  roles: string[];
  themes: string[];
  gameStage: "Early" | "Mid" | "Late";
  powerRating: number;
  strengths: string[];
  weaknesses: string[];
  synergyTags: string[];
}

// A card joined with the user's Collection row — what collection endpoints return.
export interface LibraryCard extends Card {
  quantity: number;
}

export interface CollectionEntry {
  userId: string;
  cardId: string;
  quantity: number;
}

export type PowerLevel = "Casual" | "Focused" | "High Power" | "Competitive";

export type CollectionPreference =
  | "Build strongest possible deck"
  | "Prefer cards I own"
  | "Only use my collection";

export interface DeckGenerationRequest {
  buildAroundCardNames: string[];
  powerLevel: PowerLevel;
  collectionPreference: CollectionPreference;
}

// Pipeline steps surfaced during generation (mirrors docs/architecture.md
// Deck Generation Pipeline steps 2-5).
export type GenerationPhase =
  | "analyzing"
  | "retrieving"
  | "constructing"
  | "validating";

// Minimal shape the deck builder needs back from generation: card ids plus
// the AI's rationale. Card data is resolved separately via the cards API.
export interface GeneratedDeckSummary {
  commanderId: string;
  cards: { cardId: string; quantity: number }[];
  explanation: string;
}

export interface DeckCard {
  deckId: string;
  cardId: string;
  owned: boolean;
  proxy: boolean;
}

export interface Deck {
  id: string;
  userId: string;
  commanderId: string;
  createdAt: string;
  cards: DeckCard[];
  explanation: string;
}
