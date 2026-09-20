"""Which objects in the bulk file get imported.

One rule: is it a card? Legality is deliberately not consulted, because the
corpus is shared and hosted — see `card_filter.py` for the full argument.
These tests exist to keep a format filter from creeping back in.
"""

import pytest

from knowledge_pipeline.scryfall_importer.card_filter import NON_CARD_LAYOUTS, is_card


def card(**overrides) -> dict:
    base = {
        "name": "Test Card",
        "layout": "normal",
        "legalities": {"commander": "legal", "standard": "not_legal"},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "legalities",
    [
        {"commander": "legal"},
        {"commander": "banned"},
        {"commander": "not_legal", "standard": "not_legal"},
        {"vintage": "restricted"},
        {},
    ],
    ids=["legal", "banned", "legal-nowhere", "restricted-only", "no-map"],
)
def test_legality_never_excludes_a_card(legalities):
    """The regression this file exists for.

    A banned or never-legal card is still a card somebody owns, and CSV
    collection import resolves owned names through the corpus. 1,945
    paper-printed cards are legal in no format at all; dropping them would
    turn each into a permanent placeholder in someone's collection.
    """
    assert is_card(card(legalities=legalities))


@pytest.mark.parametrize("layout", sorted(NON_CARD_LAYOUTS))
def test_non_card_layouts_are_excluded(layout):
    # Legal everywhere and still rejected: the layout is the only filter.
    assert not is_card(card(layout=layout, legalities={"commander": "legal"}))


@pytest.mark.parametrize(
    "layout",
    ["normal", "transform", "modal_dfc", "split", "flip", "adventure", "prepare"],
)
def test_real_card_layouts_are_kept(layout):
    """Layouts that look unusual but are genuine playable cards.

    `prepare` in particular is a split-style spell, not a product artifact.
    """
    assert is_card(card(layout=layout))


def test_an_unknown_layout_is_treated_as_a_card():
    """Fail open. A layout Scryfall adds after this list was written is far
    more likely to be a new card type than a new kind of token, and a missing
    card is worse than a stray one."""
    assert is_card(card(layout="some_future_layout"))


def test_a_missing_layout_is_treated_as_a_card():
    assert is_card({"name": "Odd", "legalities": {}})
