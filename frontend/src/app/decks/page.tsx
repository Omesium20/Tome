"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { MAX_DECKS, type Card, type SavedDeck } from "@/lib/types";
import { deleteDeck, getCardsByIds, listDecks } from "@/lib/api";
import {
  EMPTY_WORKING_DECK,
  loadWorkingDeck,
  saveWorkingDeck,
  workingDeckIsEmpty,
} from "@/lib/working-deck";
import { DeckTile } from "@/components/decks/DeckTile";

function DeckGridSkeleton() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="animate-pulse overflow-hidden rounded-xl border border-line bg-panel">
          <div className="aspect-[16/9] bg-panel-raised" />
          <div className="space-y-2 p-3">
            <div className="h-4 w-2/3 rounded bg-panel-raised" />
            <div className="h-3 w-1/2 rounded bg-panel-raised" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DecksPage() {
  const router = useRouter();
  const [decks, setDecks] = useState<SavedDeck[] | null>(null);
  const [commanders, setCommanders] = useState<Record<string, Card>>({});

  useEffect(() => {
    void listDecks().then(async (loaded) => {
      setDecks(loaded);
      const ids = Array.from(
        new Set(
          loaded
            .map((d) => d.commanderId)
            .filter((id): id is string => id !== null),
        ),
      );
      if (ids.length > 0) {
        const cards = await getCardsByIds(ids);
        setCommanders(Object.fromEntries(cards.map((c) => [c.id, c])));
      }
    });
  }, []);

  const atCap = (decks?.length ?? 0) >= MAX_DECKS;

  const openDeck = useCallback(
    (deck: SavedDeck) => {
      const working = loadWorkingDeck();
      // Only warn when opening would discard a different in-progress deck.
      if (
        !workingDeckIsEmpty(working) &&
        working.deckId !== deck.id &&
        !window.confirm(
          `Open “${deck.name}”? The unsaved deck in the deck builder will be replaced.`,
        )
      ) {
        return;
      }
      saveWorkingDeck({
        deckId: deck.id,
        name: deck.name,
        commanderId: deck.commanderId,
        cards: Object.fromEntries(deck.cards.map((c) => [c.cardId, c.quantity])),
      });
      router.push("/deck-builder");
    },
    [router],
  );

  const createDeck = useCallback(() => {
    const working = loadWorkingDeck();
    if (
      !workingDeckIsEmpty(working) &&
      !window.confirm(
        "Start a new deck? The current deck in the deck builder will be replaced.",
      )
    ) {
      return;
    }
    saveWorkingDeck(EMPTY_WORKING_DECK);
    router.push("/deck-builder");
  }, [router]);

  const handleDelete = useCallback((deck: SavedDeck) => {
    if (!window.confirm(`Delete “${deck.name}”? This cannot be undone.`)) return;
    setDecks((prev) => prev?.filter((d) => d.id !== deck.id) ?? prev);
    void deleteDeck(deck.id);
  }, []);

  const countClass = useMemo(
    () => (atCap ? "text-warn" : "text-ink-muted"),
    [atCap],
  );

  return (
    <main className="mx-auto max-w-screen-2xl px-4 pb-16 sm:px-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3 pb-6 pt-6">
        <h1 className="text-2xl font-semibold tracking-tight">Decks</h1>
        {decks !== null && (
          <span className={`text-sm font-medium tabular-nums ${countClass}`} aria-live="polite">
            {decks.length} / {MAX_DECKS} decks
          </span>
        )}
      </div>

      {decks === null ? (
        <DeckGridSkeleton />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
          {decks.map((deck) => (
            <DeckTile
              key={deck.id}
              deck={deck}
              commander={deck.commanderId ? (commanders[deck.commanderId] ?? null) : null}
              onOpen={openDeck}
              onDelete={handleDelete}
            />
          ))}

          <button
            type="button"
            onClick={createDeck}
            disabled={atCap}
            title={atCap ? `Deck limit reached (${MAX_DECKS})` : undefined}
            className="flex min-h-[180px] cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line bg-transparent p-6 text-ink-muted transition-colors duration-150 hover:border-ink-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-line disabled:hover:text-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            <svg viewBox="0 0 16 16" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
              <path d="M8 3v10M3 8h10" />
            </svg>
            <span className="text-sm font-medium">
              {atCap ? "Deck limit reached" : "New deck"}
            </span>
            {atCap && (
              <span className="text-xs">Delete a deck to make room.</span>
            )}
          </button>
        </div>
      )}

      {decks !== null && decks.length === 0 && (
        <p className="mx-auto mt-8 max-w-md text-center text-sm leading-relaxed text-ink-muted">
          No saved decks yet. Create one here or build a deck in the deck
          builder and save it — it will show up in this list.
        </p>
      )}
    </main>
  );
}
