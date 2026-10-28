"""`CardMetadataBlueprint`'s shape, and the one thing about it that is ordered.

`mechanics` is the blueprint's first field and that is not cosmetic. Both
backends decode against a model's JSON Schema — `AnthropicMetadataBackend`
hands the class to the SDK as `output_format`, `OllamaMetadataBackend` passes
`model_json_schema()` as a decoding grammar — and a JSON object comes back in
property order. First means the model writes a literal reading of the oracle
text before it assigns a role, a theme or a `power_rating`; anywhere else in
the class and it becomes a restatement of conclusions it was supposed to
inform. A reordering refactor wouldn't fail any other test in this suite, so
it fails this one.

Generation now takes **two** calls, so the chain is split across
`MetadataPassOne` and `MetadataPassTwo` and reassembled by `from_passes`. The
blueprint keeps its exact nine-property order anyway, because it inherits the
first seven rather than restating them. The tests below pin both halves: the
order of the whole, and the agreement between the parts and the whole.
"""

import pytest
from pydantic import ValidationError

from knowledge_pipeline.metadata_generator.prompt import build_system_prompt
from knowledge_pipeline.metadata_generator.schema import (
    MAX_TAGS_PER_CARD,
    CardMetadataBlueprint,
    GameStageProfile,
    MetadataPassOne,
    MetadataPassTwo,
    Role,
    SynergyTag,
    Theme,
)

# The reasoning chain, spelled out. `docs/data-model.md#cardmetadata` explains
# what each step is answered against; this is the machine-checkable copy.
EXPECTED_FIELD_ORDER = [
    "mechanics",
    "synergy_tags",
    "roles",
    "primary_role",
    "themes",
    "strengths",
    "weaknesses",
    "game_stage",
    "power_rating",
    "summary",
]

# Per-field `maxItems`, checked exactly rather than as a floor. A floor is the
# wrong assertion for a decoding guard: the ceiling only does its job if it is
# low enough to bind, and `roles` came down from 8 to 5 precisely because 8 was
# loose enough for a 14B to emit eight roles for one card without tripping it.
EXPECTED_LIST_CEILINGS = {
    "roles": MAX_TAGS_PER_CARD,
    "themes": MAX_TAGS_PER_CARD,
    "synergy_tags": 12,
    "strengths": 8,
    "weaknesses": 8,
}


def make_blueprint(**overrides) -> CardMetadataBlueprint:
    values = {
        "mechanics": (
            "Costs {1} to cast. It is an artifact. Tap it to add {C}{C} to "
            "your mana pool."
        ),
        "summary": "A one-mana rock that pays for itself immediately.",
        # Required-but-nullable: explicit `None` is what a model that declined
        # to name a primary looks like, and the validator then takes
        # `roles[0]` -- so the tests below that assert on `roles` ordering
        # exercise the fallback rather than a reordering by this fixture.
        "primary_role": None,
        "roles": [Role.MANA_ROCK],
        "themes": [Theme.ARTIFACTS],
        "game_stage": GameStageProfile(early=10, mid=7, late=4),
        "power_rating": 9.5,
        "strengths": ["Nets mana the turn after it lands"],
        "weaknesses": ["Does nothing on an empty board"],
        "synergy_tags": [SynergyTag.ARTIFACT_MATTERS],
    }
    values.update(overrides)
    return CardMetadataBlueprint(**values)


def test_mechanics_is_the_first_field_in_the_schema():
    """Generation order is property order, so this is the reasoning guard."""
    properties = list(CardMetadataBlueprint.model_json_schema()["properties"])

    assert properties[0] == "mechanics"
    assert properties.index("mechanics") < properties.index("power_rating")


def test_the_whole_reasoning_chain_is_in_order():
    """The two-call split must not reorder the finished shape.

    `CardMetadataBlueprint` inherits seven fields and declares two. Pydantic
    lists inherited fields first — but that is a property of the base class
    order, and multiple inheritance or a reordered base would silently invert
    it. The test above catches `mechanics` moving; this one catches every
    other step moving.
    """
    properties = list(CardMetadataBlueprint.model_json_schema()["properties"])

    assert properties == EXPECTED_FIELD_ORDER


def test_the_two_passes_partition_the_blueprint_exactly():
    """Every field belongs to exactly one pass, and the passes concatenate.

    A field in neither pass is never generated; a field in both is generated
    twice, and pass two would be free to overwrite a tag its own calibration
    ladders were chosen from.
    """
    pass_one = list(MetadataPassOne.model_fields)
    pass_two = list(MetadataPassTwo.model_fields)

    assert pass_one + pass_two == EXPECTED_FIELD_ORDER
    assert not set(pass_one) & set(pass_two)


def test_pass_twos_fields_are_declared_identically_on_the_blueprint():
    """`power_rating`/`summary` are written out twice; they must not drift.

    `CardMetadataBlueprint` restates these rather than inheriting them, to
    keep them *after* the pass-one fields (see the comment on that class).
    The cost of that choice is two declarations of the same constraints, so
    the constraints are compared here rather than trusted.
    """
    for name in MetadataPassTwo.model_fields:
        pass_two_field = MetadataPassTwo.model_fields[name]
        blueprint_field = CardMetadataBlueprint.model_fields[name]

        assert pass_two_field.annotation == blueprint_field.annotation, name
        assert pass_two_field.metadata == blueprint_field.metadata, name


def test_from_passes_rebuilds_the_blueprint():
    original = make_blueprint()
    one = MetadataPassOne(**original.model_dump(include=set(MetadataPassOne.model_fields)))
    two = MetadataPassTwo(**original.model_dump(include=set(MetadataPassTwo.model_fields)))

    assert CardMetadataBlueprint.from_passes(one, two) == original


def test_from_passes_still_validates():
    """The merge is the one place a field can go missing, so it re-checks."""
    one = MetadataPassOne(
        mechanics="Tap to add {C}.",
        # Spelled out because none of the four has a default any more: empty
        # is a legal answer, omitting the field is not. See the comment on
        # `MetadataPassOne`.
        synergy_tags=[],
        roles=[],
        primary_role=None,
        themes=[],
        strengths=["Cheap"],
        weaknesses=["Colorless"],
        game_stage=GameStageProfile(early=5, mid=5, late=5),
    )

    with pytest.raises(ValidationError):
        CardMetadataBlueprint.from_passes(
            one, MetadataPassTwo.model_construct(power_rating=99, summary="")
        )


def test_the_closed_vocabulary_lists_are_bounded_in_the_schema():
    """`maxItems` reaches the decoding grammar, which is the point.

    These lists become a grammar the model decodes against. Unbounded, a small
    model at temperature 0 can loop emitting tags until it hits the token
    ceiling, and the card fails to parse at all — observed on a real card once
    the anchor block left the prompt (`docs/lessons-learned.md`). The ceiling
    has to be in the schema, not merely checked afterwards, or the grammar
    never forbids the loop.
    """
    properties = CardMetadataBlueprint.model_json_schema()["properties"]

    for field, ceiling in EXPECTED_LIST_CEILINGS.items():
        assert properties[field]["maxItems"] == ceiling, field


@pytest.mark.parametrize("model", [MetadataPassOne, CardMetadataBlueprint])
def test_every_property_is_required_in_the_schema(model):
    """`required` is the only thing that makes a key *appear* in the output.

    A property absent from `required` is, in a decoding grammar, a branch the
    model may skip entirely — and both local tiers took that branch the moment
    the tag fields were given defaults: no `roles`, `themes` or `synergy_tags`
    key at all on 30 of 30 cards for the 7B and 12 of 30 for the 14B, with
    Pydantic's own defaults then filling `[]` so the store recorded a confident
    empty answer the model never gave (`docs/lessons-learned.md`).

    Emptiable is not the same as omittable, and only `min_length`/`max_length`
    should decide the first. So this asserts the stronger, simpler property:
    every field of both models is required, whatever its contents may be.
    """
    schema = model.model_json_schema()

    assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize("field", ["roles", "themes", "synergy_tags"])
def test_a_required_tag_field_still_accepts_an_empty_answer(field):
    """The other half of the pair above: required must not mean non-empty.

    A card legitimately has no roles — a basic land, a blank-ish vanilla
    creature — and forcing a value out of a closed vocabulary would be worse
    than an empty list. The model has to *write the key*; `[]` is a valid
    thing to write in it.
    """
    blueprint = make_blueprint(**{field: []})

    assert getattr(blueprint, field) == []


def test_a_card_with_no_roles_keeps_a_null_primary_role():
    """`primary_role` is required and nullable, and null has to survive.

    Passing `None` alongside non-empty `roles` is not this case: the validator
    reads emission order and backfills, which is tested above. The case here is
    a card with nothing to name — where a non-null primary would be invented.
    """
    blueprint = make_blueprint(primary_role=None, roles=[])

    assert blueprint.primary_role is None
    assert blueprint.roles == []


@pytest.mark.parametrize("field", ["roles", "themes", "synergy_tags"])
def test_duplicate_tags_are_dropped(field):
    """A card does not have the same theme twice; a looping model says it does."""
    value = {
        "roles": [Role.MANA_ROCK, Role.REMOVAL, Role.MANA_ROCK],
        "themes": [Theme.BIG_MANA, Theme.LANDS_MATTER, Theme.BIG_MANA],
        "synergy_tags": [
            SynergyTag.LANDFALL,
            SynergyTag.ETB_TRIGGER,
            SynergyTag.LANDFALL,
        ],
    }[field]

    blueprint = make_blueprint(**{field: value})

    assert getattr(blueprint, field) == value[:2]


def test_deduplication_keeps_the_models_own_ordering():
    """`anchors.ladders_for` reads that order to pick which ladders to show,
    so a `set` would throw away the signal the selection depends on."""
    blueprint = make_blueprint(roles=[Role.REMOVAL, Role.MANA_ROCK])

    assert blueprint.roles == [Role.REMOVAL, Role.MANA_ROCK]


def test_an_unnamed_primary_role_falls_back_to_the_models_own_first_choice():
    """Emission order was the ranking signal before this field existed.

    A model that leaves `primary_role` null has still ordered its `roles`, so
    the fallback keeps the card calibrated rather than dropping it to the
    no-ladder path.
    """
    blueprint = make_blueprint(
        roles=[Role.REMOVAL, Role.CARD_DRAW], primary_role=None
    )

    assert blueprint.primary_role is Role.REMOVAL


def test_no_roles_means_no_primary_role():
    """A basic land has neither, and nothing should invent one for it."""
    blueprint = make_blueprint(roles=[], primary_role=None)

    assert blueprint.primary_role is None
    assert blueprint.ladder_tags() == [Theme.ARTIFACTS]


def test_the_primary_role_is_moved_to_the_front_of_roles():
    """`roles[0] == primary_role` is the invariant that keeps the row honest."""
    blueprint = make_blueprint(
        roles=[Role.CARD_DRAW, Role.REMOVAL, Role.TUTOR],
        primary_role=Role.TUTOR,
    )

    assert blueprint.roles == [Role.TUTOR, Role.CARD_DRAW, Role.REMOVAL]


def test_a_primary_role_missing_from_roles_is_added_rather_than_dropped():
    """Nothing in the grammar can make the model pick from its own array.

    `primary_role` decodes against the whole `Role` vocabulary, so it can name
    a role the card was not given. The model plainly believes the card has it;
    discarding either answer loses information that the merge can keep.
    """
    blueprint = make_blueprint(roles=[Role.CARD_DRAW], primary_role=Role.TUTOR)

    assert blueprint.primary_role is Role.TUTOR
    assert blueprint.roles == [Role.TUTOR, Role.CARD_DRAW]


def test_promoting_an_outside_primary_role_cannot_overflow_the_ceiling():
    """`max_length` has already run by the time the promotion happens."""
    roles = [
        Role.CARD_DRAW,
        Role.REMOVAL,
        Role.BOUNCE,
        Role.COUNTERSPELL,
        Role.ANTHEM,
    ]
    assert len(roles) == MAX_TAGS_PER_CARD

    blueprint = make_blueprint(roles=roles, primary_role=Role.TUTOR)

    assert len(blueprint.roles) == MAX_TAGS_PER_CARD
    assert blueprint.roles[0] is Role.TUTOR
    # The tail is what gets dropped: the role the model ranked last.
    assert Role.ANTHEM not in blueprint.roles


def test_ladder_tags_lead_with_the_primary_role_then_roles_then_themes():
    """The order `anchors.ladders_for` truncates, so it decides what is shown."""
    blueprint = make_blueprint(
        roles=[Role.CARD_DRAW, Role.REMOVAL],
        primary_role=Role.REMOVAL,
        themes=[Theme.ARTIFACTS],
    )

    assert blueprint.ladder_tags() == [
        Role.REMOVAL,
        Role.REMOVAL,
        Role.CARD_DRAW,
        Theme.ARTIFACTS,
    ]


def test_primary_role_reaches_the_card_metadata_row():
    row = make_blueprint(
        roles=[Role.MANA_ROCK], primary_role=Role.MANA_ROCK
    ).to_card_metadata_row("oracle-1")

    assert row["primary_role"] == "Mana Rock"
    # A plain `str`, not the `StrEnum` member — which compares equal to it, so
    # the assertion above would pass either way. The column is `Mapped[str]`.
    assert type(row["primary_role"]) is str

    roleless = make_blueprint(roles=[], primary_role=None)

    assert roleless.to_card_metadata_row("oracle-2")["primary_role"] is None


def test_an_absurd_number_of_tags_is_refused():
    with pytest.raises(ValidationError):
        make_blueprint(strengths=[f"strength {i}" for i in range(20)])


@pytest.mark.parametrize(
    ("field", "vocabulary"), [("roles", Role), ("themes", Theme)]
)
def test_the_tag_ceiling_actually_binds(field, vocabulary):
    """Distinct members, so deduplication cannot be what refuses this."""
    too_many = list(vocabulary)[: MAX_TAGS_PER_CARD + 1]
    assert len(too_many) == MAX_TAGS_PER_CARD + 1

    with pytest.raises(ValidationError):
        make_blueprint(**{field: too_many})


def test_mechanics_is_required_and_cannot_be_empty():
    """An empty restatement is the one failure mode worth refusing outright —
    a model that skipped the reading step still answers every field below it."""
    with pytest.raises(ValidationError):
        make_blueprint(mechanics="")

    with pytest.raises(ValidationError):
        CardMetadataBlueprint(
            summary="No mechanics at all.",
            game_stage=GameStageProfile(early=1, mid=1, late=1),
            power_rating=1,
            strengths=["None"],
            weaknesses=["None"],
        )


def test_mechanics_reaches_the_card_metadata_row():
    blueprint = make_blueprint()

    row = blueprint.to_card_metadata_row("oracle-1")

    assert row["mechanics"] == blueprint.mechanics
    assert row["card_id"] == "oracle-1"


def test_the_rubric_tells_the_model_what_mechanics_is_for():
    """The field is useless without the instruction — a str field named
    `mechanics` and no rubric entry gets a second summary."""
    system = build_system_prompt()

    assert "`mechanics`" in system
    assert "restatement" in system
