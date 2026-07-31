// Mock of the Deck Generation Pipeline (docs/architecture.md): takes the
// user's build-around context, steps through the real pipeline's phases with
// artificial latency, and assembles a plausible ~100-card Commander deck from
// the mock card pool. The real backend owns retrieval and validation; this
// only needs to feel right in the UI.

import type { Card, GeneratedDeckSummary, GenerationPhase } from "@/lib/types";
import { CARD_POOL } from "./cards";
import { getPrimaryRole } from "./metadata";

const PHASES: GenerationPhase[] = [
  "analyzing",
  "retrieving",
  "constructing",
  "validating",
];

const BASIC_BY_COLOR: Record<string, string> = {
  W: "Plains",
  U: "Island",
  B: "Swamp",
  R: "Mountain",
  G: "Forest",
};

const DECK_SIZE_WITHOUT_COMMANDER = 99;
const MAX_SPELLS = 63;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isLand(card: Card): boolean {
  return card.typeLine.includes("Land");
}

function pickCommander(identity: string[]): Card {
  const legends = CARD_POOL.filter(
    (c) => c.typeLine.includes("Legendary") && c.typeLine.includes("Creature"),
  );
  const covering = legends.filter((legend) =>
    identity.every((color) => legend.colorIdentity.includes(color)),
  );
  // Prefer a commander that covers the context colors; among those, the widest
  // identity gives the deck the most candidates. Otherwise best overlap.
  const pool = covering.length > 0 ? covering : legends;
  return [...pool].sort((a, b) => {
    const overlap = (c: Card) =>
      c.colorIdentity.filter((col) => identity.includes(col)).length;
    return (
      overlap(b) - overlap(a) || b.colorIdentity.length - a.colorIdentity.length
    );
  })[0];
}

export async function mockGenerateDeck(
  contextCardNames: string[],
  onPhase?: (phase: GenerationPhase) => void,
): Promise<GeneratedDeckSummary> {
  const context = CARD_POOL.filter((c) => contextCardNames.includes(c.name));

  for (const phase of PHASES) {
    onPhase?.(phase);
    await delay(900);
  }

  const identity = Array.from(new Set(context.flatMap((c) => c.colorIdentity)));
  const commander = pickCommander(identity.length > 0 ? identity : ["G", "W"]);
  const commanderIdentity = commander.colorIdentity;

  const fitsIdentity = (card: Card) =>
    card.colorIdentity.every((color) => commanderIdentity.includes(color));

  // Context cards first so the build-around cards always make the cut.
  const contextIds = new Set(context.map((c) => c.id));
  const candidates = CARD_POOL.filter(
    (c) => !isLand(c) && c.id !== commander.id && fitsIdentity(c),
  ).sort((a, b) => Number(contextIds.has(b.id)) - Number(contextIds.has(a.id)));
  const spells = candidates.slice(0, MAX_SPELLS);

  const landsNeeded = DECK_SIZE_WITHOUT_COMMANDER - spells.length;
  const nonbasicLands = CARD_POOL.filter(
    (c) => isLand(c) && !c.typeLine.includes("Basic") && fitsIdentity(c),
  ).slice(0, Math.max(0, landsNeeded - commanderIdentity.length));

  const cards: { cardId: string; quantity: number }[] = [
    ...spells.map((c) => ({ cardId: c.id, quantity: 1 })),
    ...nonbasicLands.map((c) => ({ cardId: c.id, quantity: 1 })),
  ];

  // Fill the rest with basics split across the commander's colors.
  const basicsNeeded = landsNeeded - nonbasicLands.length;
  const basicColors = commanderIdentity.length > 0 ? commanderIdentity : ["G", "W"];
  basicColors.forEach((color, i) => {
    const perColor = Math.floor(basicsNeeded / basicColors.length);
    const extra = i < basicsNeeded % basicColors.length ? 1 : 0;
    const basic = CARD_POOL.find((c) => c.name === BASIC_BY_COLOR[color]);
    if (basic && perColor + extra > 0) {
      cards.push({ cardId: basic.id, quantity: perColor + extra });
    }
  });

  const roleCount = (role: string) =>
    spells.filter((c) => getPrimaryRole(c) === role).length;
  const contextNames = context.map((c) => c.name);
  const explanation =
    (contextNames.length > 0
      ? `Built around ${contextNames.join(", ")}. `
      : "No build-around cards were provided, so a +1/+1 counters shell was chosen. ") +
    `${commander.name} leads the deck: its color identity (${
      commanderIdentity.join("") || "colorless"
    }) covers the strategy and its abilities reinforce the theme. ` +
    `The list runs ${roleCount("Ramp")} ramp, ${roleCount("Card Draw")} draw, ` +
    `${roleCount("Removal")} removal, and ${roleCount("Protection")} protection pieces ` +
    `alongside the synergy package, with ${landsNeeded} lands to keep the curve consistent.`;

  return { commanderId: commander.id, cards, explanation };
}

export async function mockGetCardsByIds(ids: string[]): Promise<Card[]> {
  await delay(100);
  const wanted = new Set(ids);
  return CARD_POOL.filter((c) => wanted.has(c.id));
}
