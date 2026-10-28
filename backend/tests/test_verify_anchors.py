"""Corpus verification for the anchor ladders.

The offline rules — complete ladders, a rating inside its band, the two
whole-set rules — live in `test_anchors.py`. These cover the one check that
needs the corpus: a rung names a real card the format can play. The database
is never touched; `FakeSession` stands in for it, so these run anywhere.

Every test builds its own small registry through the `anchors_registry`
fixture rather than checking the shipped 330, because an assertion about the
real registry would be a test of the corpus snapshot rather than of this code.
"""

import pytest

from knowledge_pipeline.metadata_generator import anchors, verify_anchors
from knowledge_pipeline.metadata_generator.anchors import (
    BAND_ORDER,
    AnchorCard,
    AnchorLadder,
    register,
)
from knowledge_pipeline.metadata_generator.schema import Role

# Mid-band ratings for each band, so a rung can be built without arithmetic.
RATINGS = {band: sum(band.bounds) // 2 for band in BAND_ORDER}


@pytest.fixture
def anchors_registry():
    """Empty `ANCHORS` for the test, restore what the module shipped with.

    Same fixture as `test_anchors.py`'s, for the same reason: the registry is
    a module-global mutable dict, so a test that populates it would otherwise
    change what every later test sees.
    """
    shipped = dict(anchors.ANCHORS)
    shipped_profiles = anchors.GAME_STAGE_ANCHORS
    anchors.ANCHORS.clear()
    # The `game_stage` profiles are checked by the same pass and would
    # otherwise leak 14 real card names into every count below.
    anchors.GAME_STAGE_ANCHORS = ()
    try:
        yield anchors.ANCHORS
    finally:
        anchors.ANCHORS.clear()
        anchors.ANCHORS.update(shipped)
        anchors.GAME_STAGE_ANCHORS = shipped_profiles


def ladder(tag=Role.MANA_FIXING, names=("One", "Two", "Three", "Four", "Five")):
    return AnchorLadder(
        tag=tag,
        rungs=[
            AnchorCard(band, name, RATINGS[band], f"{name} sits here because of reasons.")
            for band, name in zip(BAND_ORDER, names)
        ],
    )


class FakeCard:
    def __init__(self, name, legal="legal", oracle_id=None):
        self.name = name
        self.oracle_id = oracle_id or f"id-{name}"
        self.legalities = {"commander": legal}


class FakeSession:
    """Stands in for a knowledge-database session: returns the cards it holds."""

    def __init__(self, cards):
        self._cards = list(cards)

    def scalars(self, _statement):
        return self

    def all(self):
        return self._cards


def test_a_registry_whose_every_rung_resolves_reports_nothing(anchors_registry):
    register(ladder())
    cards = [FakeCard(name) for name in verify_anchors.anchor_names()]
    assert verify_anchors.verify_anchors(FakeSession(cards)) == []


def test_a_rung_missing_from_the_corpus_is_reported(anchors_registry):
    register(ladder())
    problems = verify_anchors.verify_anchors(FakeSession([]))
    assert len(problems) == 5
    assert all("not in the corpus" in str(p) for p in problems)


def test_a_banned_rung_is_reported(anchors_registry):
    """An anchor the format cannot play is a reference point for nothing. Two
    first picks failed exactly this check when the shipped ladders were built."""
    register(ladder())
    cards = [FakeCard(name, legal="banned") for name in verify_anchors.anchor_names()]
    problems = verify_anchors.verify_anchors(FakeSession(cards))
    assert len(problems) == 5
    assert all("not Commander-legal (banned)" in str(p) for p in problems)


def test_an_ambiguous_name_is_reported_rather_than_resolved(anchors_registry):
    """Two corpus rows under one name is a rung that has to be disambiguated by
    hand — picking one silently is the error this check exists to prevent."""
    register(ladder())
    cards = [FakeCard(name) for name in verify_anchors.anchor_names()]
    cards.append(FakeCard("Three", oracle_id="id-Three-reprint"))
    problems = verify_anchors.verify_anchors(FakeSession(cards))
    assert len(problems) == 1
    assert "matches 2 cards" in str(problems[0])
    assert "id-Three" in str(problems[0])


def test_a_problem_names_the_tag_the_band_and_the_card(anchors_registry):
    """The message has to be enough to find the rung in a 2,000-line file."""
    register(ladder())
    problems = verify_anchors.verify_anchors(FakeSession([]))
    first = str(problems[0])
    assert "Role.MANA_FIXING" in first
    assert "1-2" in first
    assert "One" in first


def test_only_the_offending_rung_is_reported(anchors_registry):
    """A single bad rung must not implicate the four good ones around it."""
    register(ladder())
    cards = [FakeCard(name, legal="legal" if name != "Four" else "banned")
             for name in verify_anchors.anchor_names()]
    problems = verify_anchors.verify_anchors(FakeSession(cards))
    assert len(problems) == 1
    assert "Four" in str(problems[0])


def test_anchor_names_deduplicates_across_ladders(anchors_registry):
    """The corpus query is by name, so a name shared by two ladders is looked
    up once. (The registry forbids that sharing; the helper does not assume it.)"""
    register(ladder(Role.MANA_FIXING))
    register(ladder(Role.MANA_ROCK, names=("One", "Six", "Seven", "Eight", "Nine")))
    assert verify_anchors.anchor_names() == {
        "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    }


def test_the_shipped_registry_is_checkable_without_a_database():
    """`anchor_names` reads the real registry — it must not need a session.

    Guards the split the module docstring states: the structural rules are
    offline, and only the corpus lookup needs `KNOWLEDGE_DATABASE_URL`.
    """
    names = verify_anchors.anchor_names()
    rungs = sum(len(ladder.rungs) for ladder in anchors.ANCHORS.values())
    profiles = len(anchors.GAME_STAGE_ANCHORS)
    # Not `rungs + profiles`: the two sets may legitimately share a card, and
    # four of them do. Power and timing are different scales, so one card
    # carrying a rung and a profile states nothing contradictory.
    assert rungs < len(names) <= rungs + profiles
