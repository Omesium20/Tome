"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import type { Card, LibraryCard } from "@/lib/types";
import {
  applyFilters,
  DEFAULT_FILTERS,
  type CollectionFilters,
} from "@/lib/filter-cards";
import { CollectionFilterControls } from "@/components/collection/CollectionFilterControls";

export const CARD_DRAG_TYPE = "application/tome-card-id";

function isBasicLand(card: Card): boolean {
  return card.typeLine.includes("Basic Land");
}

function PanelCardTile({
  card,
  inDeck,
  onAdd,
}: {
  card: LibraryCard;
  inDeck: boolean;
  onAdd: (card: Card) => void;
}) {
  const addable = !inDeck || isBasicLand(card);
  return (
    <button
      type="button"
      draggable={addable}
      onDragStart={(e) => {
        e.dataTransfer.setData(CARD_DRAG_TYPE, card.id);
        e.dataTransfer.setData("text/plain", card.name);
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={() => addable && onAdd(card)}
      disabled={!addable}
      aria-label={
        addable ? `Add ${card.name} to deck` : `${card.name} is already in the deck`
      }
      title={addable ? "Drag into the deck, or click to add" : "Already in deck"}
      className={`group relative block w-full rounded-lg transition-transform duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
        addable ? "cursor-grab hover:-translate-y-0.5 active:cursor-grabbing" : "cursor-not-allowed"
      }`}
    >
      <div
        className={`relative aspect-card overflow-hidden rounded-lg border border-line bg-panel ${
          addable ? "" : "opacity-40"
        }`}
      >
        <Image
          src={card.imageUrl}
          alt={card.name}
          fill
          sizes="140px"
          className="object-cover"
        />
      </div>
      <span className="pointer-events-none absolute bottom-1 right-1 rounded border border-line bg-bg/90 px-1 py-0.5 text-[10px] font-medium text-ink-muted backdrop-blur-sm">
        ×{card.quantity}
      </span>
      {!addable && (
        <span className="pointer-events-none absolute left-1 top-1 rounded border border-line bg-bg/90 px-1 py-0.5 text-[10px] font-medium text-gold backdrop-blur-sm">
          In deck
        </span>
      )}
    </button>
  );
}

// Toggleable side panel: the user's collection, condensed, with the same
// filter mechanics as the collection page. Cards drag (or click) into the deck.
export function CollectionPanel({
  open,
  onClose,
  collection,
  deckCardIds,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  collection: LibraryCard[] | null;
  deckCardIds: Set<string>;
  onAdd: (card: Card) => void;
}) {
  const [filters, setFilters] = useState<CollectionFilters>(DEFAULT_FILTERS);

  const visibleCards = useMemo(
    () => (collection ? applyFilters(collection, filters) : []),
    [collection, filters],
  );

  if (!open) return null;

  return (
    <>
      {/* Backdrop for the overlay variant on small screens */}
      <div
        className="fixed inset-0 z-30 bg-black/50 lg:hidden"
        aria-hidden="true"
        onClick={onClose}
      />
      <aside
        aria-label="Your collection"
        className="fixed inset-y-0 right-0 z-40 w-80 overflow-y-auto border-l border-line bg-bg lg:sticky lg:top-[68px] lg:z-auto lg:max-h-[calc(100vh-68px)] lg:w-80 lg:shrink-0 lg:rounded-xl lg:border lg:bg-panel/40"
      >
        <div className="sticky top-0 z-10 border-b border-line bg-bg/95 p-3 backdrop-blur-sm lg:rounded-t-xl">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Collection</h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close collection panel"
              className="cursor-pointer rounded-md px-2 py-1 text-ink-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              ✕
            </button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CollectionFilterControls
              filters={filters}
              onChange={setFilters}
              searchWidthClass="w-full"
            />
          </div>
          <p className="mt-2 text-xs text-ink-muted" aria-live="polite">
            {visibleCards.length} {visibleCards.length === 1 ? "card" : "cards"} · drag
            or click to add
          </p>
        </div>

        <div className="p-3">
          {collection === null && (
            <div className="grid grid-cols-2 gap-2">
              {Array.from({ length: 8 }, (_, i) => (
                <div
                  key={i}
                  className="aspect-card animate-pulse rounded-lg border border-line bg-panel"
                />
              ))}
            </div>
          )}
          {collection !== null && visibleCards.length === 0 && (
            <p className="py-8 text-center text-sm text-ink-muted">
              No cards match your filters.
            </p>
          )}
          <div className="grid grid-cols-2 gap-2">
            {visibleCards.map((card) => (
              <PanelCardTile
                key={card.id}
                card={card}
                inDeck={deckCardIds.has(card.id)}
                onAdd={onAdd}
              />
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}
