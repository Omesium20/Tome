"use client";

import type { CollectionFilters } from "@/lib/filter-cards";

const COLOR_OPTIONS: { symbol: string; label: string; className: string }[] = [
  { symbol: "W", label: "White", className: "bg-mana-w text-stone-800" },
  { symbol: "U", label: "Blue", className: "bg-mana-u text-white" },
  { symbol: "B", label: "Black", className: "bg-mana-b text-stone-900" },
  { symbol: "R", label: "Red", className: "bg-mana-r text-white" },
  { symbol: "G", label: "Green", className: "bg-mana-g text-white" },
  { symbol: "C", label: "Colorless", className: "bg-mana-c text-stone-800" },
];

const TYPE_OPTIONS = [
  "Creature",
  "Instant",
  "Sorcery",
  "Artifact",
  "Enchantment",
  "Planeswalker",
  "Land",
];

const selectClasses =
  "h-10 cursor-pointer rounded-lg border border-line bg-panel px-3 text-sm text-ink transition-colors hover:border-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action";

// The search/color/type/sort controls, shared by the collection page toolbar
// and the deck builder's collection panel. Renders as fragment children of the
// caller's flex container.
export function CollectionFilterControls({
  filters,
  onChange,
  searchWidthClass = "w-full sm:w-64",
}: {
  filters: CollectionFilters;
  onChange: (filters: CollectionFilters) => void;
  searchWidthClass?: string;
}) {
  const toggleColor = (symbol: string) => {
    const colors = filters.colors.includes(symbol)
      ? filters.colors.filter((c) => c !== symbol)
      : [...filters.colors, symbol];
    onChange({ ...filters, colors });
  };

  return (
    <>
      <input
        type="search"
        value={filters.query}
        onChange={(e) => onChange({ ...filters, query: e.target.value })}
        placeholder="Search your collection…"
        aria-label="Search your collection by card name"
        className={`h-10 rounded-lg border border-line bg-panel px-3 text-sm text-ink placeholder:text-ink-muted/70 transition-colors hover:border-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${searchWidthClass}`}
      />

      <div className="flex items-center gap-1.5" role="group" aria-label="Filter by color">
        {COLOR_OPTIONS.map(({ symbol, label, className }) => {
          const active = filters.colors.includes(symbol);
          return (
            <button
              key={symbol}
              type="button"
              onClick={() => toggleColor(symbol)}
              aria-pressed={active}
              aria-label={`Filter by ${label}`}
              title={label}
              className={`flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg border text-xs font-semibold transition-all duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
                active
                  ? "border-ink-muted bg-panel-raised"
                  : "border-line bg-panel opacity-70 hover:opacity-100"
              }`}
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full ${className}`}
                aria-hidden="true"
              >
                {symbol}
              </span>
            </button>
          );
        })}
      </div>

      <select
        value={filters.type}
        onChange={(e) => onChange({ ...filters, type: e.target.value })}
        aria-label="Filter by card type"
        className={selectClasses}
      >
        <option value="">All types</option>
        {TYPE_OPTIONS.map((type) => (
          <option key={type} value={type}>
            {type}
          </option>
        ))}
      </select>

      <select
        value={filters.sort}
        onChange={(e) =>
          onChange({ ...filters, sort: e.target.value as CollectionFilters["sort"] })
        }
        aria-label="Sort collection"
        className={selectClasses}
      >
        <option value="name">Sort: Name</option>
        <option value="manaValue">Sort: Mana value</option>
        <option value="color">Sort: Color</option>
      </select>
    </>
  );
}
