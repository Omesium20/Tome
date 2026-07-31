// Mock card pool generated from Scryfall bulk card data (real cards, real art URLs).
// Stands in for the backend's Card table until the Knowledge Pipeline is wired up.

import type { Card } from "@/lib/types";

export const CARD_POOL: Card[] = [
  {
    "id": "99cfd1a8-49fd-4cc0-96f2-f9f159ce5d55",
    "oracleId": "a1f3da21-af6d-450e-bf0b-985d158418e6",
    "name": "Hardened Scales",
    "manaCost": "{G}",
    "manaValue": 1,
    "oracleText": "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/9/99cfd1a8-49fd-4cc0-96f2-f9f159ce5d55.jpg?1783903765"
  },
  {
    "id": "9341ed06-53db-4604-b60a-3ea9129afbc2",
    "oracleId": "1946ded1-5f53-409f-b0a6-5433bb0357d2",
    "name": "The Ozolith",
    "manaCost": "{1}",
    "manaValue": 1,
    "oracleText": "Whenever a creature you control leaves the battlefield, if it had counters on it, put those counters on The Ozolith.\nAt the beginning of combat on your turn, if The Ozolith has counters on it, you may move all counters from The Ozolith onto target creature.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Legendary Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/3/9341ed06-53db-4604-b60a-3ea9129afbc2.jpg?1783931006"
  },
  {
    "id": "1d5bab94-362e-45e6-8988-69e2b2c681b1",
    "oracleId": "41fed659-237c-4d8e-ad31-d17fa0d3f764",
    "name": "Ozolith, the Shattered Spire",
    "manaCost": "{1}{G}",
    "manaValue": 2,
    "oracleText": "If one or more +1/+1 counters would be put on an artifact or creature you control, that many plus one +1/+1 counters are put on it instead.\n{1}{G}, {T}: Put a +1/+1 counter on target artifact or creature you control. Activate only as a sorcery.\nCycling {2} ({2}, Discard this card: Draw a card.)",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Legendary Artifact",
    "keywords": [
      "Cycling"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/1/d/1d5bab94-362e-45e6-8988-69e2b2c681b1.jpg?1783903762"
  },
  {
    "id": "c6d36786-6e36-4a9b-97ad-ad7d9d2b8d92",
    "oracleId": "a2fe5937-212c-4e71-8d6e-f408b38100aa",
    "name": "Conclave Mentor",
    "manaCost": "{G}{W}",
    "manaValue": 2,
    "oracleText": "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on that creature instead.\nWhen this creature dies, you gain life equal to its power.",
    "colors": [
      "G",
      "W"
    ],
    "colorIdentity": [
      "G",
      "W"
    ],
    "typeLine": "Creature — Centaur Cleric",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/6/c6d36786-6e36-4a9b-97ad-ad7d9d2b8d92.jpg?1783917132"
  },
  {
    "id": "107c8aa8-c8f8-4cbf-821b-bd2cb33354f0",
    "oracleId": "c9404d7d-a026-4082-9fcb-1ab571a136b5",
    "name": "Winding Constrictor",
    "manaCost": "{B}{G}",
    "manaValue": 2,
    "oracleText": "If one or more counters would be put on an artifact or creature you control, that many plus one of each of those kinds of counters are put on that permanent instead.\nIf you would get one or more counters, you get that many plus one of each of those kinds of counters instead.",
    "colors": [
      "B",
      "G"
    ],
    "colorIdentity": [
      "B",
      "G"
    ],
    "typeLine": "Creature — Snake",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/1/0/107c8aa8-c8f8-4cbf-821b-bd2cb33354f0.jpg?1783936732"
  },
  {
    "id": "c0f1a736-8b12-4f58-a031-bf1733f65c51",
    "oracleId": "28fe909b-06e0-424c-9f75-c824a25f5865",
    "name": "Branching Evolution",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "If one or more +1/+1 counters would be put on a creature you control, twice that many +1/+1 counters are put on that creature instead.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/0/c0f1a736-8b12-4f58-a031-bf1733f65c51.jpg?1783911212"
  },
  {
    "id": "f2c4f80e-84a0-463b-82c3-5c6503809351",
    "oracleId": "01546b7d-a233-4176-8843-d732074dc5b6",
    "name": "Doubling Season",
    "manaCost": "{4}{G}",
    "manaValue": 5,
    "oracleText": "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\nIf an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/f/2/f2c4f80e-84a0-463b-82c3-5c6503809351.jpg?1783909062"
  },
  {
    "id": "1d58d08d-cd62-416d-8d8e-7d9c56d5c4da",
    "oracleId": "b45fdeab-00cc-4422-af9f-66f30a880a7c",
    "name": "Evolution Sage",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "Landfall — Whenever a land you control enters, proliferate. (Choose any number of permanents and/or players, then give each another counter of each kind already there.)",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Elf Druid",
    "keywords": [
      "Proliferate",
      "Landfall"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/1/d/1d58d08d-cd62-416d-8d8e-7d9c56d5c4da.jpg?1783904546"
  },
  {
    "id": "ef1e1dff-b559-441d-8df3-b6a418066aca",
    "oracleId": "d69b1e68-8d8e-460b-9eb4-6a68be886197",
    "name": "Kodama of the West Tree",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "Reach\nModified creatures you control have trample. (Equipment, Auras you control, and counters are modifications.)\nWhenever a modified creature you control deals combat damage to a player, search your library for a basic land card, put it onto the battlefield tapped, then shuffle.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Legendary Creature — Spirit",
    "keywords": [
      "Reach"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/e/f/ef1e1dff-b559-441d-8df3-b6a418066aca.jpg?1783923845"
  },
  {
    "id": "34bdc973-db45-46a6-ac48-ce88fb59920a",
    "oracleId": "af42079b-a3c0-448c-9bb2-b915252e87a9",
    "name": "Animation Module",
    "manaCost": "{1}",
    "manaValue": 1,
    "oracleText": "Whenever one or more +1/+1 counters are put on a permanent you control, you may pay {1}. If you do, create a 1/1 colorless Servo artifact creature token.\n{3}, {T}: Choose a counter on target permanent or player. Give that permanent or player another counter of that kind.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/3/4/34bdc973-db45-46a6-ac48-ce88fb59920a.jpg?1783937163"
  },
  {
    "id": "6340e0f3-7f9c-4d71-8daf-e1be5505eb5b",
    "oracleId": "78427103-9543-41fb-b6d4-72963fe87275",
    "name": "The Great Henge",
    "manaCost": "{7}{G}{G}",
    "manaValue": 9,
    "oracleText": "This spell costs {X} less to cast, where X is the greatest power among creatures you control.\n{T}: Add {G}{G}. You gain 2 life.\nWhenever a nontoken creature you control enters, put a +1/+1 counter on it and draw a card.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Legendary Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/6/3/6340e0f3-7f9c-4d71-8daf-e1be5505eb5b.jpg?1783915629"
  },
  {
    "id": "fec1ffcd-84d3-44b3-b1d5-b33a4fee348a",
    "oracleId": "57c1a98e-c4fb-4144-9e3e-d0fe6789ba26",
    "name": "Simic Ascendancy",
    "manaCost": "{G}{U}",
    "manaValue": 2,
    "oracleText": "{1}{G}{U}: Put a +1/+1 counter on target creature you control.\nWhenever one or more +1/+1 counters are put on a creature you control, put that many growth counters on this enchantment.\nAt the beginning of your upkeep, if this enchantment has twenty or more growth counters on it, you win the game.",
    "colors": [
      "G",
      "U"
    ],
    "colorIdentity": [
      "G",
      "U"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/f/e/fec1ffcd-84d3-44b3-b1d5-b33a4fee348a.jpg?1783913844"
  },
  {
    "id": "22be6c55-f95e-498e-b6d2-65b7ed57d2d3",
    "oracleId": "93d0e129-e3b5-4aff-9e50-f34771ed00ff",
    "name": "Fathom Mage",
    "manaCost": "{2}{G}{U}",
    "manaValue": 4,
    "oracleText": "Evolve (Whenever a creature you control enters, if that creature has greater power or toughness than this creature, put a +1/+1 counter on this creature.)\nWhenever a +1/+1 counter is put on this creature, you may draw a card.",
    "colors": [
      "G",
      "U"
    ],
    "colorIdentity": [
      "G",
      "U"
    ],
    "typeLine": "Creature — Human Wizard",
    "keywords": [
      "Evolve"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/2/2/22be6c55-f95e-498e-b6d2-65b7ed57d2d3.jpg?1783923228"
  },
  {
    "id": "46eff31d-f460-48f2-aab7-8b9b89cd87fe",
    "oracleId": "c549b0fd-1e08-4873-952e-a14dc45a0fd2",
    "name": "Champion of Lambholt",
    "manaCost": "{1}{G}{G}",
    "manaValue": 3,
    "oracleText": "Creatures with power less than this creature's power can't block creatures you control.\nWhenever another creature you control enters, put a +1/+1 counter on this creature.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Human Warrior",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/6/46eff31d-f460-48f2-aab7-8b9b89cd87fe.jpg?1783917143"
  },
  {
    "id": "c1e9cef5-c55f-47d9-9d2f-300dab8fcb0b",
    "oracleId": "03873314-6e64-43ab-95c0-3d8692a57a03",
    "name": "Herd Baloth",
    "manaCost": "{3}{G}{G}",
    "manaValue": 5,
    "oracleText": "Whenever one or more +1/+1 counters are put on this creature, you may create a 4/4 green Beast creature token.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Beast",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/1/c1e9cef5-c55f-47d9-9d2f-300dab8fcb0b.jpg?1783926829"
  },
  {
    "id": "5296e353-2efc-4d72-a877-7957eff630b9",
    "oracleId": "cc65ac73-5bef-4ecb-ad8e-39199084c027",
    "name": "Cathars' Crusade",
    "manaCost": "{3}{W}{W}",
    "manaValue": 5,
    "oracleText": "Whenever a creature you control enters, put a +1/+1 counter on each creature you control.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/5/2/5296e353-2efc-4d72-a877-7957eff630b9.jpg?1783908185"
  },
  {
    "id": "74b1eae0-1bf8-4922-a9e3-45c01ece9005",
    "oracleId": "57d0c688-6206-4c83-9fcb-27fd29e2a9ed",
    "name": "Basri's Lieutenant",
    "manaCost": "{3}{W}",
    "manaValue": 4,
    "oracleText": "Vigilance, protection from multicolored\nWhen this creature enters, put a +1/+1 counter on target creature you control.\nWhenever this creature or another creature you control dies, if it had a +1/+1 counter on it, create a 2/2 white Knight creature token with vigilance.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Creature — Human Knight",
    "keywords": [
      "Vigilance",
      "Protection"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/7/4/74b1eae0-1bf8-4922-a9e3-45c01ece9005.jpg?1783930745"
  },
  {
    "id": "89e3cc09-5057-4c05-88fc-d6cda809fc74",
    "oracleId": "16629f59-bae8-4c19-bf50-443eb0ed6856",
    "name": "Felidar Retreat",
    "manaCost": "{3}{W}",
    "manaValue": 4,
    "oracleText": "Landfall — Whenever a land you control enters, choose one —\n• Create a 2/2 white Cat Beast creature token.\n• Put a +1/+1 counter on each creature you control. Those creatures gain vigilance until end of turn.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Enchantment",
    "keywords": [
      "Landfall"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/8/9/89e3cc09-5057-4c05-88fc-d6cda809fc74.jpg?1783908940"
  },
  {
    "id": "dcd27fa3-f6b6-4137-9b6c-4cba7187664c",
    "oracleId": "cb9994b9-924b-4e10-9075-9cfbec88f2bf",
    "name": "Luminarch Aspirant",
    "manaCost": "{1}{W}",
    "manaValue": 2,
    "oracleText": "At the beginning of combat on your turn, put a +1/+1 counter on target creature you control.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Creature — Human Cleric",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/d/c/dcd27fa3-f6b6-4137-9b6c-4cba7187664c.jpg?1783923289"
  },
  {
    "id": "53876046-cdb8-4157-96cd-7832d3ad2549",
    "oracleId": "a0c47ab6-dfb4-46ee-a3f7-9e1521b4bb4b",
    "name": "Shalai, Voice of Plenty",
    "manaCost": "{3}{W}",
    "manaValue": 4,
    "oracleText": "Flying\nYou, planeswalkers you control, and other creatures you control have hexproof.\n{4}{G}{G}: Put a +1/+1 counter on each creature you control.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "G",
      "W"
    ],
    "typeLine": "Legendary Creature — Angel",
    "keywords": [
      "Flying"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/5/3/53876046-cdb8-4157-96cd-7832d3ad2549.jpg?1783907119"
  },
  {
    "id": "f7e2b0e1-c9ea-4f91-b19e-73b1bd6a0884",
    "oracleId": "8b972819-507a-40a9-ab1f-1a674ea56083",
    "name": "Abzan Falconer",
    "manaCost": "{2}{W}",
    "manaValue": 3,
    "oracleText": "Outlast {W} ({W}, {T}: Put a +1/+1 counter on this creature. Outlast only as a sorcery.)\nEach creature you control with a +1/+1 counter on it has flying.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Creature — Human Soldier",
    "keywords": [
      "Outlast"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/f/7/f7e2b0e1-c9ea-4f91-b19e-73b1bd6a0884.jpg?1783917196"
  },
  {
    "id": "fb885d30-c6e5-494a-bc01-3d5085b8e262",
    "oracleId": "82f3faa8-39fa-450b-843f-d60a4c36d8f7",
    "name": "Mikaeus, the Lunarch",
    "manaCost": "{X}{W}",
    "manaValue": 1,
    "oracleText": "Mikaeus enters with X +1/+1 counters on it.\n{T}: Put a +1/+1 counter on Mikaeus.\n{T}, Remove a +1/+1 counter from Mikaeus: Put a +1/+1 counter on each other creature you control.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Legendary Creature — Human Cleric",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/f/b/fb885d30-c6e5-494a-bc01-3d5085b8e262.jpg?1783917181"
  },
  {
    "id": "5272436e-74f0-44c4-a291-ea8ebc3f1525",
    "oracleId": "4b515bb0-f275-4400-8032-3173b799ab40",
    "name": "Walking Ballista",
    "manaCost": "{X}{X}",
    "manaValue": 0,
    "oracleText": "This creature enters with X +1/+1 counters on it.\n{4}: Put a +1/+1 counter on this creature.\nRemove a +1/+1 counter from this creature: It deals 1 damage to any target.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact Creature — Construct",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/5/2/5272436e-74f0-44c4-a291-ea8ebc3f1525.jpg?1783930088"
  },
  {
    "id": "cc387ccf-f746-4855-81e5-64f5a5e0fdda",
    "oracleId": "dde55256-5259-44e7-a267-fca45a7f0d04",
    "name": "Hangarback Walker",
    "manaCost": "{X}{X}",
    "manaValue": 0,
    "oracleText": "This creature enters with X +1/+1 counters on it.\nWhen this creature dies, create a 1/1 colorless Thopter artifact creature token with flying for each +1/+1 counter on this creature.\n{1}, {T}: Put a +1/+1 counter on this creature.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact Creature — Construct",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/c/cc387ccf-f746-4855-81e5-64f5a5e0fdda.jpg?1783903740"
  },
  {
    "id": "8c504c23-1e9a-411b-9cfe-4180d0c744f6",
    "oracleId": "1ff25f67-36a7-4cfa-a2b1-2135b5b6fb67",
    "name": "Scavenging Ooze",
    "manaCost": "{1}{G}",
    "manaValue": 2,
    "oracleText": "{G}: Exile target card from a graveyard. If it was a creature card, put a +1/+1 counter on this creature and you gain 1 life.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Ooze",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/8/c/8c504c23-1e9a-411b-9cfe-4180d0c744f6.jpg?1783909056"
  },
  {
    "id": "92613468-205e-488b-930d-11908477e9f8",
    "oracleId": "5a3fdf5a-bff8-4896-b288-3f43f9a72d9b",
    "name": "Vorinclex, Monstrous Raider",
    "manaCost": "{4}{G}{G}",
    "manaValue": 6,
    "oracleText": "Trample, haste\nIf you would put one or more counters on a permanent or player, put twice that many of each of those kinds of counters on that permanent or player instead.\nIf an opponent would put one or more counters on a permanent or player, they put half that many of each of those kinds of counters on that permanent or player instead, rounded down.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Legendary Creature — Phyrexian Praetor",
    "keywords": [
      "Haste",
      "Trample"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/2/92613468-205e-488b-930d-11908477e9f8.jpg?1783928202"
  },
  {
    "id": "d0d33d52-3d28-4635-b985-51e126289259",
    "oracleId": "7e6b9b59-cd68-4e3c-827b-38833c92d6eb",
    "name": "Atraxa, Praetors' Voice",
    "manaCost": "{G}{W}{U}{B}",
    "manaValue": 4,
    "oracleText": "Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate. (Choose any number of permanents and/or players, then give each another counter of each kind already there.)",
    "colors": [
      "B",
      "G",
      "U",
      "W"
    ],
    "colorIdentity": [
      "B",
      "G",
      "U",
      "W"
    ],
    "typeLine": "Legendary Creature — Phyrexian Angel Horror",
    "keywords": [
      "Deathtouch",
      "Flying",
      "Lifelink",
      "Vigilance",
      "Proliferate"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/d/0/d0d33d52-3d28-4635-b985-51e126289259.jpg?1783930136"
  },
  {
    "id": "c2be0e99-cf43-423f-974f-02e3313b3aa9",
    "oracleId": "e579a72f-4933-40fe-9e57-96f8d65370bc",
    "name": "Ghave, Guru of Spores",
    "manaCost": "{2}{W}{B}{G}",
    "manaValue": 5,
    "oracleText": "Ghave enters with five +1/+1 counters on it.\n{1}, Remove a +1/+1 counter from a creature you control: Create a 1/1 green Saproling creature token.\n{1}, Sacrifice a creature: Put a +1/+1 counter on target creature.",
    "colors": [
      "B",
      "G",
      "W"
    ],
    "colorIdentity": [
      "B",
      "G",
      "W"
    ],
    "typeLine": "Legendary Creature — Fungus Shaman",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/2/c2be0e99-cf43-423f-974f-02e3313b3aa9.jpg?1783921836"
  },
  {
    "id": "ae25db8c-3d10-4196-b002-9d2aabd5f4de",
    "oracleId": "b9c1ca24-5696-4777-8465-13a8f78e4fb5",
    "name": "Falco Spara, Pactweaver",
    "manaCost": "{1}{G}{W}{U}",
    "manaValue": 4,
    "oracleText": "Flying, trample\nFalco Spara enters with a shield counter on it.\nYou may look at the top card of your library any time.\nYou may cast spells from the top of your library by removing a counter from a creature you control in addition to paying their other costs.",
    "colors": [
      "G",
      "U",
      "W"
    ],
    "colorIdentity": [
      "G",
      "U",
      "W"
    ],
    "typeLine": "Legendary Creature — Bird Demon",
    "keywords": [
      "Flying",
      "Trample"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/a/e/ae25db8c-3d10-4196-b002-9d2aabd5f4de.jpg?1783923087"
  },
  {
    "id": "b4e9c870-23c0-413a-ae39-265f09da16d1",
    "oracleId": "b1544f21-7e98-461b-aed5-e748b0168c52",
    "name": "Swords to Plowshares",
    "manaCost": "{W}",
    "manaValue": 1,
    "oracleText": "Exile target creature. Its controller gains life equal to its power.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/b/4/b4e9c870-23c0-413a-ae39-265f09da16d1.jpg?1783903243"
  },
  {
    "id": "95ca89ea-1200-4bb4-ae4b-af35d3ccd35b",
    "oracleId": "d683d985-9888-4d21-8b5f-69e69ce4a03b",
    "name": "Path to Exile",
    "manaCost": "{W}",
    "manaValue": 1,
    "oracleText": "Exile target creature. Its controller may search their library for a basic land card, put that card onto the battlefield tapped, then shuffle.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/5/95ca89ea-1200-4bb4-ae4b-af35d3ccd35b.jpg?1783903242"
  },
  {
    "id": "861b5889-0183-4bee-afeb-a4b2aa700a8e",
    "oracleId": "153376c9-dffd-458c-8ce3-a4c8269bc4e9",
    "name": "Smothering Tithe",
    "manaCost": "{3}{W}",
    "manaValue": 4,
    "oracleText": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token. (It's an artifact with \"{T}, Sacrifice this token: Add one mana of any color.\")",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Enchantment",
    "keywords": [
      "Treasure"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/8/6/861b5889-0183-4bee-afeb-a4b2aa700a8e.jpg?1783915712"
  },
  {
    "id": "f3537373-ef54-4578-9d05-6216420ee349",
    "oracleId": "5def9f38-0a0b-4e8d-9f9d-29dcb46520b4",
    "name": "Esper Sentinel",
    "manaCost": "{W}",
    "manaValue": 1,
    "oracleText": "Whenever an opponent casts their first noncreature spell each turn, draw a card unless that player pays {X}, where X is this creature's power.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Artifact Creature — Human Soldier",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/f/3/f3537373-ef54-4578-9d05-6216420ee349.jpg?1783926893"
  },
  {
    "id": "483fa1cb-1e35-44f2-a143-98c0f107f5ca",
    "oracleId": "0d4ecdb1-ec90-497f-a7a4-1c68092b8757",
    "name": "Teferi's Protection",
    "manaCost": "{2}{W}",
    "manaValue": 3,
    "oracleText": "Until your next turn, your life total can't change and you gain protection from everything. All permanents you control phase out. (While they're phased out, they're treated as though they don't exist. They phase in before you untap during your untap step.)\nExile Teferi's Protection.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/8/483fa1cb-1e35-44f2-a143-98c0f107f5ca.jpg?1783921926"
  },
  {
    "id": "c3f9b454-97e9-4c77-b37a-e974221ae385",
    "oracleId": "fae37e28-e137-4177-b973-fa8b4dd8f409",
    "name": "Generous Gift",
    "manaCost": "{2}{W}",
    "manaValue": 3,
    "oracleText": "Destroy target permanent. Its controller creates a 3/3 green Elephant creature token.",
    "colors": [
      "W"
    ],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/3/c3f9b454-97e9-4c77-b37a-e974221ae385.jpg?1783903247"
  },
  {
    "id": "4f616706-ec97-4923-bb1e-11a69fbaa1f8",
    "oracleId": "cc187110-1148-4090-bbb8-e205694a39f5",
    "name": "Counterspell",
    "manaCost": "{U}{U}",
    "manaValue": 2,
    "oracleText": "Counter target spell.",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/f/4f616706-ec97-4923-bb1e-11a69fbaa1f8.jpg?1783909630"
  },
  {
    "id": "9f37c5b6-a59c-45cd-9a99-e9357fe9ea1b",
    "oracleId": "53236dd7-845a-444c-96d5-f41ed7325d8f",
    "name": "Rhystic Study",
    "manaCost": "{2}{U}",
    "manaValue": 3,
    "oracleText": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/f/9f37c5b6-a59c-45cd-9a99-e9357fe9ea1b.jpg?1783919146"
  },
  {
    "id": "dfb7c4b9-f2f4-4d4e-baf2-86551c8150fe",
    "oracleId": "d75b9c82-1b49-4c3e-a1b5-aeef57d6644b",
    "name": "Cyclonic Rift",
    "manaCost": "{1}{U}",
    "manaValue": 2,
    "oracleText": "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U} (You may cast this spell for its overload cost. If you do, change \"target\" in its text to \"each.\")",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Instant",
    "keywords": [
      "Overload"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/d/f/dfb7c4b9-f2f4-4d4e-baf2-86551c8150fe.jpg?1783913339"
  },
  {
    "id": "b5545882-6963-4729-b2c6-fb4bdc75ffcc",
    "oracleId": "36cd2364-d113-47d1-b2c4-b088d9eb88dd",
    "name": "Brainstorm",
    "manaCost": "{U}",
    "manaValue": 1,
    "oracleText": "Draw three cards, then put two cards from your hand on top of your library in any order.",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/b/5/b5545882-6963-4729-b2c6-fb4bdc75ffcc.jpg?1783904806"
  },
  {
    "id": "40140991-cffa-4b52-9a25-37e9a8aa9ddd",
    "oracleId": "8a52f3c0-2552-4425-b2e3-5496eb2232a7",
    "name": "Mystic Remora",
    "manaCost": "{U}",
    "manaValue": 1,
    "oracleText": "Cumulative upkeep {1} (At the beginning of your upkeep, put an age counter on this permanent, then sacrifice it unless you pay its upkeep cost for each age counter on it.)\nWhenever an opponent casts a noncreature spell, you may draw a card unless that player pays {4}.",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Enchantment",
    "keywords": [
      "Cumulative upkeep"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/0/40140991-cffa-4b52-9a25-37e9a8aa9ddd.jpg?1783918491"
  },
  {
    "id": "4131fa63-5afc-4b63-a4b4-f47b4bced87f",
    "oracleId": "05849bd6-8f38-4031-be2b-e2aa03beb8cc",
    "name": "Pongify",
    "manaCost": "{U}",
    "manaValue": 1,
    "oracleText": "Destroy target creature. It can't be regenerated. Its controller creates a 3/3 green Ape creature token.",
    "colors": [
      "U"
    ],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/1/4131fa63-5afc-4b63-a4b4-f47b4bced87f.jpg?1783907100"
  },
  {
    "id": "a24b4cb6-cebb-428b-8654-74347a6a8d63",
    "oracleId": "82004860-e589-4e38-8d61-8c0210e4ea39",
    "name": "Demonic Tutor",
    "manaCost": "{1}{B}",
    "manaValue": 2,
    "oracleText": "Search your library for a card, put that card into your hand, then shuffle.",
    "colors": [
      "B"
    ],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Sorcery",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/a/2/a24b4cb6-cebb-428b-8654-74347a6a8d63.jpg?1783915679"
  },
  {
    "id": "de5afccc-8d42-4bd6-b068-b9ea2361655e",
    "oracleId": "afaef788-34d1-460b-b884-9d7ae6ddeb18",
    "name": "Toxic Deluge",
    "manaCost": "{2}{B}",
    "manaValue": 3,
    "oracleText": "As an additional cost to cast this spell, pay X life.\nAll creatures get -X/-X until end of turn.",
    "colors": [
      "B"
    ],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Sorcery",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/d/e/de5afccc-8d42-4bd6-b068-b9ea2361655e.jpg?1783903236"
  },
  {
    "id": "d67be074-cdd4-41d9-ac89-0a0456c4e4b2",
    "oracleId": "34f34409-326d-4994-a0ea-1a69aa278f03",
    "name": "Sheoldred, the Apocalypse",
    "manaCost": "{2}{B}{B}",
    "manaValue": 4,
    "oracleText": "Deathtouch\nWhenever you draw a card, you gain 2 life.\nWhenever an opponent draws a card, they lose 2 life.",
    "colors": [
      "B"
    ],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Legendary Creature — Phyrexian Praetor",
    "keywords": [
      "Deathtouch"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/d/6/d67be074-cdd4-41d9-ac89-0a0456c4e4b2.jpg?1783921327"
  },
  {
    "id": "0e13f735-54fa-42b6-aea4-ced33811d7d4",
    "oracleId": "0456ec64-2c81-4763-a352-8ff64a4c3d6b",
    "name": "Deadly Rollick",
    "manaCost": "{3}{B}",
    "manaValue": 4,
    "oracleText": "If you control a commander, you may cast this spell without paying its mana cost.\nExile target creature.",
    "colors": [
      "B"
    ],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/0/e/0e13f735-54fa-42b6-aea4-ced33811d7d4.jpg?1783915679"
  },
  {
    "id": "2246c098-1071-4cc3-a60a-802406e2827b",
    "oracleId": "5825997b-10d7-4a36-972c-a80ddd90b8ed",
    "name": "Feed the Swarm",
    "manaCost": "{1}{B}",
    "manaValue": 2,
    "oracleText": "Destroy target creature or enchantment an opponent controls. You lose life equal to that permanent's mana value.",
    "colors": [
      "B"
    ],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Sorcery",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/2/2/2246c098-1071-4cc3-a60a-802406e2827b.jpg?1783904770"
  },
  {
    "id": "7673784e-db4b-43a1-8d55-1bb9fc1e284f",
    "oracleId": "4457ed35-7c10-48c8-9776-456485fdf070",
    "name": "Lightning Bolt",
    "manaCost": "{R}",
    "manaValue": 1,
    "oracleText": "Lightning Bolt deals 3 damage to any target.",
    "colors": [
      "R"
    ],
    "colorIdentity": [
      "R"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/7/6/7673784e-db4b-43a1-8d55-1bb9fc1e284f.jpg?1783903008"
  },
  {
    "id": "57f61970-382e-415c-868b-6bbb6c78825d",
    "oracleId": "07a0cba9-8768-4fd9-a3d5-b0f83b4bf8e8",
    "name": "Chaos Warp",
    "manaCost": "{2}{R}",
    "manaValue": 3,
    "oracleText": "The owner of target permanent shuffles it into their library, then reveals the top card of their library. If it's a permanent card, they put it onto the battlefield.",
    "colors": [
      "R"
    ],
    "colorIdentity": [
      "R"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/5/7/57f61970-382e-415c-868b-6bbb6c78825d.jpg?1783903234"
  },
  {
    "id": "b4b36435-55b3-4615-8812-af41d4fc64d9",
    "oracleId": "ae120613-97d6-4393-b39d-c3e6c076f5d6",
    "name": "Deflecting Swat",
    "manaCost": "{2}{R}",
    "manaValue": 3,
    "oracleText": "If you control a commander, you may cast this spell without paying its mana cost.\nYou may choose new targets for target spell or ability.",
    "colors": [
      "R"
    ],
    "colorIdentity": [
      "R"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/b/4/b4b36435-55b3-4615-8812-af41d4fc64d9.jpg?1783915656"
  },
  {
    "id": "24c0d87b-0049-4beb-b9cb-6f813b7aa7dc",
    "oracleId": "c0957e5e-c71b-439c-931c-9f55d2f76ace",
    "name": "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki",
    "manaCost": "{2}{R}",
    "manaValue": 3,
    "oracleText": "(As this Saga enters and after your draw step, add a lore counter.)\nI — Create a 2/2 red Goblin Shaman creature token with \"Whenever this token attacks, create a Treasure token.\"\nII — You may discard up to two cards. If you do, draw that many cards.\nIII — Exile this Saga, then return it to the battlefield transformed under your control.\n//\n{1}, {T}: Create a token that's a copy of another target nonlegendary creature you control, except it has haste. Sacrifice it at the beginning of the next end step.",
    "colors": [
      "R"
    ],
    "colorIdentity": [
      "R"
    ],
    "typeLine": "Enchantment — Saga // Enchantment Creature — Goblin Shaman",
    "keywords": [
      "Transform",
      "Treasure"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/2/4/24c0d87b-0049-4beb-b9cb-6f813b7aa7dc.jpg?1783923875"
  },
  {
    "id": "e60deb92-f7dd-4f4e-9036-e47dd586f985",
    "oracleId": "8b755881-a72d-4e21-a369-d2924eb4585a",
    "name": "Cultivate",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Sorcery",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/e/6/e60deb92-f7dd-4f4e-9036-e47dd586f985.jpg?1783903229"
  },
  {
    "id": "90c423cc-1264-4067-9c50-e7c88c68ef2d",
    "oracleId": "1593ea18-2f2f-4ab4-83fb-6ccc0bec8a90",
    "name": "Kodama's Reach",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Sorcery — Arcane",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/0/90c423cc-1264-4067-9c50-e7c88c68ef2d.jpg?1783904544"
  },
  {
    "id": "6a0b230b-d391-4998-a3f7-7b158a0ec2cd",
    "oracleId": "68954295-54e3-4303-a6bc-fc4547a4e3a3",
    "name": "Llanowar Elves",
    "manaCost": "{G}",
    "manaValue": 1,
    "oracleText": "{T}: Add {G}.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Elf Druid",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/6/a/6a0b230b-d391-4998-a3f7-7b158a0ec2cd.jpg?1783909057"
  },
  {
    "id": "492c2f9a-51e7-4e0f-9899-23bf43ea988b",
    "oracleId": "d3a0b660-358c-41bd-9cd2-41fbf3491b1a",
    "name": "Birds of Paradise",
    "manaCost": "{G}",
    "manaValue": 1,
    "oracleText": "Flying\n{T}: Add one mana of any color.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Bird",
    "keywords": [
      "Flying"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/9/492c2f9a-51e7-4e0f-9899-23bf43ea988b.jpg?1783903230"
  },
  {
    "id": "400b43aa-c1d2-4435-b863-061f43889422",
    "oracleId": "7735eeba-693b-47e2-bd51-414379cf1016",
    "name": "Beast Within",
    "manaCost": "{2}{G}",
    "manaValue": 3,
    "oracleText": "Destroy target permanent. Its controller creates a 3/3 green Beast creature token.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/0/400b43aa-c1d2-4435-b863-061f43889422.jpg?1783903232"
  },
  {
    "id": "6ada256f-2e55-4c1f-b4d3-d7b10b498956",
    "oracleId": "92eed395-62ca-4293-882b-8565c40daab5",
    "name": "Sylvan Library",
    "manaCost": "{1}{G}",
    "manaValue": 2,
    "oracleText": "At the beginning of your draw step, you may draw two additional cards. If you do, choose two cards in your hand drawn this turn. For each of those cards, pay 4 life or put the card on top of your library.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Enchantment",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/6/a/6ada256f-2e55-4c1f-b4d3-d7b10b498956.jpg?1783918436"
  },
  {
    "id": "e32c67d1-187f-40df-b3b3-6036f5c92834",
    "oracleId": "24882fa2-3fe9-4c1b-aa3d-0e6488b9db27",
    "name": "Heroic Intervention",
    "manaCost": "{1}{G}",
    "manaValue": 2,
    "oracleText": "Permanents you control gain hexproof and indestructible until end of turn.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Instant",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/e/3/e32c67d1-187f-40df-b3b3-6036f5c92834.jpg?1783915629"
  },
  {
    "id": "39704000-65d3-4d39-849e-a3b617376bbc",
    "oracleId": "30b24e8e-3b0e-4d8e-90f3-f66eb7c1858c",
    "name": "Eternal Witness",
    "manaCost": "{1}{G}{G}",
    "manaValue": 3,
    "oracleText": "When this creature enters, you may return target card from your graveyard to your hand.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Human Shaman",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/3/9/39704000-65d3-4d39-849e-a3b617376bbc.jpg?1783915632"
  },
  {
    "id": "276f5cee-a501-4658-bd4d-7a044bf1ccbc",
    "oracleId": "8c52bd39-0586-48ca-b263-17210cf9feb6",
    "name": "Craterhoof Behemoth",
    "manaCost": "{5}{G}{G}{G}",
    "manaValue": 8,
    "oracleText": "Haste\nWhen this creature enters, creatures you control gain trample and get +X/+X until end of turn, where X is the number of creatures you control.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Beast",
    "keywords": [
      "Haste"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/2/7/276f5cee-a501-4658-bd4d-7a044bf1ccbc.jpg?1783907345"
  },
  {
    "id": "c6f1e60f-a195-4590-80b0-86767de6c423",
    "oracleId": "4ba5b3f6-503b-43e6-b66e-4f8c55cffed7",
    "name": "Avenger of Zendikar",
    "manaCost": "{5}{G}{G}",
    "manaValue": 7,
    "oracleText": "When this creature enters, create a 0/1 green Plant creature token for each land you control.\nLandfall — Whenever a land you control enters, you may put a +1/+1 counter on each Plant creature you control.",
    "colors": [
      "G"
    ],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Creature — Elemental",
    "keywords": [
      "Landfall"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/6/c6f1e60f-a195-4590-80b0-86767de6c423.jpg?1783904552"
  },
  {
    "id": "91fdb56b-54d5-4272-8319-505ff987fe9b",
    "oracleId": "6ad8011d-3471-4369-9d68-b264cc027487",
    "name": "Sol Ring",
    "manaCost": "{1}",
    "manaValue": 1,
    "oracleText": "{T}: Add {C}{C}.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/9/1/91fdb56b-54d5-4272-8319-505ff987fe9b.jpg?1783903215"
  },
  {
    "id": "1cad1bd2-7c56-4ce0-99a6-b2a49c1288dd",
    "oracleId": "0bc7f093-bef0-4f1a-852c-4b75ebf54838",
    "name": "Arcane Signet",
    "manaCost": "{2}",
    "manaValue": 2,
    "oracleText": "{T}: Add one mana of any color in your commander's color identity.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/1/c/1cad1bd2-7c56-4ce0-99a6-b2a49c1288dd.jpg?1783903222"
  },
  {
    "id": "b61634ae-05be-4b56-8ebb-9d4ade902e42",
    "oracleId": "ca204b66-8d0c-431a-8d34-282f7c2d17da",
    "name": "Lightning Greaves",
    "manaCost": "{2}",
    "manaValue": 2,
    "oracleText": "Equipped creature has haste and shroud. (It can't be the target of spells or abilities.)\nEquip {0}",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact — Equipment",
    "keywords": [
      "Equip"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/b/6/b61634ae-05be-4b56-8ebb-9d4ade902e42.jpg?1783903217"
  },
  {
    "id": "6675632d-d74a-4b1e-8539-ac678d5545a5",
    "oracleId": "c8b143ad-43ec-4e0d-a440-e348daa31391",
    "name": "Swiftfoot Boots",
    "manaCost": "{2}",
    "manaValue": 2,
    "oracleText": "Equipped creature has hexproof and haste. (It can't be the target of spells or abilities your opponents control. It can attack and {T} no matter when it came under your control.)\nEquip {1} ({1}: Attach to target creature you control. Equip only as a sorcery.)",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact — Equipment",
    "keywords": [
      "Equip"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/6/6/6675632d-d74a-4b1e-8539-ac678d5545a5.jpg?1783903212"
  },
  {
    "id": "1d8b007b-3169-4ee3-80c7-781fc096fc7a",
    "oracleId": "65986c1b-8e51-4604-b685-d82fa7d1263a",
    "name": "Skullclamp",
    "manaCost": "{1}",
    "manaValue": 1,
    "oracleText": "Equipped creature gets +1/-1.\nWhenever equipped creature dies, draw two cards.\nEquip {1}",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact — Equipment",
    "keywords": [
      "Equip"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/1/d/1d8b007b-3169-4ee3-80c7-781fc096fc7a.jpg?1783903215"
  },
  {
    "id": "385b9f5e-81dd-49f6-a644-59d53245e3dc",
    "oracleId": "95560508-7ac9-4be9-8a3f-3c7d5b52807b",
    "name": "Fellwar Stone",
    "manaCost": "{2}",
    "manaValue": 2,
    "oracleText": "{T}: Add one mana of any color that a land an opponent controls could produce.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Artifact",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/3/8/385b9f5e-81dd-49f6-a644-59d53245e3dc.jpg?1783903740"
  },
  {
    "id": "0548fb60-c843-4f8f-a029-6f10efc63a41",
    "oracleId": "0895c9b7-ae7d-4bb3-af17-3b75deb50a25",
    "name": "Command Tower",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}: Add one mana of any color in your commander's color identity.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/0/5/0548fb60-c843-4f8f-a029-6f10efc63a41.jpg?1783903206"
  },
  {
    "id": "836b8f52-10d2-4716-9f7b-38fb23bc68de",
    "oracleId": "b473e293-59e3-4e04-acf2-622604aeb25f",
    "name": "Path of Ancestry",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "This land enters tapped.\n{T}: Add one mana of any color in your commander's color identity. When that mana is spent to cast a creature spell that shares a creature type with your commander, scry 1. (Look at the top card of your library. You may put that card on the bottom.)",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [
      "Scry"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/8/3/836b8f52-10d2-4716-9f7b-38fb23bc68de.jpg?1783903197"
  },
  {
    "id": "8853ff94-bf44-4cfd-9d3a-0743c361fb0d",
    "oracleId": "27b047e3-0d41-45e2-98e9-9391d7923a1e",
    "name": "Exotic Orchard",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}: Add one mana of any color that a land an opponent controls could produce.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/8/8/8853ff94-bf44-4cfd-9d3a-0743c361fb0d.jpg?1783903204"
  },
  {
    "id": "c0318a48-30e4-4ef7-be3d-5e561c5ce428",
    "oracleId": "a75445d3-1303-4bb5-89ad-26ea93fecd48",
    "name": "Evolving Wilds",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}, Sacrifice this land: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/0/c0318a48-30e4-4ef7-be3d-5e561c5ce428.jpg?1783903203"
  },
  {
    "id": "335444f0-0848-4c17-b056-56d4fc876a58",
    "oracleId": "f29dc596-2121-4421-8463-15f6c2e8b9b3",
    "name": "Rogue's Passage",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}: Add {C}.\n{4}, {T}: Target creature can't be blocked this turn.",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/3/3/335444f0-0848-4c17-b056-56d4fc876a58.jpg?1783903721"
  },
  {
    "id": "22017ec2-3552-4865-af76-dba042b141f5",
    "oracleId": "9fb8cd81-403a-4988-8f1c-b8eccf8abd9c",
    "name": "Karn's Bastion",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}: Add {C}.\n{4}, {T}: Proliferate. (Choose any number of permanents and/or players, then give each another counter of each kind already there.)",
    "colors": [],
    "colorIdentity": [],
    "typeLine": "Land",
    "keywords": [
      "Proliferate"
    ],
    "imageUrl": "https://cards.scryfall.io/normal/front/2/2/22017ec2-3552-4865-af76-dba042b141f5.jpg?1783906011"
  },
  {
    "id": "ce46c4e2-a515-41d7-8d70-d20cf4925996",
    "oracleId": "8a44e4e7-dfa2-427b-bbff-11c398fa60bb",
    "name": "Gavony Township",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "{T}: Add {C}.\n{2}{G}{W}, {T}: Put a +1/+1 counter on each creature you control.",
    "colors": [],
    "colorIdentity": [
      "G",
      "W"
    ],
    "typeLine": "Land",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/e/ce46c4e2-a515-41d7-8d70-d20cf4925996.jpg?1783917097"
  },
  {
    "id": "7b7c408b-8660-4db5-9a16-5003c11b4ac1",
    "oracleId": "bc71ebf6-2056-41f7-be35-b2e5c34afa99",
    "name": "Plains",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "({T}: Add {W}.)",
    "colors": [],
    "colorIdentity": [
      "W"
    ],
    "typeLine": "Basic Land — Plains",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/7/b/7b7c408b-8660-4db5-9a16-5003c11b4ac1.jpg?1785323349"
  },
  {
    "id": "c6aa89a8-3584-4906-b9a9-41ef2f021f8e",
    "oracleId": "b2c6aa39-2d2a-459c-a555-fb48ba993373",
    "name": "Island",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "({T}: Add {U}.)",
    "colors": [],
    "colorIdentity": [
      "U"
    ],
    "typeLine": "Basic Land — Island",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/6/c6aa89a8-3584-4906-b9a9-41ef2f021f8e.jpg?1785323353"
  },
  {
    "id": "4031e5e4-e573-4130-8d20-4a606edef0a0",
    "oracleId": "56719f6a-1a6c-4c0a-8d21-18f7d7350b68",
    "name": "Swamp",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "({T}: Add {B}.)",
    "colors": [],
    "colorIdentity": [
      "B"
    ],
    "typeLine": "Basic Land — Swamp",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/4/0/4031e5e4-e573-4130-8d20-4a606edef0a0.jpg?1785323357"
  },
  {
    "id": "c49d378e-9549-4320-b3c6-1aeb216d1e98",
    "oracleId": "a3fb7228-e76b-4e96-a40e-20b5fed75685",
    "name": "Mountain",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "({T}: Add {R}.)",
    "colors": [],
    "colorIdentity": [
      "R"
    ],
    "typeLine": "Basic Land — Mountain",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/4/c49d378e-9549-4320-b3c6-1aeb216d1e98.jpg?1785323363"
  },
  {
    "id": "c3e84b42-5423-4d4d-b8fc-cfbb2c53a4ca",
    "oracleId": "b34bb2dc-c1af-4d77-b0b3-a0fb342a5fc6",
    "name": "Forest",
    "manaCost": "",
    "manaValue": 0,
    "oracleText": "({T}: Add {G}.)",
    "colors": [],
    "colorIdentity": [
      "G"
    ],
    "typeLine": "Basic Land — Forest",
    "keywords": [],
    "imageUrl": "https://cards.scryfall.io/normal/front/c/3/c3e84b42-5423-4d4d-b8fc-cfbb2c53a4ca.jpg?1785323365"
  }
];
