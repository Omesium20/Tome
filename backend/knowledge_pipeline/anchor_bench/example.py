"""A small, real, valid candidate pool — the contract as something runnable.

One ladder (`Role.RAMP`), five rungs, with a second candidate on two of them so
the shape of a choice is visible rather than described. Every card here is real
and Commander-legal, so `validate` passes against an imported corpus and fails
informatively against an empty one.

It is deliberately not the shipped ladder verbatim: the 1-2 and 3-4 rungs offer
two candidates, which is what a pool in the middle of a calibration pass looks
like. A real pool proposes three or four per rung — the first pass offered two
and 20 rungs came back with only one, clustered at the extremes, which is
exactly where a ruler mark most needs alternatives.
"""

from __future__ import annotations

from typing import Any

BAND_RUBRIC: dict[str, str] = {
    "1-2": "Effectively unplayable: outclassed by cards at the same cost, or asking more than it "
           "returns.",
    "3-4": "Filler. Does something real but inefficiently; makes a deck only for want of better.",
    "5-6": "Solidly playable. A reasonable inclusion nobody would question.",
    "7-8": "A strong staple. Shapes how the deck plays and is a first pick in its role.",
    "9-10": "Format-defining. Warps deckbuilding around it, or wins on its own.",
}

RUBRIC_NOTES: tuple[str, ...] = (
    "Universality is not power. A cheap colorless card playable in every deck is a 7-8 staple, "
    "not a 9-10 -- Sol Ring is an 8. Breadth of inclusion and height of effect are different "
    "axes, and conflating them compresses the top of the scale into 'famous'.",
    "Judge in a four-player pod, not a duel. Single-target effects are worth less against three "
    "opponents; symmetric and table-wide effects are worth more.",
    "A rung is a ruler mark, not a favourite card. Prefer the typical instance of a band over the "
    "best card in it, and prefer a clear read from the oracle text over a card whose power the "
    "model has to already know.",
)


def example_pool() -> dict[str, Any]:
    """The pool as plain JSON-ready data."""
    return {
        "generated_at": "",
        "corpus": {"note": "Cards resolved against KNOWLEDGE_DATABASE_URL; see pool.py."},
        "band_rubric": BAND_RUBRIC,
        "rubric_notes": list(RUBRIC_NOTES),
        "notes": [
            "An example pool, one ladder wide. Replace it with a real one -- see the module "
            "docstring in knowledge_pipeline/anchor_bench/__init__.py for the loop.",
        ],
        "ladders": [
            {
                "tag": "Ramp",
                "tag_kind": "role",
                "enum_member": "Role.RAMP",
                "search": "oracle_text LIKE '%search your library for a basic land card%' "
                          "OR (type_line LIKE 'Artifact%' AND oracle_text LIKE '%{T}: Add%')",
                "coherence": "Even: dead weight -> one land on curve -> two lands with fixing -> "
                             "free acceleration -> explosive acceleration. Weakest joint is 5-6 "
                             "to 7-8, where the axis changes from land count to raw speed.",
                "anchorable": True,
                "rungs": [
                    {
                        "band": "1-2",
                        "variety_note": "A sorcery and an artifact, both paying full price for a "
                                        "single tapped basic.",
                        "candidates": [
                            {
                                "name": "Untamed Wilds",
                                "oracle_id": "b3b4c21d-f8d7-455f-be46-d5eb909d54df",
                                "mana_cost": "{2}{G}",
                                "type_line": "Sorcery",
                                "oracle_text": "Search your library for a basic land card, put "
                                               "that card onto the battlefield, then shuffle.",
                                "power_rating": 2,
                                "note": "Three mana for one untapped basic -- a full turn behind "
                                        "the two-mana version.",
                                "why": "Rampant Growth does this for two, and the extra mana buys "
                                       "nothing at all.",
                                "source": "search",
                                "profile": "obscure",
                            },
                            {
                                "name": "Wayfarer's Bauble",
                                "oracle_id": "31f15274-301b-47c5-ba19-0ced04520878",
                                "mana_cost": "{1}",
                                "type_line": "Artifact",
                                "oracle_text": "{2}, {T}, Sacrifice this artifact: Search your "
                                               "library for a basic land card, put that card onto "
                                               "the battlefield tapped, then shuffle.",
                                "power_rating": 2,
                                "note": "Ramp that costs two cards' worth of tempo to fetch one "
                                        "tapped basic.",
                                "why": "Four total mana across two turns to end up one land "
                                       "ahead, tapped. The same slot buys a two-mana rock.",
                                "source": "search",
                                "profile": "mid",
                            },
                        ],
                    },
                    {
                        "band": "3-4",
                        "variety_note": "Two lands eventually against one land now -- the same "
                                        "band reached from opposite directions.",
                        "candidates": [
                            {
                                "name": "Burnished Hart",
                                "oracle_id": "893fed41-c144-433f-af88-bc7d419b7fb3",
                                "mana_cost": "{3}",
                                "type_line": "Artifact Creature — Elk",
                                "oracle_text": "{3}, Sacrifice this creature: Search your library "
                                               "for up to two basic land cards, put them onto the "
                                               "battlefield tapped, then shuffle.",
                                "power_rating": 3,
                                "note": "Six mana total across two turns for two tapped lands, "
                                        "playable in any deck.",
                                "why": "Colorless and slow, but it does get there, and every deck "
                                       "can run it.",
                                "source": "search",
                                "profile": "mid",
                            },
                            {
                                "name": "Rampant Growth",
                                "oracle_id": "8539f295-5d58-4436-a73a-b9277c4c7795",
                                "mana_cost": "{1}{G}",
                                "type_line": "Sorcery",
                                "oracle_text": "Search your library for a basic land card, put "
                                               "that card onto the battlefield tapped, then "
                                               "shuffle.",
                                "power_rating": 4,
                                "note": "One basic, on curve, no upside -- the floor of playable "
                                        "ramp.",
                                "why": "The card every other two-mana ramp spell is measured "
                                       "against, and beaten by.",
                                "source": "search",
                                "profile": "famous",
                            },
                        ],
                    },
                    {
                        "band": "5-6",
                        "variety_note": "",
                        "candidates": [
                            {
                                "name": "Sakura-Tribe Elder",
                                "oracle_id": "e3afc704-220f-498f-9eaa-0821b17dc24c",
                                "mana_cost": "{1}{G}",
                                "type_line": "Creature — Snake Shaman",
                                "oracle_text": "Sacrifice this creature: Search your library for a "
                                               "basic land card, put that card onto the "
                                               "battlefield tapped, then shuffle.",
                                "power_rating": 6,
                                "note": "Two mana, one tapped land, and a body that eats an "
                                        "attack first.",
                                "why": "Rampant Growth that blocks once before ramping -- the "
                                       "same effect with a second use attached.",
                                "source": "search",
                                "profile": "mid",
                            },
                        ],
                    },
                    {
                        "band": "7-8",
                        "variety_note": "",
                        "candidates": [
                            {
                                "name": "Ancient Tomb",
                                "oracle_id": "23467047-6dba-4498-b783-1ebc4f74b8c2",
                                "mana_cost": None,
                                "type_line": "Land",
                                "oracle_text": "{T}: Add {C}{C}. This land deals 2 damage to you.",
                                "power_rating": 8,
                                "note": "A land that taps for two -- acceleration that costs no "
                                        "card and two life per use.",
                                "why": "Ramp that occupies the land slot instead of a spell slot, "
                                       "which is why it is a staple rather than a build-around.",
                                "source": "search",
                                "profile": "mid",
                            },
                        ],
                    },
                    {
                        "band": "9-10",
                        "variety_note": "",
                        "candidates": [
                            {
                                "name": "Gaea's Cradle",
                                "oracle_id": "7c427c3d-ecd8-45ef-bebd-8f10f4a311db",
                                "mana_cost": None,
                                "type_line": "Legendary Land",
                                "oracle_text": "{T}: Add {G} for each creature you control.",
                                "power_rating": 9,
                                "note": "A land that taps for as much green as you have creatures "
                                        "-- ramp that scales with the board.",
                                "why": "Unbounded mana from a land, with the one condition the "
                                       "decks that want it already meet.",
                                "source": "search",
                                "profile": "famous",
                            },
                        ],
                    },
                ],
            },
        ],
    }
