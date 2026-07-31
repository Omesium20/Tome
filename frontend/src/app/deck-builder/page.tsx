"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Card, GenerationPhase, LibraryCard } from "@/lib/types";
import { generateDeckFromContext, getCardsByIds, getCollection } from "@/lib/api";
import { compareRoles, getPrimaryRole } from "@/lib/mock/metadata";
import { DeckToolbar } from "@/components/deck-builder/DeckToolbar";
import { DeckColumn, type DeckEntry } from "@/components/deck-builder/DeckColumn";
import { CommanderSlot } from "@/components/deck-builder/CommanderSlot";
import { CardPreviewModal } from "@/components/collection/CardPreviewModal";

const STORAGE_KEY = "tome.deck.v1";

interface DeckState {
  commanderId: string | null;
  cards: Record<string, number>; // cardId -> quantity
}

const EMPTY_DECK: DeckState = { commanderId: null, cards: {} };

function loadDeck(): DeckState {
  if (typeof window === "undefined") return EMPTY_DECK;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as DeckState;
  } catch {
    // corrupt state — start fresh
  }
  return EMPTY_DECK;
}

export default function DeckBuilderPage() {
  const [deck, setDeck] = useState<DeckState | null>(null);
  const [cardCache, setCardCache] = useState<Record<string, Card>>({});
  const [ownedQuantities, setOwnedQuantities] = useState<Record<string, number>>({});
  const [aiEnabled, setAiEnabled] = useState(false);
  const [generationPhase, setGenerationPhase] = useState<GenerationPhase | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [previewEntry, setPreviewEntry] = useState<DeckEntry | null>(null);
  const loaded = useRef(false);

  // Initial load: stored deck + collection (for owned badges in search).
  useEffect(() => {
    const stored = loadDeck();
    setDeck(stored);
    loaded.current = true;

    const ids = Object.keys(stored.cards);
    if (stored.commanderId) ids.push(stored.commanderId);
    if (ids.length > 0) {
      void getCardsByIds(ids).then((cards) => {
        setCardCache((prev) => {
          const next = { ...prev };
          for (const card of cards) next[card.id] = card;
          return next;
        });
      });
    }
    void getCollection().then((collection) => {
      const owned: Record<string, number> = {};
      for (const card of collection) owned[card.id] = card.quantity;
      setOwnedQuantities(owned);
    });
  }, []);

  // Persist on every change after the initial load.
  useEffect(() => {
    if (deck && loaded.current && typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(deck));
    }
  }, [deck]);

  const cacheCards = useCallback((cards: Card[]) => {
    setCardCache((prev) => {
      const next = { ...prev };
      for (const card of cards) next[card.id] = card;
      return next;
    });
  }, []);

  const addCard = useCallback(
    (card: Card) => {
      cacheCards([card]);
      setDeck((prev) => {
        if (!prev) return prev;
        const existing = prev.cards[card.id] ?? 0;
        // Singleton format: only basic lands stack. (Real enforcement is the
        // backend validator's job — this is just a sane editing default.)
        if (existing > 0 && !card.typeLine.includes("Basic Land")) return prev;
        return { ...prev, cards: { ...prev.cards, [card.id]: existing + 1 } };
      });
    },
    [cacheCards],
  );

  const removeCard = useCallback((card: Card) => {
    setDeck((prev) => {
      if (!prev) return prev;
      const existing = prev.cards[card.id] ?? 0;
      const cards = { ...prev.cards };
      if (existing <= 1) delete cards[card.id];
      else cards[card.id] = existing - 1;
      return { ...prev, cards };
    });
  }, []);

  const setCommander = useCallback((card: Card) => {
    setDeck((prev) => {
      if (!prev) return prev;
      const cards = { ...prev.cards };
      delete cards[card.id];
      // A displaced commander goes back into its role column.
      if (prev.commanderId && prev.commanderId !== card.id) {
        cards[prev.commanderId] = cards[prev.commanderId] ?? 1;
      }
      return { commanderId: card.id, cards };
    });
  }, []);

  const removeCommander = useCallback(() => {
    setDeck((prev) => (prev ? { ...prev, commanderId: null } : prev));
  }, []);

  const clearDeck = useCallback(() => {
    if (window.confirm("Clear the entire deck?")) {
      setDeck(EMPTY_DECK);
      setExplanation(null);
    }
  }, []);

  const generate = useCallback(async () => {
    if (!deck) return;
    const contextIds = Object.keys(deck.cards);
    if (deck.commanderId) contextIds.push(deck.commanderId);
    const contextNames = contextIds
      .map((id) => cardCache[id]?.name)
      .filter((name): name is string => Boolean(name));

    setGenerationPhase("analyzing");
    setExplanation(null);
    try {
      const result = await generateDeckFromContext(contextNames, setGenerationPhase);
      const ids = result.cards.map((c) => c.cardId).concat(result.commanderId);
      cacheCards(await getCardsByIds(ids));
      setDeck({
        commanderId: result.commanderId,
        cards: Object.fromEntries(result.cards.map((c) => [c.cardId, c.quantity])),
      });
      setExplanation(result.explanation);
    } finally {
      setGenerationPhase(null);
    }
  }, [deck, cardCache, cacheCards]);

  const commander = deck?.commanderId ? (cardCache[deck.commanderId] ?? null) : null;

  const columns = useMemo(() => {
    if (!deck) return [];
    const groups = new Map<string, DeckEntry[]>();
    for (const [cardId, quantity] of Object.entries(deck.cards)) {
      const card = cardCache[cardId];
      if (!card) continue;
      const role = getPrimaryRole(card);
      const entries = groups.get(role) ?? [];
      entries.push({ card, quantity });
      groups.set(role, entries);
    }
    for (const entries of groups.values()) {
      entries.sort(
        (a, b) =>
          a.card.manaValue - b.card.manaValue || a.card.name.localeCompare(b.card.name),
      );
    }
    return Array.from(groups.entries())
      .sort(([a], [b]) => compareRoles(a, b))
      .map(([role, entries]) => ({ role, entries }));
  }, [deck, cardCache]);

  const deckCount = useMemo(() => {
    if (!deck) return 0;
    const spellCount = Object.values(deck.cards).reduce((sum, q) => sum + q, 0);
    return spellCount + (deck.commanderId ? 1 : 0);
  }, [deck]);

  const deckCardIds = useMemo(() => {
    const ids = new Set(Object.keys(deck?.cards ?? {}));
    if (deck?.commanderId) ids.add(deck.commanderId);
    return ids;
  }, [deck]);

  const previewCard: LibraryCard | null = previewEntry
    ? { ...previewEntry.card, quantity: previewEntry.quantity }
    : null;

  return (
    <main className="mx-auto max-w-screen-2xl px-4 pb-16 sm:px-6">
      <div className="flex items-baseline justify-between pb-2 pt-6">
        <h1 className="text-2xl font-semibold tracking-tight">Deck Builder</h1>
      </div>

      <DeckToolbar
        deckCount={deckCount}
        deckCardIds={deckCardIds}
        ownedQuantities={ownedQuantities}
        aiEnabled={aiEnabled}
        onToggleAi={() => setAiEnabled((v) => !v)}
        generationPhase={generationPhase}
        onGenerate={() => void generate()}
        onAdd={addCard}
        onClear={clearDeck}
      />

      {explanation && (
        <div className="mt-6 rounded-xl border border-ai/40 bg-ai/10 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-medium text-ai">AI deck rationale</h2>
              <p className="mt-1 text-sm leading-relaxed text-ink">{explanation}</p>
            </div>
            <button
              type="button"
              onClick={() => setExplanation(null)}
              aria-label="Dismiss explanation"
              className="cursor-pointer rounded-md px-2 py-1 text-ink-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] items-start gap-x-4 gap-y-8 pt-6 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
        <CommanderSlot
          commander={commander}
          onPreview={setPreviewEntry}
          onRemove={removeCommander}
        />
        {columns.map(({ role, entries }) => (
          <DeckColumn
            key={role}
            role={role}
            entries={entries}
            onPreview={setPreviewEntry}
            onRemove={removeCard}
            onSetCommander={setCommander}
          />
        ))}
        {deck !== null && columns.length === 0 && (
          <div className="col-span-full flex justify-center py-12 sm:col-span-2 sm:py-0 lg:col-span-3">
            <div className="max-w-md rounded-xl border border-line bg-panel p-8 text-center">
              <h2 className="text-lg font-medium">Start building</h2>
              <p className="mt-2 text-sm leading-relaxed text-ink-muted">
                Search above to add cards to the deck — they&apos;ll organize into
                columns by role. Turn on AI generation to have Tome build a full
                deck around whatever you&apos;ve added.
              </p>
            </div>
          </div>
        )}
      </div>

      <CardPreviewModal
        card={previewCard}
        onClose={() => setPreviewEntry(null)}
        quantityLabel="In deck"
      />
    </main>
  );
}
