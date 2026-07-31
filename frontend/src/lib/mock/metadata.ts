// Mock stand-in for CardMetadata.roles (docs/data-model.md) until the
// Knowledge Pipeline generates real metadata. Primary role first — the deck
// builder groups its columns by primary role. Role names are open-ended;
// anything not in ROLE_ORDER still renders, sorted after the known roles.

import type { Card } from "@/lib/types";

export const ROLE_ORDER = [
  "Counters",
  "Ramp",
  "Card Draw",
  "Removal",
  "Interaction",
  "Protection",
  "Tutor",
  "Recursion",
  "Tokens",
  "Drain",
  "Finisher",
  "Utility",
  "Land",
];

const CARD_ROLES: Record<string, string[]> = {
  "Hardened Scales": ["Counters"],
  "The Ozolith": ["Counters"],
  "Ozolith, the Shattered Spire": ["Counters", "Ramp"],
  "Conclave Mentor": ["Counters"],
  "Winding Constrictor": ["Counters"],
  "Branching Evolution": ["Counters"],
  "Doubling Season": ["Counters", "Tokens"],
  "Evolution Sage": ["Counters"],
  "Kodama of the West Tree": ["Counters", "Ramp"],
  "Animation Module": ["Counters", "Tokens"],
  "Cathars' Crusade": ["Counters"],
  "Luminarch Aspirant": ["Counters"],
  "Abzan Falconer": ["Counters"],
  "Mikaeus, the Lunarch": ["Counters"],
  "Vorinclex, Monstrous Raider": ["Counters"],
  "Atraxa, Praetors' Voice": ["Counters"],
  "Ghave, Guru of Spores": ["Counters", "Tokens"],
  "Falco Spara, Pactweaver": ["Counters", "Card Draw"],
  "Smothering Tithe": ["Ramp"],
  Cultivate: ["Ramp"],
  "Kodama's Reach": ["Ramp"],
  "Llanowar Elves": ["Ramp"],
  "Birds of Paradise": ["Ramp"],
  "Sol Ring": ["Ramp"],
  "Arcane Signet": ["Ramp"],
  "Fellwar Stone": ["Ramp"],
  "The Great Henge": ["Card Draw", "Ramp"],
  "Fathom Mage": ["Card Draw", "Counters"],
  "Esper Sentinel": ["Card Draw"],
  "Rhystic Study": ["Card Draw"],
  Brainstorm: ["Card Draw"],
  "Mystic Remora": ["Card Draw"],
  "Sylvan Library": ["Card Draw"],
  Skullclamp: ["Card Draw"],
  "Swords to Plowshares": ["Removal"],
  "Path to Exile": ["Removal"],
  "Generous Gift": ["Removal"],
  "Cyclonic Rift": ["Removal"],
  Pongify: ["Removal"],
  "Toxic Deluge": ["Removal"],
  "Deadly Rollick": ["Removal"],
  "Feed the Swarm": ["Removal"],
  "Lightning Bolt": ["Removal"],
  "Chaos Warp": ["Removal"],
  "Beast Within": ["Removal"],
  "Walking Ballista": ["Removal", "Counters"],
  Counterspell: ["Interaction"],
  "Teferi's Protection": ["Protection"],
  "Deflecting Swat": ["Protection"],
  "Heroic Intervention": ["Protection"],
  "Shalai, Voice of Plenty": ["Protection"],
  "Lightning Greaves": ["Protection"],
  "Swiftfoot Boots": ["Protection"],
  "Demonic Tutor": ["Tutor"],
  "Eternal Witness": ["Recursion"],
  "Herd Baloth": ["Tokens", "Counters"],
  "Basri's Lieutenant": ["Tokens", "Counters"],
  "Felidar Retreat": ["Tokens", "Counters"],
  "Hangarback Walker": ["Tokens", "Counters"],
  "Avenger of Zendikar": ["Tokens"],
  "Fable of the Mirror-Breaker": ["Tokens"],
  "Sheoldred, the Apocalypse": ["Drain", "Card Draw"],
  "Craterhoof Behemoth": ["Finisher"],
  "Simic Ascendancy": ["Finisher", "Counters"],
  "Champion of Lambholt": ["Finisher", "Counters"],
  "Scavenging Ooze": ["Utility"],
};

export function getRoles(card: Card): string[] {
  const roles = CARD_ROLES[card.name];
  if (roles) return roles;
  if (card.typeLine.includes("Land")) return ["Land"];
  return ["Utility"];
}

export function getPrimaryRole(card: Card): string {
  return getRoles(card)[0];
}

// Known roles in ROLE_ORDER order, unknown roles alphabetical after them,
// Land always last.
export function compareRoles(a: string, b: string): number {
  if (a === b) return 0;
  if (a === "Land") return 1;
  if (b === "Land") return -1;
  const ia = ROLE_ORDER.indexOf(a);
  const ib = ROLE_ORDER.indexOf(b);
  if (ia >= 0 && ib >= 0) return ia - ib;
  if (ia >= 0) return -1;
  if (ib >= 0) return 1;
  return a.localeCompare(b);
}
