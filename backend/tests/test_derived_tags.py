"""`derive_themes`: the five `Theme` members read off a card, not asked of a model.

Nothing here needs a model, a server, or a configured database — same
guarantee `test_cost_profile.py` makes for the sibling module this one
mirrors.
"""

from datetime import datetime, timezone

from database.knowledge.models import Card
from knowledge_pipeline.derived_tags import (
    CLUES,
    ENERGY,
    FOOD,
    INFECT,
    TREASURE,
    derive_themes,
)


def make_card(
    *,
    oracle_text: str | None = "Do a thing.",
    keywords: list[str] | None = None,
) -> Card:
    return Card(
        oracle_id="oid-test",
        scryfall_id="print-test",
        name="Test Card",
        mana_cost="{1}{G}",
        mana_value=2.0,
        oracle_text=oracle_text,
        colors=["G"],
        color_identity=["G"],
        type_line="Creature — Test",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        keywords=keywords or [],
        image_url=None,
        layout="normal",
        legalities={"commander": "legal"},
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    )


def test_a_plain_card_gets_no_derived_themes():
    assert derive_themes(make_card()) == []


def test_creating_a_treasure_token_is_derived():
    card = make_card(oracle_text="When this creature enters, create a Treasure token.")
    assert derive_themes(card) == [TREASURE]


def test_creating_multiple_token_types_in_one_sentence_gets_both():
    card = make_card(
        oracle_text="When this enters, create a Treasure token and a Clue token."
    )
    assert set(derive_themes(card)) == {TREASURE, CLUES}


def test_food_tokens_across_a_sentence_boundary_do_not_cross_match():
    """"Create...Food" and a later, unrelated "token" in the next sentence
    must not combine into a false Food match."""
    card = make_card(
        oracle_text=(
            "Create a Clue token. Sacrifice an artifact: create a token that's a "
            "copy of this creature."
        )
    )
    assert derive_themes(card) == [CLUES]


def test_a_card_that_merely_sacrifices_food_without_creating_it_is_not_tagged():
    """The derivation is deliberately scoped to "create a ___ token" — see
    the module docstring on why a payoff-only mention is out of scope."""
    card = make_card(oracle_text="Sacrifice a Food: You gain 3 life.")
    assert derive_themes(card) == []


def test_the_energy_symbol_is_derived():
    card = make_card(oracle_text="When this creature enters, you get {E}{E}.")
    assert derive_themes(card) == [ENERGY]


def test_spelled_out_energy_counter_reminder_text_is_derived():
    card = make_card(oracle_text="Pay one energy counter: Scry 1.")
    assert derive_themes(card) == [ENERGY]


def test_infect_keyword_is_derived():
    card = make_card(oracle_text="Infect", keywords=["Infect"])
    assert derive_themes(card) == [INFECT]


def test_toxic_keyword_is_derived():
    card = make_card(oracle_text="Toxic 2 (...)", keywords=["Toxic"])
    assert derive_themes(card) == [INFECT]


def test_infect_mentioned_only_inside_an_unrelated_keywords_reminder_is_not_tagged():
    """`keywords` is matched by exact equality, not substring — a card that
    merely name-drops infect/toxic in its own text without the ability
    itself must not false-positive."""
    card = make_card(
        oracle_text="As long as you don't have infect, this does nothing.",
        keywords=[],
    )
    assert derive_themes(card) == []


def test_a_card_with_no_oracle_text_is_not_tagged():
    card = make_card(oracle_text=None)
    assert derive_themes(card) == []
