"use client";

import Image from "next/image";
import { useEffect, useRef } from "react";
import type { LibraryCard } from "@/lib/types";
import { ManaPips } from "@/components/ui/ManaPips";

export function CardPreviewModal({
  card,
  onClose,
}: {
  card: LibraryCard | null;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (card && !dialog.open) dialog.showModal();
    if (!card && dialog.open) dialog.close();
  }, [card]);

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onClick={(e) => {
        // Click on the backdrop (the dialog element itself) closes.
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="w-full max-w-3xl rounded-2xl border border-line bg-panel p-0 text-ink shadow-2xl backdrop:bg-transparent"
    >
      {card && (
        <div className="flex flex-col gap-6 p-6 sm:flex-row">
          <div className="relative mx-auto aspect-card w-64 shrink-0 overflow-hidden rounded-xl border border-line sm:w-72">
            <Image
              src={card.imageUrl}
              alt={card.name}
              fill
              sizes="288px"
              className="object-cover"
            />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-4">
              <h2 className="text-xl font-semibold">{card.name}</h2>
              <ManaPips manaCost={card.manaCost} className="mt-1.5 shrink-0" />
            </div>
            <p className="mt-1 text-sm text-ink-muted">{card.typeLine}</p>
            {card.oracleText && (
              <p className="mt-4 whitespace-pre-line text-sm leading-relaxed text-ink">
                {card.oracleText}
              </p>
            )}
            <p className="mt-6 text-sm text-ink-muted">
              In collection: <span className="font-medium text-ink">×{card.quantity}</span>
            </p>
            <button
              type="button"
              onClick={() => dialogRef.current?.close()}
              className="mt-6 cursor-pointer rounded-lg border border-line bg-panel-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
