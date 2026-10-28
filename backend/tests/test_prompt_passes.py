"""The two-pass prompts: what each pass is shown, and what it is not.

The whole point of splitting generation in two is *subtraction* — pass one
never sees an anchor ladder, and pass two sees only the ladders for the tags
pass one assigned. Both halves of that are asserted here, because neither
fails loudly in production: a prompt that carries too much still returns valid
JSON, just with `power_rating` flattened into a band around 7, which is
exactly the bug this replaced (`docs/lessons-learned.md`).

`ANCHORS` is a module-global mutable dict, so every test that populates it
goes through `anchors_registry`, the same fixture `test_anchors.py` uses and
for the same reason.
"""

import hashlib

import pytest

from knowledge_pipeline.metadata_generator import anchors, prompt
from knowledge_pipeline.metadata_generator.anchors import (
    BAND_ORDER,
    AnchorCard,
    AnchorLadder,
    register,
)
from knowledge_pipeline.metadata_generator.prompt import (
    MAX_LADDERS_PER_CARD,
    build_pass_one_system_prompt,
    build_pass_two_system_prompt,
    render_rating_request,
)
from knowledge_pipeline.metadata_generator.schema import (
    GameStageProfile,
    MetadataPassOne,
    Role,
    SynergyTag,
    Theme,
)

CARD_FACTS = "Name: Test Card\nMana value: 2\nOracle text:\nDo a thing."


@pytest.fixture
def anchors_registry():
    """`anchors.ANCHORS`, emptied for the test and restored afterwards."""
    snapshot = dict(anchors.ANCHORS)
    anchors.ANCHORS.clear()
    try:
        yield anchors.ANCHORS
    finally:
        anchors.ANCHORS.clear()
        anchors.ANCHORS.update(snapshot)


def ladder_for_tag(tag: Role | Theme) -> AnchorLadder:
    """A complete five-rung ladder whose rung names name their own tag.

    Naming the rungs after the tag is what lets a test assert *which* ladders
    were selected, rather than merely how many.
    """
    return AnchorLadder(
        tag=tag,
        rungs=[
            AnchorCard(
                band=band,
                name=f"{tag.name} rung {band.value}",
                power_rating=band.bounds[0],
                note=f"Illustrates {band.value} for {tag.value}.",
            )
            for band in BAND_ORDER
        ],
    )


def pass_one(
    *,
    roles: list[Role] | None = None,
    themes: list[Theme] | None = None,
    primary_role: Role | None = None,
) -> MetadataPassOne:
    return MetadataPassOne(
        mechanics="Costs {1}. Tap: add {C}{C}.",
        synergy_tags=[SynergyTag.ARTIFACT_MATTERS],
        roles=roles or [],
        primary_role=primary_role,
        themes=themes or [],
        strengths=["Nets mana immediately"],
        weaknesses=["Does nothing alone"],
        game_stage=GameStageProfile(early=9, mid=6, late=3),
    )


# ------------------------------------------------------------- pass one ---


def test_pass_one_carries_no_anchor_ladders():
    """The subtraction that the whole change exists to make."""
    system = build_pass_one_system_prompt()

    assert "Calibration ladders" not in system
    assert "power_rating=" not in system


def test_pass_one_carries_the_full_closed_vocabulary():
    """Pass one is where tags are assigned, so it needs every member."""
    system = build_pass_one_system_prompt()

    assert "Valid `roles` values:" in system
    assert "Valid `themes` values:" in system
    assert "Valid `synergy_tags` values:" in system
    assert Role.MANA_FIXING.value in system
    assert Theme.ENCHANTRESS.value in system
    assert SynergyTag.PROLIFERATE.value in system


def test_pass_one_still_explains_what_mechanics_is_for():
    """Same guard as `test_metadata_schema.py`, moved to the pass that owns it."""
    system = build_pass_one_system_prompt()

    assert "`mechanics`" in system
    assert "restatement" in system


def test_pass_one_does_not_ask_for_the_fields_it_cannot_answer():
    system = build_pass_one_system_prompt()

    assert "`power_rating`" not in system
    assert "`summary`" not in system


def test_pass_one_is_much_smaller_than_the_one_pass_prompt():
    """The size claim, asserted rather than described.

    The one-pass block is ~49,000 characters, almost all of it ladders. If
    pass one ever creeps back toward that, the dilution is back.
    """
    assert len(build_pass_one_system_prompt()) < len(prompt.build_system_prompt()) / 5


# ------------------------------------------------------------- pass two ---


def test_pass_two_system_block_is_fixed_and_ladder_free():
    """The ladders are per-card data and belong in the user message.

    A system block that varied per card would miss Anthropic's prompt cache
    and break llama.cpp's KV-prefix reuse on every call
    (`docs/benchmarking-and-testing.md`).
    """
    system = build_pass_two_system_prompt()

    assert "Calibration ladders" not in system
    assert "`power_rating`" in system
    assert "`summary`" in system
    assert build_pass_two_system_prompt() == system


def test_pass_two_is_shown_only_the_ladders_for_its_own_tags(anchors_registry):
    register(ladder_for_tag(Role.MANA_ROCK))
    register(ladder_for_tag(Role.REMOVAL))
    register(ladder_for_tag(Theme.ARTIFACTS))

    message = render_rating_request(
        card_facts=CARD_FACTS, pass_one=pass_one(roles=[Role.MANA_ROCK])
    )

    assert "MANA_ROCK rung 1-2" in message
    assert "REMOVAL" not in message
    assert "ARTIFACTS" not in message


def test_pass_two_carries_the_card_and_pass_ones_answers(anchors_registry):
    register(ladder_for_tag(Role.MANA_ROCK))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(roles=[Role.MANA_ROCK], themes=[Theme.ARTIFACTS]),
    )

    assert CARD_FACTS in message
    assert "Costs {1}. Tap: add {C}{C}." in message
    assert Role.MANA_ROCK.value in message
    assert Theme.ARTIFACTS.value in message
    assert "Nets mana immediately" in message
    assert "Does nothing alone" in message
    assert "early=9.0" in message


def test_roles_are_offered_before_themes(anchors_registry):
    """A role is what the card *does* — closest to its strongest effect."""
    register(ladder_for_tag(Theme.ARTIFACTS))
    register(ladder_for_tag(Role.MANA_ROCK))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(roles=[Role.MANA_ROCK], themes=[Theme.ARTIFACTS]),
    )

    assert message.index("MANA_ROCK rung") < message.index("ARTIFACTS rung")


def test_a_heavily_tagged_card_is_capped(anchors_registry):
    """Seven ladders is most of the way back to the dilution being fixed."""
    roles = [Role.MANA_ROCK, Role.REMOVAL, Role.CARD_DRAW, Role.TUTOR]
    themes = [Theme.ARTIFACTS, Theme.TOKENS, Theme.STAX]
    for tag in (*roles, *themes):
        register(ladder_for_tag(tag))

    message = render_rating_request(
        card_facts=CARD_FACTS, pass_one=pass_one(roles=roles, themes=themes)
    )

    shown = [tag for tag in (*roles, *themes) if f"{tag.name} rung" in message]
    assert len(shown) == MAX_LADDERS_PER_CARD
    # The cap takes from the front, and roles come first.
    assert shown == roles[:MAX_LADDERS_PER_CARD]


def test_the_primary_roles_ladder_survives_the_cap(anchors_registry):
    """The reason `primary_role` exists, stated as the thing that can break.

    Four anchored roles, three slots, and the one the model named as the
    card's defining role emitted last. Under emission order its ladder is the
    one cut; the whole point of asking for a primary is that it cannot be.
    """
    roles = [Role.MANA_ROCK, Role.CARD_DRAW, Role.BOUNCE, Role.TUTOR]
    for tag in roles:
        register(ladder_for_tag(tag))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(roles=roles, primary_role=Role.TUTOR),
    )

    assert "TUTOR rung 1-2" in message
    assert "BOUNCE rung" not in message


def test_the_primary_roles_ladder_is_shown_first(anchors_registry):
    """Pass two's rubric tells the model the first ladder is the primary's."""
    register(ladder_for_tag(Role.MANA_ROCK))
    register(ladder_for_tag(Role.REMOVAL))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(
            roles=[Role.MANA_ROCK, Role.REMOVAL], primary_role=Role.REMOVAL
        ),
    )

    assert message.index("REMOVAL rung") < message.index("MANA_ROCK rung")
    # The order is only useful if pass two is told what it means.
    assert "the first is the one for the card's primary" in (
        build_pass_two_system_prompt()
    )


def test_the_primary_role_is_named_in_pass_twos_message(anchors_registry):
    """Shown to the model, not merely used to order the ladders behind it."""
    register(ladder_for_tag(Role.REMOVAL))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(
            roles=[Role.MANA_ROCK, Role.REMOVAL], primary_role=Role.REMOVAL
        ),
    )

    assert f"Primary role: {Role.REMOVAL.value}" in message


def test_a_roleless_card_reports_no_primary_role(anchors_registry):
    register(ladder_for_tag(Theme.ARTIFACTS))

    message = render_rating_request(
        card_facts=CARD_FACTS, pass_one=pass_one(themes=[Theme.ARTIFACTS])
    )

    assert "Primary role: (none)" in message
    assert "ARTIFACTS rung 1-2" in message


def test_pass_one_asks_for_a_primary_role_without_explaining_its_use(
    anchors_registry,
):
    """Both halves matter, and the second is a scar.

    The field needs a rubric entry or it gets answered arbitrarily. But a
    pass-one bullet explaining that these tags *select pass two's ladders*
    was measured making tagging dramatically worse — Sol Ring went from one
    correct theme to five (`docs/lessons-learned.md`). Pass one is told what
    to write, never what the next call will do with it.

    Asserted against the rubric rather than the whole block: the `game_stage`
    profiles that follow it use the word "ladder" to say they are *not* one.
    """
    assert "`primary_role`" in build_pass_one_system_prompt()
    assert "ladder" not in prompt._PASS_ONE_RUBRIC.lower()
    assert "power_rating" not in prompt._PASS_ONE_RUBRIC


def test_an_unanchored_tag_does_not_consume_a_ladder_slot(anchors_registry):
    """Only anchored tags count toward the cap — otherwise a card with three
    unanchored roles would be shown nothing while ladders for its themes
    sat unused."""
    register(ladder_for_tag(Theme.ARTIFACTS))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(
            roles=[Role.MANA_ROCK, Role.REMOVAL], themes=[Theme.ARTIFACTS]
        ),
    )

    assert "ARTIFACTS rung 1-2" in message


def test_a_card_with_no_matching_ladder_is_told_so(anchors_registry):
    """No ladders rather than all 66 — the fallback must not reopen the bug."""
    register(ladder_for_tag(Role.REMOVAL))

    message = render_rating_request(
        card_facts=CARD_FACTS, pass_one=pass_one(roles=[Role.MANA_ROCK])
    )

    assert "No calibration ladder covers this card" in message
    assert "Calibration ladders" not in message
    assert "REMOVAL rung" not in message


def test_a_card_with_no_tags_at_all_is_told_so(anchors_registry):
    register(ladder_for_tag(Role.MANA_ROCK))

    message = render_rating_request(card_facts=CARD_FACTS, pass_one=pass_one())

    assert "No calibration ladder covers this card" in message
    assert "MANA_ROCK rung" not in message


def test_the_role_theme_name_collision_yields_two_ladders(anchors_registry):
    """`Role.LIFEGAIN == Theme.LIFEGAIN` is `True` for a `StrEnum`.

    Deduplicating by the tag rather than by `anchor_key` would silently show
    one ladder where the card earned two — the same collision documented on
    `anchors.anchor_key`.
    """
    register(ladder_for_tag(Role.LIFEGAIN))
    register(ladder_for_tag(Theme.LIFEGAIN))

    message = render_rating_request(
        card_facts=CARD_FACTS,
        pass_one=pass_one(roles=[Role.LIFEGAIN], themes=[Theme.LIFEGAIN]),
    )

    assert "(role)" in message
    assert "(theme)" in message


# ------------------------------------------------------- the frozen base ---


def test_the_one_pass_rubric_is_unchanged_by_the_split():
    """`_RUBRIC` is a frozen artifact, not live prompt text.

    `benchmark/runner.py` records `build_system_prompt()` on a session and
    refuses to resume one whose SHA-256 has moved, and the recorded S-001
    numbers are the baseline the two-pass distribution is compared against.
    Editing this string strands both. Edit `_PASS_ONE_RUBRIC` /
    `_PASS_TWO_RUBRIC` instead; delete this test together with `_RUBRIC` when
    the harness stops measuring it.
    """
    digest = hashlib.sha256(prompt._RUBRIC.encode("utf-8")).hexdigest()

    assert digest == (
        "cd13fdb650017fe2a794a3d7ed8d5061cf314b3cfacd94ae6fae228e157346e5"
    )
