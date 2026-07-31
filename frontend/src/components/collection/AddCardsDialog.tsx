"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { Card, LibraryCard } from "@/lib/types";
import { searchCards, addToCollection } from "@/lib/api";
import { ManaPips } from "@/components/ui/ManaPips";

export function AddCardsDialog({
  open,
  onClose,
  ownedQuantities,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  ownedQuantities: Record<string, number>;
  onAdded: (card: LibraryCard) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Card[]>([]);
  const [searching, setSearching] = useState(false);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [justAddedId, setJustAddedId] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Debounced search against the card pool.
  useEffect(() => {
    if (!open) return;
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
        if (!cancelled) setResults(found);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, open]);

  const handleAdd = async (card: Card) => {
    const qty = quantities[card.id] ?? 1;
    setPendingId(card.id);
    try {
      const updated = await addToCollection(card.id, qty);
      onAdded(updated);
      setJustAddedId(card.id);
      setTimeout(() => setJustAddedId((id) => (id === card.id ? null : id)), 1500);
    } finally {
      setPendingId(null);
    }
  };

  const setQty = (cardId: string, qty: number) => {
    setQuantities((prev) => ({ ...prev, [cardId]: Math.min(99, Math.max(1, qty)) }));
  };

  const stepperButton =
    "flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-line bg-panel text-sm text-ink transition-colors hover:border-ink-muted disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action";

  return (
    <dialog
      ref={dialogRef}
      onClose={() => {
        setQuery("");
        setResults([]);
        onClose();
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="w-full max-w-xl rounded-2xl border border-line bg-panel p-0 text-ink shadow-2xl backdrop:bg-transparent"
    >
      <div className="flex max-h-[80vh] flex-col p-6">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold">Add cards</h2>
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            aria-label="Close"
            className="cursor-pointer rounded-md px-2 py-1 text-ink-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            ✕
          </button>
        </div>

        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search cards by name or type…"
          aria-label="Search cards to add"
          autoFocus
          className="mt-4 h-10 w-full rounded-lg border border-line bg-bg px-3 text-sm text-ink placeholder:text-ink-muted/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        />

        <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
          {searching && (
            <p className="py-6 text-center text-sm text-ink-muted">Searching…</p>
          )}
          {!searching && query.trim() && results.length === 0 && (
            <p className="py-6 text-center text-sm text-ink-muted">
              No cards found for “{query.trim()}”.
            </p>
          )}
          {!searching && !query.trim() && (
            <p className="py-6 text-center text-sm text-ink-muted">
              Search the card database to add cards to your collection.
            </p>
          )}
          <ul className="space-y-2">
            {results.map((card) => {
              const owned = ownedQuantities[card.id] ?? 0;
              const qty = quantities[card.id] ?? 1;
              return (
                <li
                  key={card.id}
                  className="flex items-center gap-3 rounded-xl border border-line bg-bg p-2"
                >
                  <div className="relative aspect-card w-12 shrink-0 overflow-hidden rounded-md border border-line">
                    <Image
                      src={card.imageUrl}
                      alt=""
                      fill
                      sizes="48px"
                      className="object-cover"
                    />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{card.name}</span>
                      <ManaPips manaCost={card.manaCost} className="shrink-0" />
                    </div>
                    <p className="truncate text-xs text-ink-muted">
                      {card.typeLine}
                      {owned > 0 && (
                        <span className="ml-2 text-success">Owned ×{owned}</span>
                      )}
                    </p>
                  </div>
                  <div
                    className="flex items-center gap-1"
                    role="group"
                    aria-label={`Quantity of ${card.name} to add`}
                  >
                    <button
                      type="button"
                      onClick={() => setQty(card.id, qty - 1)}
                      disabled={qty <= 1}
                      aria-label="Decrease quantity"
                      className={stepperButton}
                    >
                      −
                    </button>
                    <span className="w-6 text-center text-sm tabular-nums" aria-live="polite">
                      {qty}
                    </span>
                    <button
                      type="button"
                      onClick={() => setQty(card.id, qty + 1)}
                      aria-label="Increase quantity"
                      className={stepperButton}
                    >
                      +
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleAdd(card)}
                    disabled={pendingId === card.id}
                    className={`h-8 w-16 cursor-pointer rounded-md text-sm font-medium transition-colors duration-150 disabled:cursor-wait ${
                      justAddedId === card.id
                        ? "bg-success/20 text-success"
                        : "bg-action text-white hover:bg-action/85"
                    } focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action`}
                  >
                    {justAddedId === card.id ? "Added" : pendingId === card.id ? "…" : "Add"}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </dialog>
  );
}
