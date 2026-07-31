// Shared collection filtering/sorting — used by the collection page and the
// deck builder's collection side panel so both behave identically.

import type { LibraryCard } from "./types";

export interface CollectionFilters {
  query: string;
  colors: string[]; // subset of W U B R G C ("C" = colorless)
  type: string; // "" = all types
  sort: "name" | "manaValue" | "color";
}

export const DEFAULT_FILTERS: CollectionFilters = {
  query: "",
  colors: [],
  type: "",
  sort: "name",
};

const WUBRG = ["W", "U", "B", "R", "G"];

function colorSortKey(card: LibraryCard): number {
  if (card.typeLine.includes("Land")) return 7;
  const identity = card.colorIdentity;
  if (identity.length === 0) return 6;
  if (identity.length > 1) return 5;
  return WUBRG.indexOf(identity[0]);
}

export function applyFilters(
  cards: LibraryCard[],
  filters: CollectionFilters,
): LibraryCard[] {
  const q = filters.query.trim().toLowerCase();
  const result = cards.filter((card) => {
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

  return result.sort((a, b) => {
    if (filters.sort === "manaValue") {
      return a.manaValue - b.manaValue || a.name.localeCompare(b.name);
    }
    if (filters.sort === "color") {
      return colorSortKey(a) - colorSortKey(b) || a.name.localeCompare(b.name);
    }
    return a.name.localeCompare(b.name);
  });
}
