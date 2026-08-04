// In-browser stand-in for the future saved-deck endpoints
// (GET /api/decks, POST /api/decks, PUT /api/decks/{id}, DELETE /api/decks/{id}).
// Persists to localStorage like the collection mock; enforces the MAX_DECKS cap
// the way the real backend will.

import { MAX_DECKS, type SavedDeck, type SaveDeckInput } from "@/lib/types";

const STORAGE_KEY = "tome.mock.decks.v1";

function loadStored(): SavedDeck[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      return JSON.parse(raw) as SavedDeck[];
    } catch {
      // corrupt state — start fresh
    }
  }
  return [];
}

function saveStored(decks: SavedDeck[]) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(decks));
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function mockListDecks(): Promise<SavedDeck[]> {
  await delay(400);
  return loadStored().sort(
    (a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt),
  );
}

export async function mockSaveDeck(input: SaveDeckInput): Promise<SavedDeck> {
  await delay(300);
  const decks = loadStored();
  const now = new Date().toISOString();

  if (input.id) {
    const existing = decks.find((d) => d.id === input.id);
    if (existing) {
      existing.name = input.name;
      existing.commanderId = input.commanderId;
      existing.cards = input.cards;
      existing.updatedAt = now;
      saveStored(decks);
      return existing;
    }
    // Saved deck was deleted out from under us — fall through and recreate.
  }

  if (decks.length >= MAX_DECKS) {
    throw new Error(
      `Deck limit reached (${MAX_DECKS}). Delete a deck before saving a new one.`,
    );
  }

  const created: SavedDeck = {
    id: `deck-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: input.name,
    commanderId: input.commanderId,
    cards: input.cards,
    createdAt: now,
    updatedAt: now,
  };
  decks.push(created);
  saveStored(decks);
  return created;
}

export async function mockDeleteDeck(id: string): Promise<void> {
  await delay(300);
  saveStored(loadStored().filter((d) => d.id !== id));
}
