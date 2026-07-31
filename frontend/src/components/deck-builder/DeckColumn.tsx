"use client";

import Image from "next/image";
import type { Card } from "@/lib/types";

export interface DeckEntry {
  card: Card;
  quantity: number;
}

function CrownIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
      <path d="M1 5l3 2.5L8 3l4 4.5L15 5l-1.5 7h-11L1 5zm2 9h10v1.5H3V14z" />
    </svg>
  );
}

function isLegendaryCreature(card: Card): boolean {
  return card.typeLine.includes("Legendary") && card.typeLine.includes("Creature");
}

// One card in a stacked column: overlapped so only the title bar shows, the
// full card revealed on hover/focus. Action buttons sit over the title strip.
export function DeckCardItem({
  entry,
  stacked,
  onPreview,
  onRemove,
  onSetCommander,
}: {
  entry: DeckEntry;
  stacked: boolean;
  onPreview: (entry: DeckEntry) => void;
  onRemove: (card: Card) => void;
  onSetCommander?: (card: Card) => void;
}) {
  const { card, quantity } = entry;
  return (
    <div
      className={`group relative transition-transform duration-150 hover:z-20 focus-within:z-20 ${
        stacked ? "mt-[-128%]" : ""
      }`}
    >
      <button
        type="button"
        onClick={() => onPreview(entry)}
        aria-label={`${card.name}${quantity > 1 ? `, ${quantity} copies` : ""}`}
        className="block w-full cursor-pointer rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
      >
        <div className="relative aspect-card w-full overflow-hidden rounded-xl border border-line bg-panel shadow-md transition-shadow duration-150 group-hover:shadow-xl">
          <Image
            src={card.imageUrl}
            alt={card.name}
            fill
            sizes="(max-width: 640px) 45vw, 220px"
            className="object-cover"
          />
        </div>
      </button>

      {quantity > 1 && (
        <span className="pointer-events-none absolute left-1.5 top-1.5 rounded-md border border-line bg-bg/90 px-1.5 py-0.5 text-xs font-medium text-ink backdrop-blur-sm">
          ×{quantity}
        </span>
      )}

      <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100">
        {onSetCommander && isLegendaryCreature(card) && (
          <button
            type="button"
            onClick={() => onSetCommander(card)}
            aria-label={`Set ${card.name} as commander`}
            title="Set as commander"
            className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md border border-line bg-bg/90 text-gold backdrop-blur-sm transition-colors hover:border-gold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            <CrownIcon />
          </button>
        )}
        <button
          type="button"
          onClick={() => onRemove(card)}
          aria-label={`Remove ${card.name} from deck`}
          title="Remove from deck"
          className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md border border-line bg-bg/90 text-ink-muted backdrop-blur-sm transition-colors hover:border-warn hover:text-warn focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M3 8h10" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  );
}

export function DeckColumn({
  role,
  entries,
  onPreview,
  onRemove,
  onSetCommander,
}: {
  role: string;
  entries: DeckEntry[];
  onPreview: (entry: DeckEntry) => void;
  onRemove: (card: Card) => void;
  onSetCommander?: (card: Card) => void;
}) {
  const count = entries.reduce((sum, e) => sum + e.quantity, 0);
  return (
    <section aria-label={`${role}, ${count} cards`}>
      <header className="mb-2 flex items-baseline justify-between px-0.5">
        <h2 className="text-sm font-medium text-ink">{role}</h2>
        <span className="text-xs text-ink-muted">Qty: {count}</span>
      </header>
      <div>
        {entries.map((entry, i) => (
          <DeckCardItem
            key={entry.card.id}
            entry={entry}
            stacked={i > 0}
            onPreview={onPreview}
            onRemove={onRemove}
            onSetCommander={onSetCommander}
          />
        ))}
      </div>
    </section>
  );
}
