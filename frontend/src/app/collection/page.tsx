"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { LibraryCard } from "@/lib/types";
import { getCollection } from "@/lib/api";
import {
  CollectionGrid,
  CollectionGridSkeleton,
} from "@/components/collection/CollectionGrid";
import {
  CollectionToolbar,
  DEFAULT_FILTERS,
  type CollectionFilters,
} from "@/components/collection/CollectionToolbar";
import { CardPreviewModal } from "@/components/collection/CardPreviewModal";
import { AddCardsDialog } from "@/components/collection/AddCardsDialog";

const WUBRG = ["W", "U", "B", "R", "G"];

function colorSortKey(card: LibraryCard): number {
  if (card.typeLine.includes("Land")) return 7;
  const identity = card.colorIdentity;
  if (identity.length === 0) return 6;
  if (identity.length > 1) return 5;
  return WUBRG.indexOf(identity[0]);
}

function applyFilters(cards: LibraryCard[], filters: CollectionFilters): LibraryCard[] {
  const q = filters.query.trim().toLowerCase();
  let result = cards.filter((card) => {
    if (q && !card.name.toLowerCase().includes(q)) return false;
    if (filters.type && !card.typeLine.includes(filters.type)) return false;
    if (filters.colors.length > 0) {
      const matchesColor = card.colorIdentity.some((c) => filters.colors.includes(c));
      const matchesColorless =
        filters.colors.includes("C") && card.colorIdentity.length === 0;
      if (!matchesColor && !matchesColorless) return false;
    }
    return true;
  });

  result = [...result].sort((a, b) => {
    if (filters.sort === "manaValue") {
      return a.manaValue - b.manaValue || a.name.localeCompare(b.name);
    }
    if (filters.sort === "color") {
      return colorSortKey(a) - colorSortKey(b) || a.name.localeCompare(b.name);
    }
    return a.name.localeCompare(b.name);
  });

  return result;
}

export default function CollectionPage() {
  const [collection, setCollection] = useState<LibraryCard[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<CollectionFilters>(DEFAULT_FILTERS);
  const [previewCard, setPreviewCard] = useState<LibraryCard | null>(null);
  const [addDialogOpen, setAddDialogOpen] = useState(false);

  const loadCollection = useCallback(async () => {
    setLoadError(null);
    setCollection(null);
    try {
      setCollection(await getCollection());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load collection");
    }
  }, []);

  useEffect(() => {
    void loadCollection();
  }, [loadCollection]);

  const visibleCards = useMemo(
    () => (collection ? applyFilters(collection, filters) : []),
    [collection, filters],
  );

  const ownedQuantities = useMemo(() => {
    const map: Record<string, number> = {};
    for (const card of collection ?? []) map[card.id] = card.quantity;
    return map;
  }, [collection]);

  const totalCards = useMemo(
    () => (collection ?? []).reduce((sum, card) => sum + card.quantity, 0),
    [collection],
  );

  const handleAdded = useCallback((updated: LibraryCard) => {
    setCollection((prev) => {
      if (!prev) return prev;
      const existing = prev.findIndex((c) => c.id === updated.id);
      if (existing >= 0) {
        const next = [...prev];
        next[existing] = updated;
        return next;
      }
      return [...prev, updated];
    });
  }, []);

  const hasActiveFilters =
    filters.query.trim() !== "" || filters.colors.length > 0 || filters.type !== "";

  return (
    <main className="mx-auto max-w-screen-2xl px-4 pb-16 sm:px-6">
      <div className="flex items-baseline justify-between pb-2 pt-6">
        <h1 className="text-2xl font-semibold tracking-tight">Collection</h1>
      </div>

      <CollectionToolbar
        filters={filters}
        onChange={setFilters}
        totalCards={totalCards}
        onAddCards={() => setAddDialogOpen(true)}
      />

      <div className="pt-6">
        {loadError && (
          <div className="mx-auto max-w-md rounded-xl border border-line bg-panel p-8 text-center">
            <p className="text-sm text-ink-muted">
              Could not load your collection. {loadError}
            </p>
            <button
              type="button"
              onClick={() => void loadCollection()}
              className="mt-4 cursor-pointer rounded-lg bg-action px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-action/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              Retry
            </button>
          </div>
        )}

        {!loadError && collection === null && <CollectionGridSkeleton />}

        {collection !== null && collection.length === 0 && (
          <div className="mx-auto max-w-md rounded-xl border border-line bg-panel p-8 text-center">
            <h2 className="text-lg font-medium">Your collection is empty</h2>
            <p className="mt-2 text-sm text-ink-muted">
              Add cards to start building your library. CSV import (ManaBox export)
              is coming soon.
            </p>
            <button
              type="button"
              onClick={() => setAddDialogOpen(true)}
              className="mt-6 cursor-pointer rounded-lg bg-action px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-action/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              Add cards
            </button>
          </div>
        )}

        {collection !== null && collection.length > 0 && visibleCards.length === 0 && (
          <div className="mx-auto max-w-md rounded-xl border border-line bg-panel p-8 text-center">
            <p className="text-sm text-ink-muted">No cards match your filters.</p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={() => setFilters(DEFAULT_FILTERS)}
                className="mt-4 cursor-pointer rounded-lg border border-line bg-panel-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
              >
                Clear filters
              </button>
            )}
          </div>
        )}

        {visibleCards.length > 0 && (
          <CollectionGrid cards={visibleCards} onSelect={setPreviewCard} />
        )}
      </div>

      <CardPreviewModal card={previewCard} onClose={() => setPreviewCard(null)} />
      <AddCardsDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        ownedQuantities={ownedQuantities}
        onAdded={handleAdded}
      />
    </main>
  );
}
