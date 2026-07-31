"use client";

import type { Card } from "@/lib/types";
import { DeckCardItem, type DeckEntry } from "./DeckColumn";

// Dedicated commander column: the assigned commander, or an empty slot.
export function CommanderSlot({
  commander,
  onPreview,
  onRemove,
}: {
  commander: Card | null;
  onPreview: (entry: DeckEntry) => void;
  onRemove: (card: Card) => void;
}) {
  return (
    <section aria-label="Commander">
      <header className="mb-2 flex items-baseline justify-between px-0.5">
        <h2 className="text-sm font-medium text-gold">Commander</h2>
        <span className="text-xs text-ink-muted">Qty: {commander ? 1 : 0}</span>
      </header>
      {commander ? (
        <div className="rounded-xl outline outline-1 outline-offset-2 outline-gold/60">
          <DeckCardItem
            entry={{ card: commander, quantity: 1 }}
            stacked={false}
            onPreview={onPreview}
            onRemove={onRemove}
          />
        </div>
      ) : (
        <div className="flex aspect-card w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line bg-panel/50 p-4 text-center">
          <svg viewBox="0 0 16 16" className="h-6 w-6 text-gold/70" fill="currentColor" aria-hidden="true">
            <path d="M1 5l3 2.5L8 3l4 4.5L15 5l-1.5 7h-11L1 5zm2 9h10v1.5H3V14z" />
          </svg>
          <p className="text-xs leading-relaxed text-ink-muted">
            No commander yet. Hover a legendary creature in the deck and use the
            crown, or let AI generation pick one.
          </p>
        </div>
      )}
    </section>
  );
}
