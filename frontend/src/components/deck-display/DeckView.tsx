"use client";

import type { Deck } from "@/lib/types";

export function DeckView({ deck }: { deck: Deck }) {
  return (
    <div>
      {/* TODO: commander, deck list, owned vs missing, explanation */}
      {deck.commanderId}
    </div>
  );
}
