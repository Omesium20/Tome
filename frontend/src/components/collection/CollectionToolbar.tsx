"use client";

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
  const toggleColor = (symbol: string) => {
    const colors = filters.colors.includes(symbol)
      ? filters.colors.filter((c) => c !== symbol)
      : [...filters.colors, symbol];
    onChange({ ...filters, colors });
  };

  return (
    <div className="sticky top-14 z-30 -mx-4 border-b border-line bg-bg/95 px-4 py-3 backdrop-blur-sm sm:-mx-6 sm:px-6">
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-center gap-3">
        <input
          type="search"
          value={filters.query}
          onChange={(e) => onChange({ ...filters, query: e.target.value })}
          placeholder="Search your collection…"
          aria-label="Search your collection by card name"
          className="h-10 w-full rounded-lg border border-line bg-panel px-3 text-sm text-ink placeholder:text-ink-muted/70 transition-colors hover:border-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action sm:w-64"
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

        <span className="ml-auto text-sm text-ink-muted" aria-live="polite">
          {totalCards} {totalCards === 1 ? "card" : "cards"}
        </span>

        <button
          type="button"
          onClick={onAddCards}
          className="h-10 cursor-pointer rounded-lg bg-action px-4 text-sm font-medium text-white transition-colors duration-150 hover:bg-action/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        >
          Add cards
        </button>
      </div>
    </div>
  );
}
