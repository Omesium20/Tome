// The deck builder's in-progress deck, persisted to localStorage. Shared with
// the /decks page, which writes a saved deck here to open it in the builder.
// deckId/name link the working state back to its SavedDeck record (null until
// the deck is saved for the first time).

export const WORKING_DECK_KEY = "tome.deck.v1";

export interface WorkingDeck {
  deckId: string | null;
  name: string | null;
  commanderId: string | null;
  cards: Record<string, number>; // cardId -> quantity
}

export const EMPTY_WORKING_DECK: WorkingDeck = {
  deckId: null,
  name: null,
  commanderId: null,
  cards: {},
};

export function loadWorkingDeck(): WorkingDeck {
  if (typeof window === "undefined") return EMPTY_WORKING_DECK;
  try {
    const raw = window.localStorage.getItem(WORKING_DECK_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<WorkingDeck>;
      // Older stored decks predate deckId/name — default them.
      return {
        deckId: parsed.deckId ?? null,
        name: parsed.name ?? null,
        commanderId: parsed.commanderId ?? null,
        cards: parsed.cards ?? {},
      };
    }
  } catch {
    // corrupt state — start fresh
  }
  return EMPTY_WORKING_DECK;
}

export function saveWorkingDeck(deck: WorkingDeck) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(WORKING_DECK_KEY, JSON.stringify(deck));
  }
}

export function workingDeckIsEmpty(deck: WorkingDeck): boolean {
  return deck.commanderId === null && Object.keys(deck.cards).length === 0;
}
