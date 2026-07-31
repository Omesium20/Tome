"use client";

import type { LibraryCard } from "@/lib/types";
import { CardTile } from "./CardTile";

export function CollectionGridSkeleton() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">
      {Array.from({ length: 18 }, (_, i) => (
        <div
          key={i}
          className="aspect-card animate-pulse rounded-xl border border-line bg-panel"
        />
      ))}
    </div>
  );
}

export function CollectionGrid({
  cards,
  onSelect,
}: {
  cards: LibraryCard[];
  onSelect: (card: LibraryCard) => void;
}) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">
      {cards.map((card) => (
        <CardTile key={card.id} card={card} onSelect={onSelect} />
      ))}
    </div>
  );
}
