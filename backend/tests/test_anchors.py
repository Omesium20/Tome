"""Anchor ladders: the five-rung calibration structure and how it renders.

These tests guard the two invariants the ladder mechanism rests on
(`docs/data-model.md#anchor-cards`): a rung sits inside the band it
illustrates, and a ladder is complete or it does not exist. Both are enforced
at construction rather than at render time, because a malformed ladder that
only fails when a full-corpus run builds its system prompt would be found
~31,830 calls too late.

`ANCHORS` is a module-global mutable dict. Every test that puts anything in
it goes through the `anchors_registry` fixture, which empties it for the test
and restores whatever the module shipped with afterwards — otherwise one test
populating a tag would silently change what `build_system_prompt()` returns
for every test after it, in this file and in the rest of the suite.
"""

import pytest

from knowledge_pipeline.metadata_generator import anchors
from knowledge_pipeline.metadata_generator.anchors import (
    BAND_ORDER,
    AnchorCard,
    AnchorLadder,
    PowerBand,
    anchor_key,
    kind_of,
    ladder_for,
    missing_tags,
    register,
    render_anchors,
)
from knowledge_pipeline.metadata_generator.prompt import build_system_prompt
from knowledge_pipeline.metadata_generator.schema import Role, SynergyTag, Theme

# What the module shipped with, captured before any test can touch it. The
# last test in this file compares against it to prove the fixture below
# really does restore the registry.
SHIPPED_ANCHORS = dict(anchors.ANCHORS)


@pytest.fixture
def anchors_registry():
    """`anchors.ANCHORS`, emptied for the test and restored afterwards.

    Yields the dict itself rather than a copy: `render_anchors()` and
    `missing_tags()` read the module global by name, so a test has to mutate
    that object in place for them to see it.
    """
    snapshot = dict(anchors.ANCHORS)
    anchors.ANCHORS.clear()
    try:
        yield anchors.ANCHORS
    finally:
        anchors.ANCHORS.clear()
        anchors.ANCHORS.update(snapshot)


def rung(band: PowerBand, name: str = "", note: str = "") -> AnchorCard:
    """An `AnchorCard` sitting at the low edge of `band`, with filler text."""
    low, _high = band.bounds
    return AnchorCard(
        band=band,
        name=name or f"Card {band.value}",
        power_rating=low,
        note=note or f"Illustrates the {band.value} band.",
    )


def ladder(tag: Role | Theme = Role.RAMP, bands=BAND_ORDER) -> AnchorLadder:
    """A complete, valid ladder for `tag` (or a deliberately broken one)."""
    return AnchorLadder(tag=tag, rungs=[rung(band) for band in bands])


# ------------------------------------------------------------- PowerBand ---


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        (PowerBand.B1_2, (1, 2)),
        (PowerBand.B3_4, (3, 4)),
        (PowerBand.B5_6, (5, 6)),
        (PowerBand.B7_8, (7, 8)),
        (PowerBand.B9_10, (9, 10)),
    ],
)
def test_bounds_parses_each_member(band, expected):
    """`bounds` is parsed from the member value, so a typo there is a bug here."""
    assert band.bounds == expected


def test_the_bands_tile_the_whole_scale_with_no_gap_or_overlap():
    """1-10 is covered exactly once over — an uncovered rating would be a
    `power_rating` no rung could ever illustrate."""
    covered = [
        rating
        for band in BAND_ORDER
        for rating in range(band.bounds[0], band.bounds[1] + 1)
    ]
    assert covered == list(range(1, 11))


@pytest.mark.parametrize("band", list(PowerBand))
def test_contains_is_inclusive_at_both_edges(band):
    low, high = band.bounds
    assert band.contains(low)
    assert band.contains(high)


@pytest.mark.parametrize("band", list(PowerBand))
def test_contains_rejects_just_outside_either_edge(band):
    low, high = band.bounds
    assert not band.contains(low - 1)
    assert not band.contains(high + 1)


def test_contains_accepts_a_fractional_rating_inside_the_band():
    """`power_rating` is a float, so a rung may sit between the integers."""
    assert PowerBand.B5_6.contains(5.5)
    assert not PowerBand.B5_6.contains(6.5)


# ------------------------------------------------------------ BAND_ORDER ---


def test_band_order_covers_every_member_exactly_once():
    assert sorted(BAND_ORDER, key=lambda band: band.value) == sorted(
        PowerBand, key=lambda band: band.value
    )
    assert len(BAND_ORDER) == len(set(BAND_ORDER)) == len(PowerBand)


def test_band_order_is_ascending():
    """The order a ladder renders in. Descending would put the strongest card
    first and quietly invert the comparison the model is asked to make."""
    bounds = [band.bounds for band in BAND_ORDER]
    assert bounds == sorted(bounds)


# ------------------------------------------------------------ AnchorCard ---


def test_a_valid_anchor_card_keeps_its_fields():
    card = AnchorCard(PowerBand.B5_6, "Cultivate", 6, "Efficient ramp, no upside.")
    assert (card.band, card.name, card.power_rating, card.note) == (
        PowerBand.B5_6,
        "Cultivate",
        6,
        "Efficient ramp, no upside.",
    )


@pytest.mark.parametrize("band", list(PowerBand))
def test_a_rating_on_either_band_edge_is_accepted(band):
    low, high = band.bounds
    assert AnchorCard(band, "Edge", low, "Low edge.").power_rating == low
    assert AnchorCard(band, "Edge", high, "High edge.").power_rating == high


def test_a_rating_above_the_band_is_rejected():
    with pytest.raises(ValueError, match="falls outside"):
        AnchorCard(PowerBand.B3_4, "Too Strong", 7, "Rated well above its band.")


def test_a_rating_below_the_band_is_rejected():
    with pytest.raises(ValueError, match="falls outside"):
        AnchorCard(PowerBand.B7_8, "Too Weak", 2, "Rated well below its band.")


@pytest.mark.parametrize("name", ["", "   ", "\n\t"], ids=["empty", "spaces", "ws"])
def test_an_anchor_without_a_name_is_rejected(name):
    with pytest.raises(ValueError, match="needs a card name"):
        AnchorCard(PowerBand.B5_6, name, 5, "A note.")


@pytest.mark.parametrize("note", ["", "   ", "\n\t"], ids=["empty", "spaces", "ws"])
def test_an_anchor_without_a_note_is_rejected(note):
    """A bare name only lets the model pattern-match the card it already
    knows; the note is what states *why* the rung sits where it does."""
    with pytest.raises(ValueError, match="needs a note"):
        AnchorCard(PowerBand.B5_6, "Cultivate", 5, note)


# ---------------------------------------------------------- AnchorLadder ---


@pytest.mark.parametrize(
    "order",
    [
        tuple(reversed(BAND_ORDER)),
        (PowerBand.B5_6, PowerBand.B1_2, PowerBand.B9_10, PowerBand.B3_4, PowerBand.B7_8),
        (PowerBand.B9_10, PowerBand.B7_8, PowerBand.B1_2, PowerBand.B5_6, PowerBand.B3_4),
    ],
    ids=["reversed", "scrambled", "scrambled-2"],
)
def test_rungs_are_sorted_into_band_order_however_they_are_passed(order):
    """The band travels on the card, so a caller can write them in any order
    and still get a ladder that reads low to high."""
    built = AnchorLadder(tag=Theme.TOKENS, rungs=[rung(band) for band in order])
    assert [r.band for r in built.rungs] == list(BAND_ORDER)


def test_a_ladder_accepts_a_tuple_as_well_as_a_list():
    built = AnchorLadder(tag=Role.REMOVAL, rungs=tuple(rung(b) for b in BAND_ORDER))
    assert [r.band for r in built.rungs] == list(BAND_ORDER)


def test_a_duplicate_band_is_rejected():
    rungs = [rung(band) for band in BAND_ORDER]
    rungs.append(rung(PowerBand.B5_6, name="Second 5-6 Card"))
    with pytest.raises(ValueError, match="same power band"):
        AnchorLadder(tag=Role.RAMP, rungs=rungs)


def test_a_duplicate_band_standing_in_for_a_missing_one_is_rejected():
    """Five rungs, but 9-10 was filed twice and 1-2 never written. Counting
    rungs would pass this; checking the set of bands is what catches it."""
    rungs = [
        rung(PowerBand.B9_10),
        rung(PowerBand.B3_4),
        rung(PowerBand.B5_6),
        rung(PowerBand.B7_8),
        rung(PowerBand.B9_10, name="Another 9-10 Card"),
    ]
    with pytest.raises(ValueError, match="same power band"):
        AnchorLadder(tag=Role.RAMP, rungs=rungs)


@pytest.mark.parametrize(
    "missing",
    [PowerBand.B9_10, PowerBand.B5_6, PowerBand.B1_2],
    ids=["missing-top", "missing-middle", "missing-bottom"],
)
def test_an_incomplete_ladder_is_rejected(missing):
    """A gap is an unanchored band the model fills with a guess nobody
    reviewed — which is worse than no ladder, because the surrounding rungs
    make the guess look calibrated."""
    bands = [band for band in BAND_ORDER if band is not missing]
    with pytest.raises(ValueError, match="is missing the"):
        ladder(Role.RAMP, bands=bands)


def test_a_ladder_with_no_rungs_at_all_is_rejected():
    with pytest.raises(ValueError, match="is missing the"):
        AnchorLadder(tag=Role.RAMP, rungs=[])


def test_the_error_names_every_band_that_is_missing():
    with pytest.raises(ValueError) as excinfo:
        ladder(Theme.STAX, bands=[PowerBand.B3_4, PowerBand.B5_6, PowerBand.B7_8])
    message = str(excinfo.value)
    assert Theme.STAX.value in message
    assert "1-2" in message
    assert "9-10" in message


def test_render_emits_all_five_rungs_in_ascending_order():
    built = AnchorLadder(
        tag=Role.RAMP,
        rungs=[
            AnchorCard(PowerBand.B9_10, "Top Card", 9, "Format-defining ramp."),
            AnchorCard(PowerBand.B1_2, "Bottom Card", 1, "Barely ramp at all."),
            AnchorCard(PowerBand.B5_6, "Middle Card", 6, "Solidly playable ramp."),
            AnchorCard(PowerBand.B3_4, "Low Card", 3, "Budget-only ramp."),
            AnchorCard(PowerBand.B7_8, "High Card", 8, "A staple rock."),
        ],
    )
    rendered = built.render()

    assert rendered.startswith(f"{Role.RAMP.value} (role):")
    positions = [
        rendered.index(name)
        for name in ("Bottom Card", "Low Card", "Middle Card", "High Card", "Top Card")
    ]
    assert positions == sorted(positions)
    for band in BAND_ORDER:
        assert band.value in rendered
    assert "Format-defining ramp." in rendered
    assert "power_rating=8" in rendered
    assert len(rendered.splitlines()) == 6  # tag header + one line per rung


# --------------------------------------------------------- render_anchors ---


def test_render_anchors_is_empty_when_the_registry_is_empty(anchors_registry):
    """The smoke-test path: no ladders, no calibration block, and the caller
    (not this function) decides whether that is acceptable."""
    assert render_anchors() == ""


def test_render_anchors_includes_the_calibration_instructions(anchors_registry):
    register(ladder(Role.RAMP))
    rendered = render_anchors()

    assert "Calibration ladders" in rendered
    assert "power_rating" in rendered
    assert "place it where it falls between them" in rendered


def test_render_anchors_includes_every_populated_tag(anchors_registry):
    register(ladder(Role.RAMP))
    register(ladder(Theme.TOKENS))
    register(ladder(Role.REMOVAL))
    rendered = render_anchors()

    for tag in (Role.RAMP, Theme.TOKENS, Role.REMOVAL):
        assert f"{tag.value} ({kind_of(tag)}):" in rendered
    # Every rung of every ladder, not just the tag headers.
    assert rendered.count("Card 9-10") == 3


def test_render_anchors_omits_tags_that_have_no_ladder(anchors_registry):
    register(ladder(Role.RAMP))
    rendered = render_anchors()

    assert f"{Role.RAMP.value} (role):" in rendered
    assert f"{Theme.VOLTRON.value}:" not in rendered


# ----------------------------------------------------------- missing_tags ---


def test_missing_tags_lists_every_role_and_theme_when_empty(anchors_registry):
    missing = missing_tags()

    assert set(missing) == {*Role, *Theme}
    assert len(missing) == len(Role) + len(Theme)


def test_missing_tags_does_not_include_synergy_tags(anchors_registry):
    """Ladders calibrate `power_rating` through roles and themes only —
    `synergy_tags` carry no rating, so they have nothing to anchor."""
    assert not set(missing_tags()) & set(SynergyTag)


def test_missing_tags_omits_a_tag_once_it_has_a_ladder(anchors_registry):
    register(ladder(Role.RAMP))
    register(ladder(Theme.TOKENS))

    missing = missing_tags()

    assert Role.RAMP not in missing
    assert Theme.TOKENS not in missing
    assert Role.REMOVAL in missing
    assert len(missing) == len(Role) + len(Theme) - 2


# ----------------------------------------------------- build_system_prompt ---


def test_the_system_prompt_omits_the_anchor_block_when_the_registry_is_empty(
    anchors_registry,
):
    prompt = build_system_prompt()

    assert "Calibration ladders" not in prompt
    # The rest of the cached block is still there.
    assert "Valid `roles` values:" in prompt
    assert Role.RAMP.value in prompt


def test_the_system_prompt_includes_the_anchor_block_when_populated(anchors_registry):
    register(ladder(Role.RAMP))
    prompt = build_system_prompt()

    assert "Calibration ladders" in prompt
    assert "Card 9-10" in prompt
    assert prompt.index("Valid `roles` values:") < prompt.index("Calibration ladders")


# --------------------------------------------------------- the registry ---


def test_the_shipped_registry_holds_only_valid_complete_ladders():
    """Every shipped ladder is filed under its own tag's key, and complete.

    This asserted `isinstance(key, (Role, Theme))` while `ANCHORS` was empty,
    and was therefore green against a registry keyed the way `anchor_key`
    exists to avoid. Keys are qualified strings; the enum member lives on the
    ladder.
    """
    for key, tag_ladder in anchors.ANCHORS.items():
        assert isinstance(tag_ladder, AnchorLadder)
        assert isinstance(tag_ladder.tag, (Role, Theme))
        assert key == anchor_key(tag_ladder.tag)
        assert [r.band for r in tag_ladder.rungs] == list(BAND_ORDER)


def test_every_role_and_theme_ships_with_a_ladder():
    """An unanchored tag is a band of the scale nobody calibrated. Not fatal
    at runtime -- `missing_tags` exists so it can be seen -- but shipping one
    means part of the corpus is rated freehand, so it should fail here."""
    assert missing_tags() == []


def test_no_shipped_card_is_rated_differently_in_another_ladder():
    """The rule that decided several rungs: a card rated 9 under one tag and 8
    under another teaches, inside one prompt, that the scale depends on which
    ladder you read."""
    ratings: dict[str, tuple[float, str]] = {}
    for key, tag_ladder in anchors.ANCHORS.items():
        for card in tag_ladder.rungs:
            rating, first = ratings.setdefault(card.name, (card.power_rating, key))
            assert rating == card.power_rating, (
                f"{card.name} is {rating} in {first} and {card.power_rating} in {key}"
            )


def test_no_shipped_card_anchors_two_ladders():
    """A card doing double duty is one fewer independent reference point, and
    across the `Role`/`Theme` pairs that share a word (Lifegain, Equipment) it
    would anchor both sides of the distinction those tags exist to draw."""
    seen: dict[str, str] = {}
    for key, tag_ladder in anchors.ANCHORS.items():
        for card in tag_ladder.rungs:
            assert card.name not in seen, f"{card.name}: {seen[card.name]} and {key}"
            seen[card.name] = key


def test_the_registry_is_left_exactly_as_it_shipped():
    """Runs last: proves `anchors_registry` restored the global rather than
    leaking a test ladder into the rest of the suite."""
    assert anchors.ANCHORS == SHIPPED_ANCHORS


# -------------------------------------------- Role/Theme key collision ---
#
# `Role` and `Theme` are StrEnums, so a member *is* its string value:
# `Role.LIFEGAIN == Theme.LIFEGAIN` is True and the two hash identically.
# Both vocabularies define "Lifegain" and "Equipment". Keyed on the member,
# `ANCHORS` collapsed each pair into one entry — two of 52 ladders vanished
# with nothing raised, and `missing_tags()` was blind to it because it
# compared with the same broken equality. These guard the fix.


COLLIDING_TAGS = [
    (Role.LIFEGAIN, Theme.LIFEGAIN),
    (Role.EQUIPMENT, Theme.EQUIPMENT),
]


@pytest.mark.parametrize("role_tag, theme_tag", COLLIDING_TAGS, ids=lambda t: t.name)
def test_colliding_role_and_theme_are_equal_as_enum_members(role_tag, theme_tag):
    """The premise of the bug, asserted so the fix isn't mysterious later."""
    assert role_tag == theme_tag
    assert hash(role_tag) == hash(theme_tag)


@pytest.mark.parametrize("role_tag, theme_tag", COLLIDING_TAGS, ids=lambda t: t.name)
def test_anchor_key_separates_a_role_from_a_theme_of_the_same_name(role_tag, theme_tag):
    assert anchor_key(role_tag) != anchor_key(theme_tag)
    assert anchor_key(role_tag).startswith("Role.")
    assert anchor_key(theme_tag).startswith("Theme.")


@pytest.mark.parametrize("role_tag, theme_tag", COLLIDING_TAGS, ids=lambda t: t.name)
def test_both_halves_of_a_name_collision_register_and_survive(
    anchors_registry, role_tag, theme_tag
):
    """The regression itself: registering both must keep both."""
    register(ladder(role_tag))
    register(ladder(theme_tag))

    assert len(anchors_registry) == 2
    assert ladder_for(role_tag) is not None
    assert ladder_for(theme_tag) is not None
    assert ladder_for(role_tag).tag is role_tag
    assert ladder_for(theme_tag).tag is theme_tag


@pytest.mark.parametrize("role_tag, theme_tag", COLLIDING_TAGS, ids=lambda t: t.name)
def test_missing_tags_still_reports_the_unregistered_half(
    anchors_registry, role_tag, theme_tag
):
    """Anchoring the role must not make the same-named theme look anchored."""
    register(ladder(role_tag))
    outstanding = missing_tags()

    assert role_tag not in [t for t in outstanding if isinstance(t, Role)]
    assert theme_tag in [t for t in outstanding if isinstance(t, Theme)]


@pytest.mark.parametrize("role_tag, theme_tag", COLLIDING_TAGS, ids=lambda t: t.name)
def test_rendered_prompt_distinguishes_the_two_blocks(
    anchors_registry, role_tag, theme_tag
):
    """Two blocks headed `Lifegain:` would be ambiguous in the prompt itself,
    not only in the registry — the heading carries the kind for that reason."""
    register(ladder(role_tag))
    register(ladder(theme_tag))
    rendered = render_anchors()

    assert f"{role_tag.value} (role):" in rendered
    assert f"{theme_tag.value} (theme):" in rendered


def test_registering_one_tag_twice_raises(anchors_registry):
    register(ladder(Role.RAMP))
    with pytest.raises(ValueError, match="already has a ladder"):
        register(ladder(Role.RAMP))


def test_kind_of_reports_the_vocabulary_a_tag_belongs_to():
    assert kind_of(Role.LIFEGAIN) == "role"
    assert kind_of(Theme.LIFEGAIN) == "theme"
