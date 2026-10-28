"""Blueprint for AI-generated `CardMetadata` (`docs/data-model.md#cardmetadata`).

This module owns the *shape* a metadata-generation call must produce — the
same role `scryfall_importer/mapping.py`'s `CardRow` plays for `Card`: a
validated, Python-side representation that sits between raw model output and
the `CardMetadata` row in `database/knowledge/models.py`. It is not that
SQLAlchemy row itself — those columns are plain strings/JSON, since that's
what Alembic diffs cleanly — this is what generation actually produces and
what the generator validates before writing that row.

Generation takes **two** calls per card, and this module owns both halves:
`MetadataPassOne` (`mechanics` … `game_stage`) and `MetadataPassTwo`
(`power_rating`, `summary`), merged by `CardMetadataBlueprint.from_passes`.
The split exists so pass two can be shown only the anchor ladders matching
the roles and themes pass one assigned, instead of all 66 every time — see
`docs/knowledge-pipeline.md#metadata-generation` and `docs/lessons-learned.md`.

`Role`, `Theme`, and `SynergyTag` are closed enums, per
`docs/data-model.md#controlled-vocabulary-roles-themes-synergy_tags` — the
model is shown these exact members in the prompt and must choose only from
them, so retrieval filtering and the deck builder's role-based grouping never
see near-duplicate strings ("Ramp" vs. "Mana Ramp") for the same concept.

How a tag is sorted into one of the three:

  Role        what the card DOES (a producer of an effect)
  Theme       the strategy a card is an engine or payoff for
  SynergyTag  a narrow hook: a specific trigger or text pattern the card
              cares about or enables

Deliberately NOT in these enums: anything derivable from Scryfall's own
fields (type line, `keywords`, mana cost, `produced_mana`, oracle text
regexes). Derive those in the ingest step instead of asking the LLM —
`knowledge_pipeline/derived_tags.py` is where that rule is implemented:

  - Aura / Equipment / Vehicle / Saga as card types       -> type_line
  - evasion (flying, menace, trample...), haste, deathtouch -> keywords[]
  - X spell                                                -> "{X}" in mana_cost
  - "adds one mana of any color in your commander's color
    identity" (formerly SynergyTag.COLOR_IDENTITY_FIXING)  -> oracle regex
  - tribes: store as list[str] validated against Scryfall's
    /catalog/creature-types, rather than an enum. Theme.TRIBAL stays as the
    boolean "this card cares about creature types".

Combos are a relation between cards, not a property of one card. Join
Commander Spellbook's combo data on card ids instead of tagging.

The members below are a first-draft vocabulary, not a ratified taxonomy —
see `docs/data-model.md#anchor-cards`. Adding or renaming a `Role`/`Theme`
member later requires building it a full five-rung anchor ladder before it
can be trusted at the same 1-10 scale as the rest — not one example card, a
card per power band — and is a deliberate, reviewed edit for that reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, Field, field_validator, model_validator

# The ceiling on `roles` and `themes`, and the one place it is written down.
#
# It is a decoding guard first: these lists become a grammar the model decodes
# against, and an unbounded `list[enum]` gives it no reason to ever close the
# array. A small model at temperature 0 can fall into emitting `"Big Mana",
# "Lands Matter", "Big Mana", ...` until it hits the token ceiling, which lands
# as a JSON parse failure for the whole card; `maxItems` in the schema makes the
# grammar itself forbid that (`docs/lessons-learned.md`).
#
# Five rather than the eight it was first set at. Eight was chosen to bound
# garbage without ever truncating a real answer, but a measurement on eight hard
# cards found the 14B emitting a full eight roles for a single modal Equipment —
# so the ceiling was loose enough that inflation fit comfortably underneath it.
# Five is still above any card's honest role count and now costs something to
# reach.
MAX_TAGS_PER_CARD = 5


def utcnow() -> datetime:
    """Naive UTC now, matching `scryfall_importer/mapping.py`'s convention.

    Timestamp columns are plain `DateTime` (SQLite has no real timezone
    support), so every datetime reaching the database is normalized here.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(StrEnum):
    """What a card *does* functionally. Closed vocabulary."""

    # --- Mana ---------------------------------------------------------
    # RAMP is split four ways. Rocks, dorks and land ramp die to different
    # things and feed different themes (Artifacts / wipes / Landfall).
    MANA_ROCK = "Mana Rock"
    MANA_DORK = "Mana Dork"
    LAND_RAMP = "Land Ramp"  # fetches or puts lands onto the battlefield
    RITUAL = "Ritual"  # one-shot burst mana; does not count toward ramp
    MANA_FIXING = "Mana Fixing"

    # --- Card flow ----------------------------------------------------
    CARD_DRAW = "Card Draw"  # real card advantage: one card becomes more
    IMPULSE_DRAW = "Impulse Draw"  # exile-and-may-play; expires, so not true draw
    CARD_SELECTION = "Card Selection"  # scry / surveil / loot: quality, not quantity
    TUTOR = "Tutor"

    # --- Interaction --------------------------------------------------
    REMOVAL = "Removal"  # targeted, creature / planeswalker
    ARTIFACT_ENCHANTMENT_REMOVAL = "Artifact/Enchantment Removal"
    BOUNCE = "Bounce"
    BOARD_WIPE = "Board Wipe"
    COUNTERSPELL = "Counterspell"
    PROTECTION = "Protection"
    GRAVEYARD_HATE = "Graveyard Hate"
    HAND_DISRUPTION = "Hand Disruption"
    STAX_PIECE = "Stax Piece"
    LAND_DESTRUCTION = "Land Destruction"
    THEFT = "Theft Effect"

    # --- Board and graveyard development -------------------------------
    RECURSION = "Recursion"  # graveyard -> hand
    REANIMATION = "Reanimation"  # graveyard -> battlefield
    TOKEN_GENERATOR = "Token Generator"
    CLONE = "Clone"  # copies a creature or permanent
    ANTHEM = "Anthem"
    # Value renamed so it no longer collides with Theme.LIFEGAIN ("Lifegain").
    LIFEGAIN = "Lifegain Source"

    # --- Finishing ----------------------------------------------------
    # WIN_CONDITION is dropped for the same reason as COMBO_PIECE: what
    # counts as "the win" depends on the deck. These four are decidable
    # from the card.
    ALT_WIN_CONDITION = "Alternate Win Condition"  # "you win" / "target player loses"
    MASS_PUMP = "Mass Pump"  # Overrun effects
    LIFE_DRAIN = "Life Drain"  # each opponent loses life
    DIRECT_DAMAGE = "Direct Damage"  # burn to face or creatures
    EXTRA_COMBAT = "Extra Combat"
    EXTRA_TURNS = "Extra Turns"

    # Dropped: COMBO_PIECE (see module docstring), WIN_CONDITION (above),
    # RAMP (split above), EQUIPMENT and AURA (card types; read the type line).


class Theme(StrEnum):
    """What *strategy or archetype* a card supports. Closed vocabulary."""

    ARISTOCRATS = "Aristocrats"
    VOLTRON = "Voltron"
    SPELLSLINGER = "Spellslinger"
    TOKENS = "Tokens"
    # Replaces LANDFALL. EDHREC's "Lands Matter" has ~65K decks to Landfall's
    # ~23K. Landfall itself moves down to SynergyTag.LANDFALL as the hook.
    LANDS_MATTER = "Lands Matter"
    BIG_MANA = "Big Mana"
    REANIMATOR = "Reanimator"
    GROUP_HUG = "Group Hug"
    GROUP_SLUG = "Group Slug"
    STAX = "Stax"
    PILLOW_FORT = "Pillow Fort"  # attack taxes and deterrents
    ARTIFACTS = "Artifacts"
    ENCHANTRESS = "Enchantress"
    AURAS = "Auras"  # aura-voltron / bogles, distinct from Enchantress
    TRIBAL = "Tribal"
    COUNTERS = "+1/+1 Counters"
    MINUS_COUNTERS = "-1/-1 Counters"
    SUPERFRIENDS = "Superfriends"
    STORM = "Storm"
    WHEEL = "Wheel"
    BLINK = "Blink"
    GRAVEYARD_VALUE = "Graveyard Value"
    SELF_MILL = "Self-Mill"
    MILL = "Mill"
    LIFEGAIN = "Lifegain"
    EQUIPMENT = "Equipment"
    VEHICLES = "Vehicles"
    FORCED_COMBAT = "Forced Combat"  # goad and must-attack effects
    # Infect / Toxic — decidable from `keywords`, not asked of the model.
    # See `DERIVED_THEMES` below.
    INFECT = "Infect"
    # Named-resource themes: each is decidable from "create a ___ token" or
    # the "{E}" energy symbol and has real payoff cards. Also in
    # `DERIVED_THEMES` — `knowledge_pipeline/derived_tags.py` reads the card
    # for these, so pass one is never asked about them.
    TREASURE = "Treasure"
    FOOD = "Food"
    CLUES = "Clues"
    ENERGY = "Energy"
    DISCARD = "Discard"

    # No MIDRANGE. It was dropped rather than anchored: nothing printed on a
    # card makes it midrange — the word describes a deck's posture across a
    # whole game, so any ladder for it grades cards by a property they don't
    # individually have. A closed vocabulary only earns its strictness if every
    # member is decidable from the card in front of the model.
    #
    # No CONTROL and no AGGRO, for the same reason (judgment call: restore
    # them only if your anchor ladders make them decidable per card). Control
    # is the union of the COUNTERSPELL / BOARD_WIPE / REMOVAL roles and Aggro
    # is the union of haste / attack-trigger / anthem cards. Compute them at
    # the deck level from role counts instead of tagging individual cards.
    #
    # No SACRIFICE (judgment call): ARISTOCRATS plus
    # SynergyTag.SACRIFICE_OUTLET and DEATH_TRIGGER already cover it, and a
    # small model will tag both every time.
    #
    # No FLYING, dropped for a different reason than MIDRANGE. Flying is
    # decidable from the card -- but an anchor ladder for it grades the wrong
    # thing. Every rung that reads as a strong "flying card" is a card that
    # *grants* evasion, while the theme is supposed to collect cards that are
    # *paid off* by it, and the top of the scale has almost nothing in the
    # second group. A ladder whose rungs answer a different question than the
    # tag asks teaches the model to mislabel rather than to calibrate. Evasion
    # is better served as a property of a card than as an archetype of its own.


# `Theme` members `knowledge_pipeline/derived_tags.py` reads straight off a
# card's own `oracle_text`/`keywords` -- no model judgment involved. Still
# real `Theme` members: they keep their anchor ladder and their place in
# `CardMetadata.themes`. What changes is who asks -- `prompt.py` drops them
# from pass one's "Valid `themes` values" listing, and `pipeline.py` merges
# `derive_themes`'s output into the card's themes afterward
# (`MetadataPassOne.with_derived_themes`), so a card still gets rated against
# the Treasure ladder even though the model was never asked about Treasure.
DERIVED_THEMES = frozenset(
    {Theme.TREASURE, Theme.FOOD, Theme.CLUES, Theme.ENERGY, Theme.INFECT}
)


class SynergyTag(StrEnum):
    """Narrow mechanical hooks a card cares about — finer-grained than `Theme`."""

    # Existing, kept
    COUNTERS_MATTER = "Counters Matter"  # any counter type; +1/+1 decks use Theme.COUNTERS
    ETB_TRIGGER = "ETB Trigger"
    DEATH_TRIGGER = "Death Trigger"
    SACRIFICE_OUTLET = "Sacrifice Outlet"
    COST_REDUCTION = "Cost Reduction"
    COPY_EFFECT = "Copy Effect"  # spell, ability or permanent copying
    UNTAP_EFFECT = "Untap Effect"
    MANA_DOUBLER = "Mana Doubler"
    GRAVEYARD_FILL = "Graveyard Fill"
    ARTIFACT_MATTERS = "Artifact Matters"
    ENCHANTMENT_MATTERS = "Enchantment Matters"
    SPELLS_MATTER = "Instant/Sorcery Matters"
    X_SPELL = "X Spell"  # also derivable from mana_cost; fine to keep as a hook
    COMBAT_DAMAGE_TRIGGER = "Combat Damage Trigger"

    # Moved down from Theme
    LANDFALL = "Landfall"

    # New, from EDHREC themes with real deck counts and decidable text
    ATTACK_TRIGGER = "Attack Trigger"  # "whenever ~ attacks"; pairs with COMBAT_DAMAGE_TRIGGER
    PROLIFERATE = "Proliferate"
    LEGENDS_MATTER = "Legends Matter"  # ~33K EDHREC decks
    COMMANDER_MATTERS = "Commander Matters"  # commander tax, cast from command zone, partner
    DISCARD_OUTLET = "Discard Outlet"  # madness / reanimator enabler
    CAST_FROM_EXILE = "Cast From Exile"  # adventure / foretell / plot / suspend / impulse payoffs
    CAST_FROM_GRAVEYARD = "Cast From Graveyard"  # flashback / escape / "cast from your graveyard"
    TOPDECK_MATTERS = "Topdeck Matters"  # reveal / look at / play from top of library
    TOUGHNESS_MATTERS = "Toughness Matters"
    DEVOTION = "Devotion"
    MONARCH = "Monarch"

    # Dropped: COLOR_IDENTITY_FIXING (oracle regex at ingest; see docstring).


# Role buckets for a deck-health panel, matching the Command Zone template's
# categories (ramp, card advantage, targeted vs mass disruption).
RAMP_ROLES = frozenset({Role.MANA_ROCK, Role.MANA_DORK, Role.LAND_RAMP})
CARD_ADVANTAGE_ROLES = frozenset({Role.CARD_DRAW, Role.IMPULSE_DRAW})
TARGETED_DISRUPTION_ROLES = frozenset(
    {Role.REMOVAL, Role.ARTIFACT_ENCHANTMENT_REMOVAL, Role.COUNTERSPELL, Role.BOUNCE}
)
MASS_DISRUPTION_ROLES = frozenset({Role.BOARD_WIPE, Role.STAX_PIECE})


class GameStageProfile(BaseModel):
    """Independent early/mid/late strength scores — not a distribution.

    A card can legitimately score high on all three (Sol Ring) or low on all
    three (a narrow, situational answer). See
    `docs/data-model.md#game_stage-gamestageprofile`.
    """

    early: float = Field(ge=1, le=10)
    mid: float = Field(ge=1, le=10)
    late: float = Field(ge=1, le=10)


class MetadataPassOne(BaseModel):
    """Pass one of generation: everything decidable without an anchor ladder.

    `mechanics` through `game_stage` — the prefix of `CardMetadataBlueprint`'s
    reasoning chain that answers *what this card is* before anything asks *how
    good it is*. The cut is not arbitrary: by the end of this model the card's
    `roles` and `themes` are decided, which is exactly what
    `anchors.ladders_for` needs to pick the handful of calibration ladders
    pass two should see.

    This exists because showing all 66 ladders on every call diluted
    `power_rating` into a narrow band around 7
    (`docs/lessons-learned.md`). Splitting the call lets pass two be shown
    only the ladders for the tags this card actually has.

    `CardMetadataBlueprint` inherits from this class rather than restating its
    fields, so the two can never disagree about the shape or the order of the
    chain's first seven steps.
    """

    # First field, deliberately — see `CardMetadataBlueprint`. A literal
    # restatement of the rules text, carrying no judgment of its own.
    mechanics: str = Field(min_length=1)
    # Every `max_length` here is a decoding guard, not a modelling opinion —
    # see `MAX_TAGS_PER_CARD` for the failure it prevents and why the tag
    # ceilings came down from eight to five. `synergy_tags` keeps a looser
    # bound because it is a list of narrow hooks the rubric asks the model to
    # be exhaustive about, not a judgment it is asked to be selective about.
    #
    # **None of the four tag fields has a default, and that is a decoding
    # decision rather than a Python one.** A field with a default is absent
    # from the schema's `required` list, and `required` is the only thing that
    # makes a key *appear* — in a decoding grammar an unrequired property is a
    # branch the model may skip entirely. "A card may have zero roles" is a
    # statement about the array being empty; it is not a licence to omit the
    # key, and conflating the two cost a whole benchmark session: when these
    # three were loosened from `min_length=1` to `default_factory=list`, the
    # 7B tier emitted no `roles`, `themes` or `synergy_tags` at all on 30 of
    # 30 cards and the 14B on 12 of 30, because the shortest grammar-legal
    # path through the object had become the one that answers six fields
    # instead of ten (`docs/lessons-learned.md`). Worse, every skipped field
    # is a skipped *reasoning step*: `strengths` is defined as grounded in the
    # roles the model just assigned, and it was being written with no roles
    # ever assigned.
    #
    # Required-but-emptiable is what was wanted all along: the key must be
    # written, `[]` (or `null` for `primary_role`) is a valid thing to write
    # in it. `tests/test_metadata_schema.py` pins every property into
    # `required` so this cannot regress silently again.
    synergy_tags: list[SynergyTag] = Field(max_length=12)
    roles: list[Role] = Field(max_length=MAX_TAGS_PER_CARD)
    # The one role the card is most defined by, and the only ranking signal in
    # this model that the model itself was actually asked for. Before it,
    # `anchors.ladders_for` had to read emission order as importance — an
    # assumption nothing in the prompt ever asked the model to honour. See
    # `ladder_tags`.
    #
    # Nullable because `roles` may legitimately be empty: a basic land has no
    # role in this vocabulary, and the rubric tells the model to leave a list
    # empty rather than force an inexact match. `null` is what it writes then
    # — but it does write it, for the reason given above `synergy_tags`.
    primary_role: Role | None
    themes: list[Theme] = Field(max_length=MAX_TAGS_PER_CARD)
    strengths: list[str] = Field(min_length=1, max_length=8)
    weaknesses: list[str] = Field(min_length=1, max_length=8)
    game_stage: GameStageProfile

    @field_validator("synergy_tags", "roles", "themes")
    @classmethod
    def _drop_duplicate_tags(cls, tags: list[Any]) -> list[Any]:
        """Deduplicate a closed-vocabulary list, keeping first-seen order.

        These are sets in everything but type: a card does not have the
        "Lands Matter" theme twice, and nothing downstream — retrieval
        filtering, role-based grouping, ladder selection — means anything
        different by a repeated member.

        It is a validator rather than a `set` field because order carries
        information. The model emits its strongest read first, and
        `anchors.ladders_for` uses that order to decide which three ladders a
        card is calibrated against; a `set` would discard exactly the signal
        the selection depends on.

        Runs *after* `max_length`, which is deliberate. The ceiling exists to
        stop a repetition loop at decode time; this cleans up what such a loop
        leaves behind, and a run that trips both is still visible as a card
        with implausibly many tags rather than as a silent pass.
        """
        seen: set[str] = set()
        unique: list[Any] = []
        for tag in tags:
            if tag in seen:
                continue
            seen.add(tag)
            unique.append(tag)
        return unique

    @model_validator(mode="after")
    def _lead_with_the_primary_role(self) -> "MetadataPassOne":
        """Reconcile `primary_role` and `roles` so neither can contradict the other.

        Constrained decoding can enforce that `primary_role` is *a* member of
        the `Role` vocabulary, but not that it is one of the roles this card
        was just given — the grammar has no way to express "pick from the array
        you wrote three tokens ago". So the two arrive independently and are
        squared up here:

        - No primary named, but roles assigned: take the first. That is the
          model's own strongest-first read, which is the signal this field
          exists to replace; falling back to it keeps older behaviour rather
          than leaving the card uncalibrated.
        - A primary named that isn't in `roles`: the model clearly believes the
          card has that role, so add it rather than discard the answer, and
          move it to the front.

        The result is the invariant `roles[0] == primary_role` whenever a
        primary exists, which is what makes the stored row self-consistent —
        `ladder_tags` does not depend on it, and states the order itself.

        Prepending can push `roles` one past `MAX_TAGS_PER_CARD`, since
        `max_length` has already run by the time this does. The overflow is
        trimmed from the tail, which drops the role the model ranked last to
        keep the one it named most important.
        """
        if self.primary_role is None:
            if self.roles:
                self.primary_role = self.roles[0]
            return self

        rest = [role for role in self.roles if role != self.primary_role]
        self.roles = [self.primary_role, *rest][:MAX_TAGS_PER_CARD]
        return self

    def ladder_tags(self) -> list[Role | Theme]:
        """This card's tags, ordered for `anchors.ladders_for`.

        Primary role first, then the remaining roles, then themes. That order
        is the whole point of `primary_role`: `ladders_for` truncates to
        `prompt.MAX_LADDERS_PER_CARD`, so whichever tags lead this list are the
        ones pass two is actually calibrated against, and before this method
        the lead was emission order — a ranking the model was never asked to
        produce. Now the ladder for the role the model *said* defines the card
        is the one it cannot be cut off from.

        Roles still precede themes, unchanged: a role is what the card does,
        which is the closest thing to the "strongest effect" the rubric asks it
        to calibrate against. The primary role is repeated at the head of the
        list rather than removed from `roles`; `ladders_for` deduplicates, so
        this states the ordering explicitly instead of relying on the
        `roles[0]` invariant above holding.
        """
        primary: list[Role | Theme] = (
            [] if self.primary_role is None else [self.primary_role]
        )
        return [*primary, *self.roles, *self.themes]

    def with_derived_themes(self, derived: Iterable[Theme]) -> "MetadataPassOne":
        """Add in the `DERIVED_THEMES` `derive_themes` read off the card itself.

        Called by `pipeline.py` right after pass one returns, before anything
        downstream reads `self.themes` -- so `ladder_tags` and the stored row
        both see the merged list, and a card that creates Treasures is rated
        against the Treasure ladder even though the model was never asked
        about Treasure at all (`DERIVED_THEMES`).

        Returns a new instance rather than mutating in place, consistent with
        treating pass one as the model's own settled record
        (`prompt.render_rating_request`'s "Already established" framing).

        Derived themes are prepended, not appended: they are facts read off
        the card, not guesses, so when the merge would overflow
        `MAX_TAGS_PER_CARD` a derived theme displaces one of the model's own
        rather than the other way around. Deduplicated the same way
        `_drop_duplicate_tags` dedupes the model's own output.
        """
        merged: list[Theme] = []
        for theme in (*derived, *self.themes):
            if theme not in merged:
                merged.append(theme)
        return self.model_copy(update={"themes": merged[:MAX_TAGS_PER_CARD]})


class MetadataPassTwo(BaseModel):
    """Pass two of generation: the calibrated rating and the capstone summary.

    Deliberately *not* a subclass of anything. Pass two is handed the card plus
    pass one's finished output and asked for these two fields only — a model
    that could also emit `roles` here would be free to quietly revise the tags
    its calibration ladders were selected from, which would make the selection
    a lie.

    `summary` travels with `power_rating` rather than staying in pass one
    because it is defined as the trailing restatement of a judgment already
    made (`CardMetadataBlueprint`'s docstring). Written in pass one it would
    precede the rating it is supposed to reflect.

    The two fields are restated on `CardMetadataBlueprint` rather than
    inherited from here — see that class's note on why — and
    `tests/test_metadata_schema.py` pins the two declarations together.
    """

    power_rating: float = Field(ge=1, le=10)
    summary: str = Field(min_length=1)


class CardMetadataBlueprint(MetadataPassOne):
    """The exact shape one card's finished metadata must have.

    Deliberately excludes `card_id` and `updated_at` — a generation call is
    stateless (`docs/data-model.md#anchor-cards`) and shouldn't be trusted to
    fill in the pipeline's own bookkeeping. Use `to_card_metadata_row` to
    attach those before writing to `database.knowledge.models.CardMetadata`.

    **No longer produced by a single call.** `pipeline.py` generates
    `MetadataPassOne`, selects that card's anchor ladders from the `roles` and
    `themes` it returned, then generates `MetadataPassTwo` against them, and
    merges the two with `from_passes`. This class remains the one definition
    of the finished shape: the row writer, the database columns and every
    reader still speak in terms of it.

    **Field order is load-bearing, and it encodes a reasoning chain, not just
    `mechanics` first.** Both backends decode against a model's JSON Schema —
    the Anthropic SDK builds it from the class itself, Ollama turns it into a
    decoding grammar — and a JSON object is emitted in property order, so each
    field is answered with every prior field already written down. Splitting
    the chain across two calls preserves that property and strengthens it: the
    cut falls on a step boundary, and pass two cannot reach back and revise
    what pass one decided.

    Inheriting from `MetadataPassOne` is what keeps the order honest. Pydantic
    lists inherited fields before a subclass's own, so the nine properties come
    back in exactly the order below with the first seven defined in only one
    place:

    1. `mechanics` — a literal reading of the oracle text, no judgment yet.
    2. `synergy_tags` — the narrowest mechanical hooks, read straight off
       that restatement.
    3. `roles` — what the card functionally does, informed by the tags.
    4. `primary_role` — which of those roles defines the card, chosen while
       the list is still the last thing written and before any broader read
       has had a chance to talk the model out of it.
    5. `themes` — the archetype-level read, informed by the roles.
    6. `strengths` / `weaknesses` — qualitative evaluation, grounded in the
       roles/themes just assigned rather than the oracle text directly.
    7. `game_stage` — quantitative early/mid/late scoring.
    8. `power_rating` — a single calibrated number, placed against the
       anchor ladder for whichever role/theme this card was just given, with
       the qualitative call already on record.
    9. `summary` — the capstone. Written last on purpose: by the time the
       model reaches it, `power_rating` and every tag are already decided, so
       it restates the finished judgment instead of anticipating it.

    Moving `mechanics` out of first position would turn it from a reasoning
    step into a trailing restatement of conclusions already reached — the
    same field, doing none of the work it exists to do. `summary` is the one
    field deliberately built to *be* that trailing restatement; nothing below
    it is answered against it the way everything is answered against
    `mechanics`. See `docs/data-model.md#mechanics-the-model-reads-before-it-judges`.
    """

    # Restated rather than inherited from `MetadataPassTwo`. Multiple
    # inheritance would order these two *before* the pass-one fields —
    # pydantic collects fields in reverse-MRO order — putting `power_rating`
    # first and inverting the whole reasoning chain. Two lines of duplication
    # is the cheaper price, and `tests/test_metadata_schema.py` asserts the
    # two declarations stay identical.
    power_rating: float = Field(ge=1, le=10)
    # Last field, deliberately — see the class docstring. Everything else is
    # already decided by the time the model writes this one.
    summary: str = Field(min_length=1)

    @classmethod
    def from_passes(
        cls, one: MetadataPassOne, two: MetadataPassTwo
    ) -> "CardMetadataBlueprint":
        """Merge the two generation passes into one finished blueprint.

        Both halves arrive already validated — each was decoded against its
        own JSON Schema — so this is a join rather than a second validation
        pass. It still goes through the constructor instead of
        `model_construct`, because the merge is the one place a field could go
        missing entirely and re-checking nine fields costs nothing.
        """
        return cls(**one.model_dump(), **two.model_dump())

    def to_card_metadata_row(
        self, card_id: str, *, updated_at: datetime | None = None
    ) -> dict[str, Any]:
        """Shape this blueprint into a `database.knowledge.models.CardMetadata` row.

        `game_stage` serializes to a JSON string, matching that column's plain
        `Mapped[str]` type — the fixed shape is enforced here, on the way in,
        rather than by a database-specific composite column type.
        """
        return {
            "card_id": card_id,
            "mechanics": self.mechanics,
            "synergy_tags": [tag.value for tag in self.synergy_tags],
            "roles": [role.value for role in self.roles],
            # Stored as its own column rather than left implicit as `roles[0]`.
            # Readers that want "what is this card for" — retrieval grouping,
            # the deck builder's slot filling — should not have to know that
            # position zero is privileged, and a row whose `primary_role` is
            # null is honestly distinguishable from one that simply has roles.
            "primary_role": (
                None if self.primary_role is None else self.primary_role.value
            ),
            "themes": [theme.value for theme in self.themes],
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "game_stage": self.game_stage.model_dump_json(),
            "power_rating": self.power_rating,
            "summary": self.summary,
            "updated_at": updated_at or utcnow(),
        }
