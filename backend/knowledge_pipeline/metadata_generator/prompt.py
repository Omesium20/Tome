"""Builds the prompts for metadata generation — two passes, four pieces.

Generation is **two calls per card** (`docs/knowledge-pipeline.md#metadata-generation`):

- Pass one: `build_pass_one_system_prompt` (rubric + closed taxonomy, no
  anchors) and `render_card_facts` (the card). Produces `MetadataPassOne`.
- Pass two: `build_pass_two_system_prompt` (how to read a ladder) and
  `render_rating_request` (the card, pass one's answers, and **only** the
  ladders for the roles and themes pass one assigned). Produces
  `MetadataPassTwo`.

Both system blocks are fixed strings, built once per run and reused for every
card. The per-card ladders ride in pass two's *user* message deliberately: a
system block that varied per card would miss Anthropic's prompt cache on
every call and break llama.cpp's KV-prefix reuse, which is what collapses
local prefill from ~35 s to ~0.2 s
(`docs/benchmarking-and-testing.md#four-operational-facts`).

`build_system_prompt` is the **retired one-call prompt**, and nothing calls it
any more: not `pipeline.py`, and no longer `benchmark/runner.py` either, which
now makes production's two calls. It is kept for one reason — it is the prompt
S-001 to S-003 were recorded against (`benchmark-v2-sessions-001-003.db`), so
re-deriving what those sessions measured needs this exact string. It is not a
supported path, and a change to the vocabulary that touches the two-pass
prompts should not be propagated into it by hand.

All four are plain strings — the pydantic models in `schema.py` are what
actually constrain the model's output; this module only shapes what it's asked.
"""

from __future__ import annotations

from database.knowledge.models import Card

from ..cost_profile import cost_note, facts_from_card
from .anchors import (
    ladders_for,
    render_anchors,
    render_game_stage_anchors,
    render_ladders,
)
from .schema import DERIVED_THEMES, MetadataPassOne, Role, SynergyTag, Theme

# How many calibration ladders pass two is shown at most.
#
# A card with four roles and three themes matches seven ladders — roughly
# 6,000 tokens, most of the way back to the ~14,000-token block whose dilution
# this split exists to remove. Three keeps the block near the ~1,700 tokens the
# A/B measured as an improvement while still giving the model more than one
# reference point to place the card between.
MAX_LADDERS_PER_CARD = 3

# The frozen one-pass rubric. Several of its bullets are restated in
# `_PASS_ONE_RUBRIC` / `_PASS_TWO_RUBRIC` below rather than composed out of
# fragments shared with them, because the two have genuinely diverged — pass
# two's bullets point at the user message for their ladders, and pass one's
# have no `power_rating` step to lead up to. The duplication is the price of
# keeping this text reproducible for `build_system_prompt`; a golden SHA-256
# in `tests/test_prompt_passes.py` catches an accidental edit to it.
_RUBRIC = """\
You are a Magic: The Gathering card analyst generating structured strategic
metadata for one card at a time, for a Commander deck-building assistant.

Rules:
- `mechanics` is answered first, and everything else is answered against it.
  It is a plain-language, literal restatement of the card's rules text: what
  it costs, when you can use it, what triggers it, what it actually does, and
  every condition, limit or cost attached to that. Spell out the keywords you
  rely on ("Landfall — whenever a land enters the battlefield under your
  control"), include the parts that constrain the card as readily as the parts
  that make it good, and say nothing the text does not say. No strategy, no
  evaluation, no synergies, no deck context, no comparison to other cards —
  those belong to the fields below. Judge the rest of this card from the
  restatement you just wrote rather than from the card's name or reputation;
  if the two disagree, the text wins.
- `roles`, `themes`, and `synergy_tags` are closed vocabularies. Choose only
  from the lists below — never invent a new value or rephrase an existing one.
- `synergy_tags` are narrower mechanical hooks than themes (e.g. ETB Trigger,
  Sacrifice Outlet) — tag every hook that's actually present in the text,
  even ones that feel minor. Answer this one straight off `mechanics`, before
  the broader `roles`/`themes` calls below.
- `roles` describes what the card *does* functionally (e.g. Mana Rock, Removal).
- `themes` describes what *strategy or archetype* the card supports (e.g.
  Aristocrats, Voltron). A card can serve a theme without needing it.
- A card can legitimately have zero `roles`, zero `themes`, or zero
  `synergy_tags` — these are closed vocabularies, not fields that must always
  have an answer. Leave a list empty rather than forcing a weak or inexact
  match.
- `strengths`/`weaknesses` are short, specific bullet phrases grounded in the
  `roles`/`themes` you just assigned, not restatements of the oracle text.
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
- `summary` is answered last, once every tag and the `power_rating` are
  already decided: 1-2 sentences on what the card does and why someone would
  play it, consistent with the judgment you've already made rather than a
  second copy of `mechanics`.
- Judge the card as it is played in a 100-card singleton Commander deck, not
  in any other format.
"""


def build_system_prompt() -> str:
    """The retired one-call system block: rubric + closed taxonomy + all anchors.

    **Called by nothing but tests.** `pipeline.py` generates in two passes and
    so does `benchmark/runner.py`; this is the single-call prompt both used
    before the split. It is kept rather than deleted because it is what
    sessions S-001 to S-003 were recorded against, and those numbers cannot be
    read without it.

    What it is *not* is a baseline anyone should re-measure against. It carries
    all 66 anchor ladders in every call, which is the defect the split exists
    to remove, and it carries none of `game_stage`'s turn ranges or reference
    profiles — so its `power_rating` sits in a flat band around 7 and its
    `game_stage` answers a question about impact rather than castability. That
    is exactly what made those three sessions' prompt-shaped findings
    unusable; `docs/lessons-learned.md` records it.

    Historical reasons it was previously kept byte-identical, no longer load-bearing:

    - `benchmark/runner.py` recorded this string on a session and refused to
      resume one whose SHA-256 had changed, so editing it would strand the
      recorded sessions rather than merely re-measure them.
    - It is the baseline the two-pass distribution is compared against. A
      before/after that cannot still render "before" is not a comparison.

    Deterministic and identical across every call and tier, which is what made
    it worth prompt-caching. Delete it once the benchmark harness generates in
    two passes too (`docs/benchmarking-and-testing.md`).
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


# ---------------------------------------------------------------------------
# Pass one: what the card is. No ladders.
# ---------------------------------------------------------------------------

_PASS_ONE_RUBRIC = """\
You are a Magic: The Gathering card analyst generating structured strategic
metadata for one card at a time, for a Commander deck-building assistant.

This is the first of two passes over the card. You describe and classify it
here; a second pass will score its power afterwards. Do not rate the card's
power and do not summarize it — those fields are not yours to write, and
anticipating them here only biases them.

Rules:
- `mechanics` is answered first, and everything else is answered against it.
  It is a plain-language, literal restatement of the card's rules text: what
  it costs, when you can use it, what triggers it, what it actually does, and
  every condition, limit or cost attached to that. Spell out the keywords you
  rely on ("Landfall — whenever a land enters the battlefield under your
  control"), include the parts that constrain the card as readily as the parts
  that make it good, and say nothing the text does not say. No strategy, no
  evaluation, no synergies, no deck context, no comparison to other cards —
  those belong to the fields below. Judge the rest of this card from the
  restatement you just wrote rather than from the card's name or reputation;
  if the two disagree, the text wins.
- `roles`, `themes`, and `synergy_tags` are closed vocabularies. Choose only
  from the lists below — never invent a new value or rephrase an existing one.
- `synergy_tags` are narrower mechanical hooks than themes (e.g. ETB Trigger,
  Sacrifice Outlet) — tag every hook that's actually present in the text,
  even ones that feel minor. Answer this one straight off `mechanics`, before
  the broader `roles`/`themes` calls below.
- `roles` describes what the card *does* functionally (e.g. Mana Rock, Removal).
- `primary_role` is the one role from the `roles` list you have just written
  that the card is most defined by — the job it is in a deck to do, not every
  job it can incidentally perform. Pick it from that list rather than from the
  full vocabulary. Leave it null only when you assigned no roles at all.
- `themes` describes what *strategy or archetype* the card supports (e.g.
  Aristocrats, Voltron). A card can serve a theme without needing it.
- Treasure, Food, Clues, Energy, and Infect are not in the `themes` list
  below. They are added separately from the card's own text after you
  respond, so do not spend a `themes` slot on them even if the card plainly
  qualifies.
- A card can legitimately have zero `roles`, zero `themes`, or zero
  `synergy_tags` — these are closed vocabularies, not fields that must always
  have an answer. Leave a list empty rather than forcing a weak or inexact
  match.
- `strengths`/`weaknesses` are short, specific bullet phrases grounded in the
  `roles`/`themes` you just assigned, not restatements of the oracle text.
- `game_stage` is three independent 1-10 scores (early/mid/late), not a
  distribution — they don't need to sum to anything, and a card can score
  high (or low) on all three at once. In a Commander game, *early* is about
  turns 1-4, *mid* turns 5-8, and *late* turn 9 onward. Score each stage on
  how much the card is doing in that phase. A card that is doing nothing in
  a phase scores 1-2 there; use the whole range rather than clustering.
- Score `early` from the first turn the card can realistically do something,
  which is its total cost to function and not its mana value. Cost
  reductions, alternative costs, costs paid later such as equip, and delays
  such as suspend all move that turn, in either direction. Read it off the
  `mechanics` restatement you have already written.
- Judge the card as it is played in a 100-card singleton Commander deck, not
  in any other format.
"""


def build_pass_one_system_prompt() -> str:
    """Pass one's system block: rubric, closed taxonomy, game stage profiles.

    **Still no `power_rating` ladders**, which is the whole point of the
    split: this block does not carry the 66 of them — ~14,000 tokens of
    calibration data that pass one has no use for, and whose presence
    flattened `power_rating` into a band around 7
    (`docs/lessons-learned.md`).

    It *does* carry the `game_stage` profiles, and they are a different kind
    of thing: ~630 tokens, one set for the whole corpus rather than one per
    tag, and fixed across every card. Nothing selects them, so none of the
    dilution or mis-selection that argued against the ladders applies —
    see the comment above `GAME_STAGE_ANCHORS` for why that set can be global
    when the power ladders cannot.

    Fixed across every card and tier, so it stays worth prompt-caching.
    """
    sections = [
        _PASS_ONE_RUBRIC,
        "Valid `roles` values:\n" + ", ".join(role.value for role in Role),
        # `DERIVED_THEMES` excluded: `derive_themes` reads them off the card
        # directly and `pipeline.py` merges them in after generation
        # (`MetadataPassOne.with_derived_themes`) -- see `schema.DERIVED_THEMES`.
        "Valid `themes` values:\n"
        + ", ".join(theme.value for theme in Theme if theme not in DERIVED_THEMES),
        "Valid `synergy_tags` values:\n"
        + ", ".join(tag.value for tag in SynergyTag),
    ]

    game_stage = render_game_stage_anchors()
    if game_stage:
        sections.append(game_stage)

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Pass two: how good the card is, against its own ladders.
# ---------------------------------------------------------------------------

_PASS_TWO_RUBRIC = """\
You are a Magic: The Gathering card analyst scoring one card's power, for a
Commander deck-building assistant.

This is the second of two passes. A first pass has already read the card and
classified it; you are given the card, that classification, and the
calibration ladders for the roles and themes it was assigned. Treat the
classification as settled — do not re-tag the card, do not argue with it, and
do not let a tag you would have chosen differently change how you read the
ladders you were given. Answer two fields only.

Rules:
- `power_rating` is 1-10, and is *not* scored freehand. The calibration
  ladders in the message below are the ones that apply to this card: each has
  five reference cards, one per band of the scale. Read the card against those
  rungs and place it where it falls between them. Where several ladders are
  shown they are ordered, and the first is the one for the card's primary
  role — calibrate against that one unless another ladder shown plainly
  covers this card's strongest effect better.
  The ends of the scale are real positions, not reserve: a card weaker than a
  ladder's 1-2 rung scores 1-2, and a card that does less than every rung
  shown scores below all of them. If the message says no ladder covers this
  card, fall back on your own judgment, applied as consistently as you can.
- `summary` is answered last, once the `power_rating` is already decided: 1-2
  sentences on what the card does and why someone would play it, consistent
  with the judgment you've just made rather than a second copy of the
  `mechanics` restatement you were given.
- Judge the card as it is played in a 100-card singleton Commander deck, not
  in any other format.
"""

_NO_LADDER_NOTICE = (
    "No calibration ladder covers this card — none of the roles or themes it "
    "was assigned has one. Score `power_rating` on the 1-10 scale using your "
    "own judgment, applied as consistently as you can across cards, and use "
    "the full range rather than clustering in the middle."
)


def build_pass_two_system_prompt() -> str:
    """Pass two's system block: how to read a ladder, and nothing else.

    No ladders of its own and no taxonomy — the ladders are per-card data and
    travel in the user message (`render_rating_request`), and the closed
    vocabularies were pass one's job. Fixed across every card, so this block
    caches exactly like pass one's.
    """
    return _PASS_TWO_RUBRIC


def _render_pass_one_result(pass_one: MetadataPassOne) -> str:
    """Pass one's answers, as the settled context pass two rates against."""
    stage = pass_one.game_stage
    lines = [
        "Already established for this card by the first pass — treat as settled:",
        "",
        f"Mechanics: {pass_one.mechanics}",
        f"Roles: {', '.join(role.value for role in pass_one.roles) or '(none)'}",
        "Primary role: "
        + (pass_one.primary_role.value if pass_one.primary_role else "(none)"),
        f"Themes: {', '.join(theme.value for theme in pass_one.themes) or '(none)'}",
        "Synergy tags: "
        + (", ".join(tag.value for tag in pass_one.synergy_tags) or "(none)"),
        "Strengths:",
        *(f"- {item}" for item in pass_one.strengths),
        "Weaknesses:",
        *(f"- {item}" for item in pass_one.weaknesses),
        f"Game stage: early={stage.early}, mid={stage.mid}, late={stage.late}",
    ]
    return "\n".join(lines)


def render_rating_request(*, card_facts: str, pass_one: MetadataPassOne) -> str:
    """Pass two's user message: the card, pass one's answers, and its ladders.

    The ladders are selected from `pass_one`'s own tags by exact registry
    lookup (`anchors.ladders_for`), never by a heuristic or a name match, and
    the set is capped at `MAX_LADDERS_PER_CARD`.

    Which ones survive that cap is `MetadataPassOne.ladder_tags`'s decision,
    not this function's: primary role, then the other roles, then themes. The
    cap is what makes the order matter — a card matching seven ladders is shown
    three, so leading with the role the model named as the card's defining one
    is the difference between calibrating against that role and calibrating
    against whichever role happened to be emitted first.

    `card_facts` is passed in rather than re-rendered here so both passes are
    guaranteed to have seen the same text for the same card.
    """
    ladders = ladders_for(pass_one.ladder_tags(), limit=MAX_LADDERS_PER_CARD)
    calibration = render_ladders(ladders) or _NO_LADDER_NOTICE

    return "\n\n".join([card_facts, _render_pass_one_result(pass_one), calibration])


def _legal_formats(card: Card) -> list[str]:
    return sorted(
        name for name, status in card.legalities.items() if status in ("legal", "restricted")
    )


def render_card_facts(card: Card) -> str:
    """The per-card user message: everything the model needs, nothing it doesn't.

    Deliberately excludes fields that don't bear on strategy (`scryfall_id`,
    `image_url`) — see `docs/data-model.md#card` for why those exist at all.

    Ends with a `Cost note:` line on the ~17% of cards whose `mana_value`
    misstates what they cost to function (`knowledge_pipeline/cost_profile.py`).
    That line is **data, not instruction** — it states a fact about the card and
    says nothing about how to score it, because the rubric owns the scoring rule
    and `docs/lessons-learned.md` records a pass-one bullet that backfired the
    moment it read as emphasis. Deriving it in code rather than asking the model
    to notice is the division `CLAUDE.md` requires.
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

    note = cost_note(facts_from_card(card))
    if note:
        lines.append(note)

    return "\n".join(lines)
