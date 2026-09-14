"""Format profiles decide which cards an import accepts.

Tome is Commander-only, so the guarantee under test is twofold: Commander is
the only pool an import can write, *and* the scaffolding for other formats is
real enough that enabling one is a flag flip.
"""

import pytest

from knowledge_pipeline.scryfall_importer.formats import (
    PROFILES,
    FormatNotEnabledError,
    FormatProfile,
    UnknownFormatError,
    enabled_profiles,
    resolve,
    selectable_names,
)


def card(**overrides) -> dict:
    base = {
        "name": "Test Card",
        "layout": "normal",
        "legalities": {"commander": "legal", "standard": "not_legal", "vintage": "legal"},
    }
    base.update(overrides)
    return base


def test_commander_profile_filters_on_commander_legality():
    profile = resolve("commander")

    assert profile.accepts(card())
    assert not profile.accepts(card(legalities={"commander": "banned"}))
    assert not profile.accepts(card(legalities={"commander": "not_legal"}))
    assert not profile.accepts(card(legalities={}))


def test_edh_resolves_to_commander():
    assert resolve("edh").name == "commander"


def test_commander_is_the_only_importable_format():
    assert list(enabled_profiles()) == ["commander"]
    assert selectable_names() == ["commander", "edh"]


@pytest.mark.parametrize("name", ["standard", "pioneer"])
def test_scaffolded_formats_are_registered_but_refused(name):
    """Registered so the machinery stays multi-format; refused so users can't
    fill a Commander app with a Standard card pool."""
    assert name in PROFILES

    with pytest.raises(FormatNotEnabledError) as excinfo:
        resolve(name)

    assert "Commander-only" in str(excinfo.value)


def test_a_scaffolded_profile_still_works_when_asked_for_explicitly():
    """The switch-over is `enabled=True`, not a rewrite — so the filter itself
    has to be correct already."""
    profile = resolve("standard", allow_disabled=True)

    assert profile.accepts(card(legalities={"standard": "legal"}))
    assert not profile.accepts(card())  # commander-legal, standard not_legal


def test_commander_rejects_restricted_by_default():
    # "restricted" is a Vintage-only concept; profiles use the default
    # accepted set of {"legal"} unless they say otherwise.
    assert not resolve("commander").accepts(card(legalities={"commander": "restricted"}))


def test_accepted_can_widen_the_legality_values():
    """What a future Vintage entry would need: a restricted card is legal,
    just limited to one copy, so `== "legal"` would discard Black Lotus."""
    vintage = FormatProfile(
        "vintage", "Vintage", "vintage", accepted=frozenset({"legal", "restricted"})
    )

    assert vintage.accepts(card(name="Black Lotus", legalities={"vintage": "restricted"}))
    assert not vintage.accepts(card(legalities={"vintage": "banned"}))


@pytest.mark.parametrize(
    "layout",
    ["token", "emblem", "art_series", "double_faced_token", "vanguard", "front_card"],
)
def test_non_card_layouts_are_excluded_from_every_profile(layout):
    for profile in PROFILES.values():
        legal_everywhere = card(
            layout=layout, legalities={profile.legality_key: "legal"}
        )
        assert not profile.accepts(legal_everywhere), profile.name


@pytest.mark.parametrize("layout", ["normal", "transform", "modal_dfc", "split", "prepare"])
def test_real_card_layouts_are_kept(layout):
    """Layouts that look unusual but are genuine playable cards.

    `prepare` in particular is a split-style spell, not a product artifact.
    """
    assert resolve("commander").accepts(card(layout=layout))


def test_resolve_is_case_insensitive_and_trims():
    assert resolve("  Commander ").name == "commander"
    assert resolve(" EDH ").name == "commander"


def test_unknown_format_lists_the_available_ones():
    with pytest.raises(UnknownFormatError) as excinfo:
        resolve("pokemon")

    assert "commander" in str(excinfo.value)


def test_every_profile_has_a_distinct_name_matching_its_key():
    for key, profile in PROFILES.items():
        assert key == profile.name
