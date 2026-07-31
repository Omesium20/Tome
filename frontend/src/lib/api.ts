import type {
  Card,
  Deck,
  DeckGenerationRequest,
  GeneratedDeckSummary,
  GenerationPhase,
  LibraryCard,
} from "./types";
import {
  mockAddToCollection,
  mockGetCollection,
  mockSearchCards,
} from "./mock/collection";
import { mockGenerateDeck, mockGetCardsByIds } from "./mock/generate";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// The backend routes are still stubs, so mocks are on unless explicitly disabled.
const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS !== "false";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    throw new Error(`Request to ${path} failed: ${res.status} ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

export function generateDeck(payload: DeckGenerationRequest): Promise<Deck> {
  return request<Deck>("/api/decks/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function uploadCollection(file: File): Promise<{ imported: number }> {
  const formData = new FormData();
  formData.append("file", file);

  return fetch(`${API_BASE_URL}/api/collection/import`, {
    method: "POST",
    body: formData,
  }).then((res) => {
    if (!res.ok) {
      throw new Error(`Collection import failed: ${res.status} ${res.statusText}`);
    }
    return res.json();
  });
}

export function getCollection(): Promise<LibraryCard[]> {
  if (USE_MOCKS) return mockGetCollection();
  return request<LibraryCard[]>("/api/collection");
}

export function searchCards(query: string): Promise<Card[]> {
  if (USE_MOCKS) return mockSearchCards(query);
  return request<Card[]>(`/api/cards/search?q=${encodeURIComponent(query)}`);
}

export function getCardsByIds(ids: string[]): Promise<Card[]> {
  if (USE_MOCKS) return mockGetCardsByIds(ids);
  return request<Card[]>(`/api/cards?ids=${ids.map(encodeURIComponent).join(",")}`);
}

// Deck generation as the deck builder consumes it: build-around card names in,
// commander + card ids + explanation out. onPhase reports pipeline progress
// (mock only for now — the real backend runs synchronously).
export function generateDeckFromContext(
  contextCardNames: string[],
  onPhase?: (phase: GenerationPhase) => void,
): Promise<GeneratedDeckSummary> {
  if (USE_MOCKS) return mockGenerateDeck(contextCardNames, onPhase);
  return generateDeck({
    buildAroundCardNames: contextCardNames,
    powerLevel: "Focused",
    collectionPreference: "Build strongest possible deck",
  }).then((deck) => ({
    commanderId: deck.commanderId,
    cards: deck.cards.map((c) => ({ cardId: c.cardId, quantity: 1 })),
    explanation: deck.explanation,
  }));
}

export function addToCollection(
  cardId: string,
  quantity: number,
): Promise<LibraryCard> {
  if (USE_MOCKS) return mockAddToCollection(cardId, quantity);
  return request<LibraryCard>("/api/collection", {
    method: "POST",
    body: JSON.stringify({ cardId, quantity }),
  });
}
