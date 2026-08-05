"use client";

import Image from "next/image";
import type { LibraryCard } from "@/lib/types";

export function CardTile({
  card,
  onSelect,
}: {
  card: LibraryCard;
  onSelect: (card: LibraryCard) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(card)}
      className="group relative block w-full cursor-pointer rounded-xl transition-transform duration-200 hover:-translate-y-1 focus-visible:-translate-y-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action [content-visibility:auto] [contain-intrinsic-size:180px_252px]"
      aria-label={`${card.name}, ${card.quantity} in collection`}
    >
      <div className="relative aspect-card overflow-hidden rounded-xl border border-line bg-panel shadow-md transition-shadow duration-200 group-hover:shadow-xl">
        <Image
          src={card.imageUrl}
          alt={card.name}
          fill
          sizes="(max-width: 640px) 45vw, (max-width: 1024px) 30vw, 200px"
          className="object-cover"
        />
      </div>
      <span className="absolute bottom-2 right-2 rounded-md border border-line bg-bg/90 px-1.5 py-0.5 text-xs font-medium text-ink-muted backdrop-blur-sm">
        ×{card.quantity}
      </span>
    </button>
  );
}
