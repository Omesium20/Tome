"""The cost classifier: which cards' mana value misstates their timing.

Two things are worth pinning here, and they are not the keyword tables —
those are data, and a missing entry costs one card rather than corrupting a
run. What matters is:

- the **direction** calls that a keyword list gets backwards (`Delve` and
  `{X}` are *later*, not cheaper), since those are the cases the module exists
  to get right;
- that `facts_from_prompt` and `facts_from_card` agree, because
  `benchmark/report.py` splits plain-cost cards from exceptions through the
  first and the generator writes notes through the second. A disagreement
  there silently puts cards in the wrong half of a metric.

Nothing here needs a model, a server, or a configured database.
"""

from datetime import datetime, timezone

import pytest

from database.knowledge.models import Card
from knowledge_pipeline.cost_profile import (
    COST_NOTE_PREFIX,
    CostDirection,
    CostFacts,
    cost_note,
    cost_signals,
    facts_from_card,
    facts_from_prompt,
    has_cost_exception,
)
from knowledge_pipeline.metadata_generator.prompt import render_card_facts


def make_card(
    name: str = "Test Card",
    *,
    mana_cost: str | None = "{1}{G}",
    mana_value: float = 2.0,
    oracle_text: str | None = "Do a thing.",
    type_line: str = "Creature — Test",
    keywords: list[str] | None = None,
    layout: str = "normal",
) -> Card:
    return Card(
        oracle_id=f"oid-{name}",
        scryfall_id=f"print-{name}",
        name=name,
        mana_cost=mana_cost,
        mana_value=mana_value,
        oracle_text=oracle_text,
        colors=["G"],
        color_identity=["G"],
        type_line=type_line,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        keywords=keywords or [],
        image_url=None,
        layout=layout,
        legalities={"commander": "legal"},
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    )


def directions(facts: CostFacts) -> set[CostDirection]:
    return {signal.direction for signal in cost_signals(facts)}


# ---------------------------------------------------------------------------
# A plain card is the common case, and the one the metric is computed over.
# ---------------------------------------------------------------------------


def test_plain_card_has_no_signals() -> None:
    facts = facts_from_card(make_card("Lightning Bolt"))
    assert cost_signals(facts) == ()
    assert not has_cost_exception(facts)
    assert cost_note(facts) is None


# ---------------------------------------------------------------------------
# Direction, which is the whole point of the module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword",
    ["Convoke", "Improvise", "Affinity", "Evoke", "Morph", "Prototype"],
)
def test_reductions_and_alternative_costs_read_cheaper(keyword: str) -> None:
    facts = facts_from_card(make_card(keywords=[keyword]))
    assert directions(facts) == {CostDirection.CHEAPER}


@pytest.mark.parametrize("keyword", ["Cycling", "Channel", "Foretell", "Plot"])
def test_cheap_early_modes_are_not_cost_reductions(keyword: str) -> None:
    """Cycling does not make the card castable early — it gives it another use.

    Filed apart from `CHEAPER` so the note can say which of the two it is.
    """
    facts = facts_from_card(make_card(keywords=[keyword]))
    assert directions(facts) == {CostDirection.EARLY_MODE}


@pytest.mark.parametrize(
    "keyword",
    ["Equip", "Level Up", "Unearth", "Kicker", "Suspend", "Flashback", "Crew"],
)
def test_activation_and_second_use_costs_read_later(keyword: str) -> None:
    facts = facts_from_card(make_card(keywords=[keyword]))
    assert directions(facts) == {CostDirection.LATER}


def test_delve_reads_later_not_cheaper() -> None:
    """The case a keyword list gets backwards.

    Delve reduces a cost, so it looks like cost reduction — but it pays with
    a graveyard, which the early game does not have. Treasure Cruise is a
    cheap *late* card, and calling it cheaper would push `early` the wrong way.
    """
    facts = facts_from_card(make_card("Treasure Cruise", keywords=["Delve"]))
    assert directions(facts) == {CostDirection.LATER}


def test_x_in_mana_cost_reads_later() -> None:
    """The other backwards case, and the more damaging one.

    `{X}{B}{B}` reports mana value 3: a late-game card wearing an early-game
    cost, pushing `early` up exactly where the model is already biased high.
    """
    facts = facts_from_card(
        make_card("Torment of Hailfire", mana_cost="{X}{B}{B}", mana_value=3.0)
    )
    assert CostDirection.LATER in directions(facts)


def test_land_is_structural_not_cheap() -> None:
    facts = facts_from_card(
        make_card("Forest", mana_cost=None, mana_value=0.0, type_line="Land")
    )
    assert directions(facts) == {CostDirection.STRUCTURAL}


def test_card_can_be_wrong_in_both_directions() -> None:
    facts = facts_from_card(
        make_card(
            "Chord of Calling",
            mana_cost="{X}{G}{G}{G}",
            keywords=["Convoke"],
        )
    )
    assert directions(facts) == {CostDirection.CHEAPER, CostDirection.LATER}
    assert "both directions" in (cost_note(facts) or "")


# ---------------------------------------------------------------------------
# Oracle-text patterns, and the reminder text that used to spoof them.
# ---------------------------------------------------------------------------


def test_alternative_cost_is_detected() -> None:
    """Force of Will's phrasing, which the free-cast wording does not cover."""
    facts = facts_from_card(
        make_card(
            "Force of Will",
            oracle_text=(
                "You may pay 1 life and exile a blue card from your hand "
                "rather than pay this spell's mana cost.\nCounter target spell."
            ),
        )
    )
    assert directions(facts) == {CostDirection.CHEAPER}


def test_reminder_text_does_not_create_a_discount() -> None:
    """Cascade *describes* a free cast; it does not get one.

    Matching the loose phrasing against raw oracle text fired on 231 corpus
    cards with no discount of their own, so reminder text is stripped before
    any pattern runs.
    """
    facts = facts_from_card(
        make_card(
            "Bloodbraid Elf",
            keywords=["Cascade", "Haste"],
            oracle_text=(
                "Haste\nCascade (When you cast this spell, exile cards from the "
                "top of your library until you exile a nonland card that costs "
                "less. You may cast it without paying its mana cost.)"
            ),
        )
    )
    assert cost_signals(facts) == ()


def test_self_cost_reduction_is_detected() -> None:
    facts = facts_from_card(
        make_card(oracle_text="This spell costs {1} less to cast for each Elf you control.")
    )
    assert directions(facts) == {CostDirection.CHEAPER}


# ---------------------------------------------------------------------------
# The two entry points have to agree — see the module docstring.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card",
    [
        make_card("Lightning Bolt"),
        make_card("Bonesplitter", keywords=["Equip"], type_line="Artifact — Equipment"),
        make_card("Shark Typhoon", keywords=["Cycling", "Flash"]),
        make_card("Torment of Hailfire", mana_cost="{X}{B}{B}"),
        make_card("Forest", mana_cost=None, type_line="Land"),
        make_card("Ancestral Vision", mana_cost=None, keywords=["Suspend"]),
        make_card(
            "Monk Class",
            type_line="Enchantment — Class",
            layout="class",
            oracle_text=(
                "(Gain the next level as a sorcery to add its ability.)\n"
                "The second spell you cast each turn costs {1} less to cast."
            ),
        ),
    ],
    ids=lambda card: card.name,
)
def test_prompt_and_card_paths_agree(card: Card) -> None:
    from_card = facts_from_card(card)
    from_prompt = facts_from_prompt(render_card_facts(card))
    assert has_cost_exception(from_prompt) == has_cost_exception(from_card)


def test_prompt_path_keeps_oracle_text_that_opens_with_reminder_text() -> None:
    """A placeholder is a whole parenthesised line, not merely a leading `(`.

    The looser rule read `(Gain the next level ...)` as "this field is absent"
    and discarded the rules text of every Class and Saga in the corpus.
    """
    card = make_card(
        "Monk Class",
        layout="class",
        oracle_text=(
            "(Gain the next level as a sorcery to add its ability.)\n"
            "The second spell you cast each turn costs {1} less to cast."
        ),
    )
    facts = facts_from_prompt(render_card_facts(card))
    assert "costs {1} less to cast" in facts.oracle_text
    assert directions(facts) == {CostDirection.CHEAPER}


def test_prompt_path_reads_a_missing_mana_cost_as_missing() -> None:
    """`render_card_facts` prints `(none)`, which is truthy if taken literally."""
    card = make_card("Ancestral Vision", mana_cost=None, mana_value=0.0)
    facts = facts_from_prompt(render_card_facts(card))
    assert facts.mana_cost == ""
    assert CostDirection.STRUCTURAL in directions(facts)


def test_prompt_path_does_not_mistake_a_transform_card_for_a_split_card() -> None:
    """Both layouts print `A // B` as the name; only split prints two costs.

    Keying the heuristic on the name classified 388 ordinary double-faced
    cards as split cards.
    """
    card = make_card("Graveyard Trespasser // Graveyard Glutton", layout="transform")
    assert not has_cost_exception(facts_from_prompt(render_card_facts(card)))

    split = make_card("Cease // Desist", mana_cost="{1}{U} // {2}{B}", layout="split")
    assert has_cost_exception(facts_from_prompt(render_card_facts(split)))


# ---------------------------------------------------------------------------
# The rendered note.
# ---------------------------------------------------------------------------


def test_note_is_prefixed_and_states_a_fact() -> None:
    facts = facts_from_card(make_card("Bonesplitter", keywords=["Equip"]))
    note = cost_note(facts)
    assert note is not None
    assert note.startswith(COST_NOTE_PREFIX)
    assert "understates" in note


def test_note_is_capped_rather_than_listing_everything() -> None:
    """A card with a complicated cost has its oracle text in the prompt already."""
    facts = facts_from_card(
        make_card(
            "Busy Card",
            mana_cost="{X}{G}",
            keywords=["Equip", "Kicker", "Flashback", "Suspend", "Buyback"],
        )
    )
    note = cost_note(facts)
    assert note is not None
    assert note.count(";") <= 2
