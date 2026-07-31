"use client";

import type { CollectionFilters } from "@/lib/filter-cards";
import { CollectionFilterControls } from "./CollectionFilterControls";

export function CollectionToolbar({
  filters,
  onChange,
  totalCards,
  onAddCards,
}: {
  filters: CollectionFilters;
  onChange: (filters: CollectionFilters) => void;
  totalCards: number;
  onAddCards: () => void;
}) {
  return (
    <div className="sticky top-0 z-30 -mx-4 border-b border-line bg-bg/95 px-4 py-3 backdrop-blur-sm sm:-mx-6 sm:px-6">
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-center gap-3">
        <CollectionFilterControls filters={filters} onChange={onChange} />

        <button
          type="button"
          onClick={onAddCards}
          className="h-10 cursor-pointer rounded-lg bg-action px-4 text-sm font-medium text-white transition-colors duration-150 hover:bg-action/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        >
          Add cards
        </button>
      </div>

      <div className="mx-auto mt-2 flex max-w-screen-2xl justify-center">
        <span className="text-sm text-ink-muted" aria-live="polite">
          {totalCards} {totalCards === 1 ? "card" : "cards"}
        </span>
      </div>
    </div>
  );
}
