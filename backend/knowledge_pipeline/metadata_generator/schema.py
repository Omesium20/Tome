"""Blueprint for AI-generated `CardMetadata` (`docs/data-model.md#cardmetadata`).

This module owns the *shape* a metadata-generation call must produce — the
same role `scryfall_importer/mapping.py`'s `CardRow` plays for `Card`: a
validated, Python-side representation that sits between raw model output and
the `CardMetadata` row in `database/knowledge/models.py`. It is not that
SQLAlchemy row itself — those columns are plain strings/JSON, since that's
what Alembic diffs cleanly — this is what a generation call actually returns
and what the generator validates before writing that row.

`Role`, `Theme`, and `SynergyTag` are closed enums, per
`docs/data-model.md#controlled-vocabulary-roles-themes-synergy_tags` — the
model is shown these exact members in the prompt and must choose only from
them, so retrieval filtering and the deck builder's role-based grouping never
see near-duplicate strings ("Ramp" vs. "Mana Ramp") for the same concept.

The members below are a first-draft vocabulary, not a ratified taxonomy —
see `docs/data-model.md#anchor-cards`. Adding or renaming a `Role`/`Theme`
member later requires building it a full five-rung anchor ladder before it
can be trusted at the same 1-10 scale as the rest — not one example card, a
card per power band — and is a deliberate, reviewed edit for that reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Naive UTC now, matching `scryfall_importer/mapping.py`'s convention.

    Timestamp columns are plain `DateTime` (SQLite has no real timezone
    support), so every datetime reaching the database is normalized here.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(StrEnum):
    """What a card *does* functionally. Closed vocabulary."""

    RAMP = "Ramp"
    MANA_FIXING = "Mana Fixing"
    REMOVAL = "Removal"
    BOARD_WIPE = "Board Wipe"
    CARD_DRAW = "Card Draw"
    CARD_SELECTION = "Card Selection"
    TUTOR = "Tutor"
    RECURSION = "Recursion"
    GRAVEYARD_HATE = "Graveyard Hate"
    PROTECTION = "Protection"
    COUNTERSPELL = "Counterspell"
    COMBO_PIECE = "Combo Piece"
    WIN_CONDITION = "Win Condition"
    STAX_PIECE = "Stax Piece"
    LAND_DESTRUCTION = "Land Destruction"
    HAND_DISRUPTION = "Hand Disruption"
    TOKEN_GENERATOR = "Token Generator"
    ANTHEM = "Anthem"
    EQUIPMENT = "Equipment"
    AURA = "Aura"
    EXTRA_COMBAT = "Extra Combat"
    EXTRA_TURNS = "Extra Turns"
    LIFEGAIN = "Lifegain"
    THEFT = "Theft Effect"


class Theme(StrEnum):
    """What *strategy or archetype* a card supports. Closed vocabulary."""

    ARISTOCRATS = "Aristocrats"
    VOLTRON = "Voltron"
    SPELLSLINGER = "Spellslinger"
    TOKENS = "Tokens"
    LANDFALL = "Landfall"
    BIG_MANA = "Big Mana"
    REANIMATOR = "Reanimator"
    GROUP_HUG = "Group Hug"
    GROUP_SLUG = "Group Slug"
    STAX = "Stax"
    ARTIFACTS = "Artifacts"
    ENCHANTRESS = "Enchantress"
    TRIBAL = "Tribal"
    COUNTERS = "+1/+1 Counters"
    SACRIFICE = "Sacrifice"
    SUPERFRIENDS = "Superfriends"
    STORM = "Storm"
    WHEEL = "Wheel"
    BLINK = "Blink"
    GRAVEYARD_VALUE = "Graveyard Value"
    CONTROL = "Control"
    AGGRO = "Aggro"
    MILL = "Mill"
    LIFEGAIN = "Lifegain"
    EQUIPMENT = "Equipment"
    # No MIDRANGE. It was dropped rather than anchored: nothing printed on a
    # card makes it midrange — the word describes a deck's posture across a
    # whole game, so any ladder for it grades cards by a property they don't
    # individually have. A closed vocabulary only earns its strictness if every
    # member is decidable from the card in front of the model.
    TREASURE = "Treasure"
    DISCARD = "Discard"
    # No FLYING, dropped for a different reason than MIDRANGE. Flying is
    # decidable from the card -- but an anchor ladder for it grades the wrong
    # thing. Every rung that reads as a strong "flying card" is a card that
    # *grants* evasion, while the theme is supposed to collect cards that are
    # *paid off* by it, and the top of the scale has almost nothing in the
    # second group. A ladder whose rungs answer a different question than the
    # tag asks teaches the model to mislabel rather than to calibrate. Evasion
    # is better served as a property of a card than as an archetype of its own.


class SynergyTag(StrEnum):
    """Narrow mechanical hooks a card cares about — finer-grained than `Theme`."""

    COUNTERS_MATTER = "Counters Matter"
    ETB_TRIGGER = "ETB Trigger"
    DEATH_TRIGGER = "Death Trigger"
    SACRIFICE_OUTLET = "Sacrifice Outlet"
    COST_REDUCTION = "Cost Reduction"
    COPY_EFFECT = "Copy Effect"
    UNTAP_EFFECT = "Untap Effect"
    MANA_DOUBLER = "Mana Doubler"
    GRAVEYARD_FILL = "Graveyard Fill"
    ARTIFACT_MATTERS = "Artifact Matters"
    ENCHANTMENT_MATTERS = "Enchantment Matters"
    SPELLS_MATTER = "Instant/Sorcery Matters"
    X_SPELL = "X Spell"
    COMBAT_DAMAGE_TRIGGER = "Combat Damage Trigger"
    COLOR_IDENTITY_FIXING = "Color Identity Fixing"


class GameStageProfile(BaseModel):
    """Independent early/mid/late strength scores — not a distribution.

    A card can legitimately score high on all three (Sol Ring) or low on all
    three (a narrow, situational answer). See
    `docs/data-model.md#game_stage-gamestageprofile`.
    """

    early: float = Field(ge=1, le=10)
    mid: float = Field(ge=1, le=10)
    late: float = Field(ge=1, le=10)


class CardMetadataBlueprint(BaseModel):
    """The exact shape one metadata-generation call must produce.

    Deliberately excludes `card_id` and `updated_at` — a generation call is
    stateless (`docs/data-model.md#anchor-cards`) and shouldn't be trusted to
    fill in the pipeline's own bookkeeping. Use `to_card_metadata_row` to
    attach those before writing to `database.knowledge.models.CardMetadata`.
    """

    summary: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    themes: list[Theme] = Field(min_length=1)
    game_stage: GameStageProfile
    power_rating: float = Field(ge=1, le=10)
    strengths: list[str] = Field(min_length=1)
    weaknesses: list[str] = Field(min_length=1)
    synergy_tags: list[SynergyTag] = Field(min_length=1)

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
            "summary": self.summary,
            "roles": [role.value for role in self.roles],
            "themes": [theme.value for theme in self.themes],
            "game_stage": self.game_stage.model_dump_json(),
            "power_rating": self.power_rating,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "synergy_tags": [tag.value for tag in self.synergy_tags],
            "updated_at": updated_at or utcnow(),
        }
