// In-browser stand-in for the future collection endpoints
// (GET /api/collection, GET /api/cards/search, POST /api/collection).
// Persists to localStorage so the collection behaves like real backend state,
// and adds a little latency so loading states are exercised for real.

import type { Card, LibraryCard } from "@/lib/types";
import { CARD_POOL } from "./cards";

const STORAGE_KEY = "tome.mock.collection.v1";

// Seeded ownership: most of the counters package plus staples and lands.
// Names not listed here stay unowned so the "add cards" flow has cards to find.
const SEED_QUANTITIES: Record<string, number> = {
  "Hardened Scales": 1,
  "The Ozolith": 1,
  "Ozolith, the Shattered Spire": 1,
  "Conclave Mentor": 2,
  "Winding Constrictor": 1,
  "Evolution Sage": 1,
  "Kodama of the West Tree": 1,
  "Animation Module": 1,
  "Simic Ascendancy": 1,
  "Fathom Mage": 1,
  "Champion of Lambholt": 1,
  "Herd Baloth": 2,
  "Cathars' Crusade": 1,
  "Basri's Lieutenant": 1,
  "Felidar Retreat": 1,
  "Luminarch Aspirant": 2,
  "Abzan Falconer": 3,
  "Mikaeus, the Lunarch": 1,
  "Walking Ballista": 1,
  "Hangarback Walker": 1,
  "Scavenging Ooze": 1,
  "Ghave, Guru of Spores": 1,
  "Swords to Plowshares": 3,
  "Path to Exile": 1,
  "Generous Gift": 2,
  Counterspell: 4,
  Brainstorm: 4,
  Pongify: 1,
  "Feed the Swarm": 1,
  "Lightning Bolt": 4,
  "Chaos Warp": 1,
  Cultivate: 2,
  "Kodama's Reach": 1,
  "Llanowar Elves": 4,
  "Beast Within": 1,
  "Heroic Intervention": 1,
  "Eternal Witness": 1,
  "Avenger of Zendikar": 1,
  "Sol Ring": 2,
  "Arcane Signet": 2,
  "Lightning Greaves": 1,
  "Swiftfoot Boots": 1,
  "Fellwar Stone": 1,
  "Command Tower": 2,
  "Path of Ancestry": 1,
  "Evolving Wilds": 3,
  "Rogue's Passage": 1,
  "Karn's Bastion": 1,
  "Gavony Township": 1,
  Plains: 12,
  Island: 10,
  Swamp: 8,
  Mountain: 9,
  Forest: 14,
};

type StoredCollection = Record<string, number>; // cardId -> quantity

function seedCollection(): StoredCollection {
  const stored: StoredCollection = {};
  for (const card of CARD_POOL) {
    const qty = SEED_QUANTITIES[card.name];
    if (qty) stored[card.id] = qty;
  }
  return stored;
}

function loadStored(): StoredCollection {
  if (typeof window === "undefined") return seedCollection();
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      return JSON.parse(raw) as StoredCollection;
    } catch {
      // fall through to reseed on corrupt data
    }
  }
  const seeded = seedCollection();
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(seeded));
  return seeded;
}

function saveStored(stored: StoredCollection) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function mockGetCollection(): Promise<LibraryCard[]> {
  await delay(600);
  const stored = loadStored();
  return CARD_POOL.filter((card) => stored[card.id]).map((card) => ({
    ...card,
    quantity: stored[card.id],
  }));
}

export async function mockSearchCards(query: string): Promise<Card[]> {
  await delay(300);
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return CARD_POOL.filter(
    (card) =>
      card.name.toLowerCase().includes(q) ||
      card.typeLine.toLowerCase().includes(q),
  ).slice(0, 20);
}

export async function mockAddToCollection(
  cardId: string,
  quantity: number,
): Promise<LibraryCard> {
  await delay(300);
  const card = CARD_POOL.find((c) => c.id === cardId);
  if (!card) throw new Error(`Unknown card id: ${cardId}`);
  const stored = loadStored();
  stored[cardId] = (stored[cardId] ?? 0) + quantity;
  saveStored(stored);
  return { ...card, quantity: stored[cardId] };
}
