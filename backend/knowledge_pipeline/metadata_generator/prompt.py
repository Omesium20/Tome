"""Builds the two halves of a metadata-generation call.

`build_system_prompt` is the cached block every call shares: the closed
taxonomy, the rubric, and whatever anchor cards exist
(`docs/data-model.md#anchor-cards`). `render_card_facts` is the per-card user
message. Both are plain strings — `CardMetadataBlueprint` (`schema.py`)
is what actually constrains the model's output; this module only shapes what
it's asked.
"""

from __future__ import annotations

from database.knowledge.models import Card

from .anchors import render_anchors
from .schema import Role, SynergyTag, Theme

_RUBRIC = """\
You are a Magic: The Gathering card analyst generating structured strategic
metadata for one card at a time, for a Commander deck-building assistant.

Rules:
- `roles`, `themes`, and `synergy_tags` are closed vocabularies. Choose only
  from the lists below — never invent a new value or rephrase an existing one.
- `roles` describes what the card *does* functionally (e.g. Ramp, Removal).
- `themes` describes what *strategy or archetype* the card supports (e.g.
  Aristocrats, Voltron). A card can serve a theme without needing it.
- `synergy_tags` are narrower mechanical hooks than themes (e.g. ETB Trigger,
  Sacrifice Outlet) — tag every hook that's actually present in the text,
  even ones that feel minor.
- `game_stage` is three independent 1-10 scores (early/mid/late), not a
  distribution — they don't need to sum to anything, and a card can score
  high (or low) on all three at once. Score each stage on how much the card
  is doing *in that phase of a Commander game specifically*.
- `power_rating` is 1-10, and is *not* scored freehand. Each role and theme
  with a calibration ladder below has five reference cards, one per band of
  the scale. Find a ladder for a tag this card shares, read the card against
  those five rungs, and place it where it falls between them. If the card
  shares several tags that have ladders, calibrate against the one its
  strongest effect belongs to. Only when no tag this card uses has a ladder
  should you fall back on your own judgment, applied as consistently as you
  can.
- `summary` is 1-2 sentences on what the card does and why someone would
  play it. `strengths`/`weaknesses` are short, specific bullet phrases, not
  restatements of the oracle text.
- Judge the card as it is played in a 100-card singleton Commander deck, not
  in any other format.
"""


def build_system_prompt() -> str:
    """The system block: rubric + closed taxonomy + anchors.

    Deterministic and identical across every call and tier, which is what
    makes it worth prompt-caching (`docs/benchmarking-and-testing.md`'s
    "system_prompt" column assumes exactly this block).
    """
    sections = [
        _RUBRIC,
        "Valid `roles` values:\n" + ", ".join(role.value for role in Role),
        "Valid `themes` values:\n" + ", ".join(theme.value for theme in Theme),
        "Valid `synergy_tags` values:\n"
        + ", ".join(tag.value for tag in SynergyTag),
    ]

    anchors = render_anchors()
    if anchors:
        sections.append(anchors)

    return "\n\n".join(sections)


def _legal_formats(card: Card) -> list[str]:
    return sorted(
        name for name, status in card.legalities.items() if status in ("legal", "restricted")
    )


def render_card_facts(card: Card) -> str:
    """The per-card user message: everything the model needs, nothing it doesn't.

    Deliberately excludes fields that don't bear on strategy (`scryfall_id`,
    `image_url`) — see `docs/data-model.md#card` for why those exist at all.
    """
    lines = [
        f"Name: {card.name}",
        f"Mana cost: {card.mana_cost or '(none)'}",
        f"Mana value: {card.mana_value}",
        f"Type line: {card.type_line}",
        f"Color identity: {', '.join(card.color_identity) or '(colorless)'}",
        f"Oracle text:\n{card.oracle_text or '(vanilla — no rules text)'}",
    ]

    if card.power is not None or card.toughness is not None:
        lines.append(f"Power/Toughness: {card.power or '?'}/{card.toughness or '?'}")
    if card.loyalty is not None:
        lines.append(f"Loyalty: {card.loyalty}")
    if card.defense is not None:
        lines.append(f"Defense: {card.defense}")
    if card.keywords:
        lines.append(f"Keywords: {', '.join(card.keywords)}")

    legal_in = _legal_formats(card)
    lines.append(f"Legal in: {', '.join(legal_in) if legal_in else '(no format)'}")

    return "\n".join(lines)
