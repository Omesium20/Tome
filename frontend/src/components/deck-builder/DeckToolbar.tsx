"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { Card, GenerationPhase } from "@/lib/types";
import { searchCards } from "@/lib/api";
import { ManaPips } from "@/components/ui/ManaPips";

const PHASE_LABELS: Record<GenerationPhase, string> = {
  analyzing: "Analyzing strategy…",
  retrieving: "Retrieving synergies…",
  constructing: "Constructing deck…",
  validating: "Validating rules…",
};

function isBasicLand(card: Card): boolean {
  return card.typeLine.includes("Basic Land");
}

export function DeckToolbar({
  deckCount,
  deckCardIds,
  ownedQuantities,
  aiEnabled,
  onToggleAi,
  generationPhase,
  onGenerate,
  onAdd,
  onClear,
  panelOpen,
  onTogglePanel,
}: {
  deckCount: number;
  deckCardIds: Set<string>;
  ownedQuantities: Record<string, number>;
  aiEnabled: boolean;
  onToggleAi: () => void;
  generationPhase: GenerationPhase | null;
  onGenerate: () => void;
  onAdd: (card: Card) => void;
  onClear: () => void;
  panelOpen: boolean;
  onTogglePanel: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Card[]>([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  const generating = generationPhase !== null;

  // Debounced search for the add-to-deck dropdown.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const found = await searchCards(q);
        if (!cancelled) {
          setResults(found);
          setOpen(true);
        }
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  // Close the dropdown on outside click.
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  const countClass =
    deckCount === 100
      ? "text-success"
      : deckCount > 100
        ? "text-warn"
        : "text-ink-muted";

  return (
    <div className="sticky top-0 z-30 -mx-4 border-b border-line bg-bg/95 px-4 py-3 backdrop-blur-sm sm:-mx-6 sm:px-6">
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-center gap-3">
        <div ref={searchRef} className="relative w-full sm:w-72">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => results.length > 0 && setOpen(true)}
            onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
            placeholder="Add cards to deck…"
            aria-label="Search cards to add to the deck"
            disabled={generating}
            className="h-10 w-full rounded-lg border border-line bg-panel px-3 text-sm text-ink placeholder:text-ink-muted/70 transition-colors hover:border-ink-muted disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          />
          {open && (searching || results.length > 0 || query.trim()) && (
            <ul className="absolute left-0 top-full z-40 mt-1 max-h-96 w-full overflow-y-auto rounded-xl border border-line bg-panel p-1 shadow-2xl sm:w-96">
              {searching && (
                <li className="px-3 py-2 text-sm text-ink-muted">Searching…</li>
              )}
              {!searching && results.length === 0 && query.trim() && (
                <li className="px-3 py-2 text-sm text-ink-muted">
                  No cards found for “{query.trim()}”.
                </li>
              )}
              {!searching &&
                results.map((card) => {
                  const inDeck = deckCardIds.has(card.id);
                  const addable = !inDeck || isBasicLand(card);
                  const owned = ownedQuantities[card.id] ?? 0;
                  return (
                    <li key={card.id}>
                      <button
                        type="button"
                        onClick={() => addable && onAdd(card)}
                        disabled={!addable}
                        className="flex w-full cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-panel-raised disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-action"
                      >
                        <div className="relative aspect-card w-9 shrink-0 overflow-hidden rounded border border-line">
                          <Image
                            src={card.imageUrl}
                            alt=""
                            fill
                            sizes="36px"
                            className="object-cover"
                          />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-medium">
                              {card.name}
                            </span>
                            <ManaPips manaCost={card.manaCost} className="shrink-0" />
                          </div>
                          <p className="truncate text-xs text-ink-muted">
                            {card.typeLine}
                            {owned > 0 && (
                              <span className="ml-2 text-success">Owned ×{owned}</span>
                            )}
                            {inDeck && (
                              <span className="ml-2 text-gold">In deck</span>
                            )}
                          </p>
                        </div>
                      </button>
                    </li>
                  );
                })}
            </ul>
          )}
        </div>

        <button
          type="button"
          onClick={onTogglePanel}
          aria-pressed={panelOpen}
          aria-label="Toggle collection panel"
          title="Show your collection to drag cards into the deck"
          className={`flex h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm font-medium transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
            panelOpen
              ? "border-action bg-action/15 text-ink"
              : "border-line bg-panel text-ink-muted hover:border-ink-muted hover:text-ink"
          }`}
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
            <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" />
            <path d="M10 2.5v11" />
          </svg>
          Collection
        </button>

        <span className={`text-sm font-medium tabular-nums ${countClass}`} aria-live="polite">
          {deckCount} / 100
        </span>

        <div className="ml-auto flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-muted">
            AI generation
            <button
              type="button"
              role="switch"
              aria-checked={aiEnabled}
              aria-label="Toggle AI generation"
              onClick={onToggleAi}
              disabled={generating}
              className={`relative h-6 w-11 cursor-pointer rounded-full border transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
                aiEnabled ? "border-ai bg-ai" : "border-line bg-panel"
              }`}
            >
              <span
                aria-hidden="true"
                className={`absolute top-0.5 h-[18px] w-[18px] rounded-full transition-all duration-200 ${
                  aiEnabled ? "left-6 bg-white" : "left-1 bg-ink-muted"
                }`}
              />
            </button>
          </label>

          {aiEnabled && (
            <button
              type="button"
              onClick={onGenerate}
              disabled={generating}
              className="h-10 cursor-pointer rounded-lg bg-ai px-4 text-sm font-medium text-white transition-colors duration-150 hover:bg-ai/85 disabled:cursor-wait disabled:opacity-70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ai"
            >
              {generating ? PHASE_LABELS[generationPhase] : "Generate deck"}
            </button>
          )}

          <button
            type="button"
            onClick={onClear}
            disabled={generating || deckCount === 0}
            className="h-10 cursor-pointer rounded-lg border border-line bg-panel px-4 text-sm text-ink-muted transition-colors duration-150 hover:border-ink-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}
