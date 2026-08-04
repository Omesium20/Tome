"use client";

import Image from "next/image";
import type { Card, SavedDeck } from "@/lib/types";

export function deckCardCount(deck: SavedDeck): number {
  const spells = deck.cards.reduce((sum, c) => sum + c.quantity, 0);
  return spells + (deck.commanderId ? 1 : 0);
}

export function DeckTile({
  deck,
  commander,
  onOpen,
  onDelete,
}: {
  deck: SavedDeck;
  commander: Card | null;
  onOpen: (deck: SavedDeck) => void;
  onDelete: (deck: SavedDeck) => void;
}) {
  const count = deckCardCount(deck);
  const updated = new Date(deck.updatedAt).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="group relative">
      <button
        type="button"
        onClick={() => onOpen(deck)}
        aria-label={`Open ${deck.name} in the deck builder`}
        className="block w-full cursor-pointer rounded-xl text-left transition-transform duration-200 hover:-translate-y-1 focus-visible:-translate-y-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
      >
        <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-md transition-shadow duration-200 group-hover:shadow-xl">
          <div className="relative aspect-[16/9] bg-panel-raised">
            {commander ? (
              // Full card art cropped to a banner; nudge upward so the art
              // box (not the title bar) fills the frame.
              <Image
                src={commander.imageUrl}
                alt=""
                fill
                sizes="(max-width: 640px) 90vw, (max-width: 1024px) 45vw, 300px"
                className="object-cover object-[center_20%]"
              />
            ) : (
              <div className="flex h-full items-center justify-center text-ink-muted/50">
                <svg viewBox="0 0 24 24" className="h-10 w-10" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
                  <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3h11A1.5 1.5 0 0 1 19 4.5v15a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5v-15Z" />
                  <path d="M8 3v18" />
                </svg>
              </div>
            )}
          </div>
          <div className="p-3">
            <h3 className="truncate text-sm font-medium text-ink">{deck.name}</h3>
            <p className="mt-1 flex items-center gap-2 text-xs text-ink-muted">
              <span className={`tabular-nums ${count === 100 ? "text-success" : ""}`}>
                {count} cards
              </span>
              <span aria-hidden="true">·</span>
              <span>Updated {updated}</span>
            </p>
          </div>
        </div>
      </button>
      <button
        type="button"
        onClick={() => onDelete(deck)}
        aria-label={`Delete ${deck.name}`}
        title="Delete deck"
        className="absolute right-2 top-2 cursor-pointer rounded-md border border-line bg-bg/90 p-1.5 text-ink-muted opacity-0 backdrop-blur-sm transition-all duration-150 hover:text-warn focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action group-hover:opacity-100 group-focus-within:opacity-100"
      >
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
          <path d="M2.5 4h11M6.5 4V2.5h3V4M5 4l.5 9.5h5L11 4M6.8 6.5v4.7M9.2 6.5v4.7" />
        </svg>
      </button>
    </div>
  );
}
