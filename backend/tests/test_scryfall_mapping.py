"""Mapping Scryfall card objects onto Card rows.

The cases that matter are the multi-faced layouts, where Scryfall omits fields
at the card root and the importer has to merge across `card_faces`.
"""

from knowledge_pipeline.scryfall_importer.mapping import (
    FACE_TEXT_SEPARATOR,
    to_card_row,
)

NORMAL_CARD = {
    "object": "card",
    "id": "6658398a-46a5-4f41-9b1b-4a47f2822cf8",
    "oracle_id": "2be2f6c8-d8df-4e5c-a0c1-7566183bbe81",
    "name": "Cultivate",
    "layout": "normal",
    "mana_cost": "{2}{G}",
    "cmc": 3.0,
    "type_line": "Sorcery",
    "oracle_text": "Search your library for up to two basic land cards...",
    "colors": ["G"],
    "color_identity": ["G"],
    "keywords": [],
    "image_uris": {"normal": "https://cards.scryfall.io/normal/front/x.jpg"},
    "legalities": {"commander": "legal", "standard": "not_legal"},
}

# A vanilla creature carries power/toughness at the root, same as any other
# root-only field.
CREATURE_CARD = {
    "id": "eeee",
    "oracle_id": "oracle-hellraiser",
    "name": "Orcish Hellraiser",
    "layout": "normal",
    "mana_cost": "{1}{R}",
    "cmc": 2.0,
    "type_line": "Creature — Orc Warrior",
    "oracle_text": "Echo {R}",
    "power": "3",
    "toughness": "2",
    "colors": ["R"],
    "color_identity": ["R"],
    "keywords": ["Echo"],
    "legalities": {"commander": "legal"},
}

PLANESWALKER_CARD = {
    "id": "ffff",
    "oracle_id": "oracle-walker",
    "name": "Test Planeswalker",
    "layout": "normal",
    "mana_cost": "{3}{U}",
    "cmc": 4.0,
    "type_line": "Legendary Planeswalker — Test",
    "oracle_text": "+1: ...",
    "loyalty": "5",
    "colors": ["U"],
    "color_identity": ["U"],
    "keywords": [],
    "legalities": {"commander": "legal"},
}

# Transform cards put everything except cmc/color_identity on the faces.
TRANSFORM_CARD = {
    "object": "card",
    "id": "aaaa",
    "oracle_id": "oracle-hermit",
    "name": "Hinterland Hermit // Hinterland Scourge",
    "layout": "transform",
    "cmc": 2.0,
    "color_identity": ["R"],
    "keywords": [],
    "legalities": {"commander": "legal"},
    "card_faces": [
        {
            "name": "Hinterland Hermit",
            "mana_cost": "{1}{R}",
            "type_line": "Creature — Human Werewolf",
            "oracle_text": "At the beginning of each upkeep, transform...",
            "power": "1",
            "toughness": "1",
            "colors": ["R"],
            "image_uris": {"normal": "https://cards.scryfall.io/normal/front/h.jpg"},
        },
        {
            "name": "Hinterland Scourge",
            # The back face of a transform card has no mana cost.
            "type_line": "Creature — Werewolf",
            "oracle_text": "Hinterland Scourge must be blocked if able.",
            # Deliberately different from the front face's stats, so the test
            # can tell "front face" apart from "any face"/"last face".
            "power": "3",
            "toughness": "3",
            "colors": ["R"],
            "image_uris": {"normal": "https://cards.scryfall.io/normal/back/h.jpg"},
        },
    ],
}


def test_normal_card_maps_straight_across():
    row = to_card_row(NORMAL_CARD)

    assert row is not None
    assert row.oracle_id == "2be2f6c8-d8df-4e5c-a0c1-7566183bbe81"
    assert row.scryfall_id == "6658398a-46a5-4f41-9b1b-4a47f2822cf8"
    assert row.name == "Cultivate"
    assert row.mana_cost == "{2}{G}"
    assert row.mana_value == 3.0
    assert row.colors == ["G"]
    assert row.layout == "normal"
    assert row.image_url == "https://cards.scryfall.io/normal/front/x.jpg"
    assert row.legalities["commander"] == "legal"


def test_transform_card_keeps_both_faces():
    row = to_card_row(TRANSFORM_CARD)

    assert row is not None
    # Both rules boxes survive, each labelled with its face name — the back
    # face is often the whole reason to play the card.
    assert row.oracle_text is not None
    assert FACE_TEXT_SEPARATOR in row.oracle_text
    assert "Hinterland Hermit" in row.oracle_text
    assert "Hinterland Scourge" in row.oracle_text
    assert "must be blocked if able" in row.oracle_text


def test_transform_card_reads_root_only_fields_from_root():
    row = to_card_row(TRANSFORM_CARD)

    assert row is not None
    # cmc and color_identity are always present at the root, even when every
    # other gameplay field has moved to the faces.
    assert row.mana_value == 2.0
    assert row.color_identity == ["R"]


def test_transform_card_uses_front_face_image_and_mana_cost():
    row = to_card_row(TRANSFORM_CARD)

    assert row is not None
    # Only the front face has a mana cost, so no " // " join happens.
    assert row.mana_cost == "{1}{R}"
    assert row.image_url == "https://cards.scryfall.io/normal/front/h.jpg"


def test_creature_reads_power_and_toughness_from_root():
    row = to_card_row(CREATURE_CARD)

    assert row is not None
    assert row.power == "3"
    assert row.toughness == "2"
    assert row.loyalty is None
    assert row.defense is None


def test_planeswalker_reads_loyalty_from_root():
    row = to_card_row(PLANESWALKER_CARD)

    assert row is not None
    assert row.loyalty == "5"
    assert row.power is None
    assert row.toughness is None


def test_transform_card_uses_front_face_power_and_toughness():
    row = to_card_row(TRANSFORM_CARD)

    assert row is not None
    # The two faces have different stats (1/1 front, 3/3 back) — unlike
    # oracle_text, these aren't merged, and the front face's numbers are
    # the ones that apply while the card hasn't transformed.
    assert row.power == "1"
    assert row.toughness == "1"


def test_split_card_joins_mana_costs():
    split = {
        "id": "bbbb",
        "oracle_id": "oracle-split",
        "name": "Fire // Ice",
        "layout": "split",
        "cmc": 4.0,
        "color_identity": ["R", "U"],
        "keywords": [],
        "legalities": {"commander": "legal"},
        "card_faces": [
            {"name": "Fire", "mana_cost": "{1}{R}", "type_line": "Instant",
             "oracle_text": "Fire deals 2 damage divided as you choose.", "colors": ["R"]},
            {"name": "Ice", "mana_cost": "{1}{U}", "type_line": "Instant",
             "oracle_text": "Tap target permanent. Draw a card.", "colors": ["U"]},
        ],
    }

    row = to_card_row(split)

    assert row is not None
    assert row.mana_cost == "{1}{R} // {1}{U}"
    # colors is absent at the root here, so it's the union across faces.
    assert sorted(row.colors) == ["R", "U"]
    # No face carries an image, and neither does the root.
    assert row.image_url is None


def test_land_without_mana_cost_maps_to_none():
    land = {
        "id": "cccc",
        "oracle_id": "oracle-forest",
        "name": "Forest",
        "layout": "normal",
        "mana_cost": "",
        "cmc": 0.0,
        "type_line": "Basic Land — Forest",
        "oracle_text": "({T}: Add {G}.)",
        "colors": [],
        "color_identity": ["G"],
        "keywords": [],
        "legalities": {"commander": "legal"},
    }

    row = to_card_row(land)

    assert row is not None
    # An empty mana cost is stored as NULL rather than "", so downstream code
    # doesn't have to treat "" and None as separate cases.
    assert row.mana_cost is None
    assert row.mana_value == 0.0


def test_card_without_oracle_id_is_skipped():
    assert to_card_row({"id": "x", "name": "Nameless", "layout": "normal"}) is None


def test_reversible_card_takes_oracle_id_from_faces():
    reversible = {
        "id": "dddd",
        "name": "Propaganda // Propaganda",
        "layout": "reversible_card",
        "cmc": 3.0,
        "color_identity": ["U"],
        "keywords": [],
        "legalities": {"commander": "legal"},
        "card_faces": [
            {"oracle_id": "oracle-propaganda", "name": "Propaganda",
             "mana_cost": "{2}{U}", "type_line": "Enchantment",
             "oracle_text": "Creatures can't attack you unless...", "colors": ["U"]},
            {"oracle_id": "oracle-propaganda", "name": "Propaganda",
             "mana_cost": "{2}{U}", "type_line": "Enchantment",
             "oracle_text": "Creatures can't attack you unless...", "colors": ["U"]},
        ],
    }

    row = to_card_row(reversible)

    assert row is not None
    assert row.oracle_id == "oracle-propaganda"
