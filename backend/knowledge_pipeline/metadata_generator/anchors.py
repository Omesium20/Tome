"""Anchor ladders: five graded calibration cards per `Role`/`Theme` value.

See `docs/data-model.md#anchor-cards`. Anchors are what keep `power_rating`
and the role/theme boundary consistent across ~31,830 independent, stateless
generation calls — and across model tiers, once tiering exists: every call,
on every tier, sees the same fixed reference points in its system prompt.

**Five rungs, not one card.** A single labeled example per tag pins one point
on the 1-10 scale and says nothing about the rest of it — a model told only
that Cultivate is a 6 has no reference for what a 3 or a 9 looks like, and
nothing stops 31,830 stateless calls from drifting. Each tag therefore gets a
*ladder*: one card per power band, covering 1-2, 3-4, 5-6, 7-8 and 9-10. The
model rates a card by reading it against the ladder for a tag it shares and
placing it between the rungs, which is a comparison rather than a judgment
call against an abstract scale.

Every ladder is complete or absent. A missing rung is a gap the model fills
with its own unanchored guess, in precisely the band nobody checked — so
`AnchorLadder` refuses to construct with fewer than five, rather than
silently calibrating four-fifths of a scale.

**The registry ships populated: one complete ladder for every `Role` and
`Theme` member** -- 66 ladders, 330 rungs, 330 distinct cards.

**Read `docs/data-model.md#anchor-cards` for how these 330 were settled**, which
is not uniform and should not be remembered as though it were. The original 51
ladders had machine triage and a human pick on every rung. The 2026-10
vocabulary revision pushed the job to 155 new rungs, the human pass did not
survive it, and 149 rungs were taken from the triage scorer's recommendation --
48 of those decided by a tie-break built for reproducibility. Those 48 are the
softest rungs in the registry. "Reviewed as a batch and found acceptable" is a
weaker claim than "chosen", and the docs say so rather than rounding up.

A rung is **revised** from here on by having a model propose and choose against
the band rubric, writing the `register(...)` call directly -- cheaper and better
calibrated than a tie-break, and the step it replaces had already stopped
happening. `docs/lessons-learned.md` records why the review scaffolding built
for that step was deleted rather than kept as a statement of intent.

What survived from that machinery is the part a model cannot do for itself:
every rung is checked against the corpus, so no name here is a recollection.
Two gates stand on an edit, and a recalibration is not done until both pass
(`docs/anchor-recalibration.md` is the procedure):

    python -m knowledge_pipeline.metadata_generator.verify_anchors
    pytest tests/test_anchors.py

The first says the cards are real and Commander-legal. The second says the set
is coherent -- five rungs each, every rating inside its band, no card at two
different ratings, no card anchoring two ladders, no member left unanchored.

`Theme.FLYING` has no ladder because the member no longer exists -- see
`schema.py`, which records why it was dropped rather than anchored.

Add or replace a ladder with `register`, one complete ladder per enum value.
Do not assign into `ANCHORS` directly -- see `anchor_key` for why the registry
is keyed by a qualified string rather than by the enum member:

    register(AnchorLadder(
        tag=Role.MANA_FIXING,
        rungs=[
            AnchorCard(PowerBand.B1_2, "Wayfarer's Bauble", 2,
                       "Ramp that costs two cards' worth of tempo to fetch one land."),
            ...
            AnchorCard(PowerBand.B9_10, "Mana Vault", 9, "..."),
        ],
    ))
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .schema import Role, Theme


class PowerBand(StrEnum):
    """One rung of the ladder: a two-point slice of the 1-10 scale.

    Bands are two points wide rather than one so a ladder stays five cards
    instead of ten. Five is enough to interpolate against and short enough to
    keep in a cached system block across 66 tags; ten would double the block
    for a precision `power_rating` doesn't carry anyway.

    The member *values* are the ranges themselves. Naming the bands
    ("filler", "staple") would bake a rubric into the enum that
    `docs/data-model.md` is the right place to state, and that a reviewer
    should be able to revise without a code change.
    """

    B1_2 = "1-2"
    B3_4 = "3-4"
    B5_6 = "5-6"
    B7_8 = "7-8"
    B9_10 = "9-10"

    @property
    def bounds(self) -> tuple[int, int]:
        """The band's inclusive `(low, high)` bounds, parsed from its value."""
        low, high = self.value.split("-")
        return int(low), int(high)

    def contains(self, rating: float) -> bool:
        low, high = self.bounds
        return low <= rating <= high


# Ladder order, low to high. `PowerBand` is a `StrEnum`, so its members
# already iterate in definition order — this exists so the ordering is an
# explicit, testable constant rather than an implicit property of how the
# class happens to be written.
BAND_ORDER: tuple[PowerBand, ...] = (
    PowerBand.B1_2,
    PowerBand.B3_4,
    PowerBand.B5_6,
    PowerBand.B7_8,
    PowerBand.B9_10,
)


@dataclass(frozen=True)
class AnchorCard:
    """One rung: a hand-picked card fixing what its band looks like for a tag."""

    band: PowerBand
    name: str
    power_rating: float
    note: str

    def __post_init__(self) -> None:
        if not self.band.contains(self.power_rating):
            low, high = self.band.bounds
            raise ValueError(
                f"{self.name}'s power_rating {self.power_rating} falls outside "
                f"band {self.band.value} ({low}-{high}). A rung that sits in a "
                "different band than the one it illustrates teaches the model "
                "the opposite of what it is for."
            )
        if not self.name.strip():
            raise ValueError("An anchor needs a card name.")
        if not self.note.strip():
            raise ValueError(
                f"{self.name} needs a note: the rung has to say *why* it sits "
                "in its band, or the model can only pattern-match the name."
            )


@dataclass(frozen=True)
class AnchorLadder:
    """The five graded reference cards for one `Role` or `Theme` value.

    Constructed from a sequence rather than a mapping so a caller writes the
    rungs in reading order and cannot silently file a card under the wrong
    band key — the band travels on the `AnchorCard` itself, and this class
    checks that the set is exactly the five, each once.
    """

    tag: Role | Theme
    rungs: tuple[AnchorCard, ...]

    def __init__(self, tag: Role | Theme, rungs: list[AnchorCard] | tuple[AnchorCard, ...]):
        ordered = tuple(sorted(rungs, key=lambda rung: BAND_ORDER.index(rung.band)))
        object.__setattr__(self, "tag", tag)
        object.__setattr__(self, "rungs", ordered)
        self._validate()

    def _validate(self) -> None:
        bands = [rung.band for rung in self.rungs]
        if len(bands) != len(set(bands)):
            raise ValueError(
                f"{self.tag.value}: two rungs claim the same power band "
                f"({', '.join(band.value for band in bands)}). One card per band."
            )
        missing = [band for band in BAND_ORDER if band not in bands]
        if missing:
            raise ValueError(
                f"{self.tag.value} is missing the "
                f"{', '.join(band.value for band in missing)} rung(s). A ladder is "
                "complete or absent: a gap is an unanchored band the model fills "
                "with a guess nobody reviewed."
            )

    def render(self) -> str:
        """This ladder as the block the system prompt shows for one tag.

        The heading carries the *kind* as well as the name because `Role` and
        `Theme` both define "Lifegain" and "Equipment" — two blocks headed
        just `Lifegain:` would be an ambiguity in the prompt itself, not only
        in the registry.
        """
        lines = [f"{self.tag.value} ({kind_of(self.tag)}):"]
        for rung in self.rungs:
            lines.append(
                f"  {rung.band.value:>5}  {rung.name} "
                f"(power_rating={rung.power_rating}) — {rung.note}"
            )
        return "\n".join(lines)


def kind_of(tag: Role | Theme) -> str:
    """`"role"` or `"theme"` — which vocabulary a tag belongs to."""
    return "role" if isinstance(tag, Role) else "theme"


def anchor_key(tag: Role | Theme) -> str:
    """The registry key for a tag, e.g. `"Role.LIFEGAIN"`.

    **Why the registry is not keyed on the enum member itself.** `Role` and
    `Theme` are `StrEnum`s, so a member *is* its string value: `Role.LIFEGAIN
    == Theme.LIFEGAIN` is `True` and the two hash identically. Both
    vocabularies define "Lifegain" and "Equipment" — a role is what a card
    *does*, a theme is what it *supports*, and those are different questions
    that happen to share a word. Keyed on the member, `ANCHORS` would collapse
    each pair into one entry, silently dropping two of 53 ladders and keeping
    whichever was assigned last. Nothing would raise, `missing_tags()` would
    report nothing wrong (it compares with the same broken equality), and the
    rendered prompt would just be missing two ladders.

    Qualifying by the enum's own class name makes the two distinct, and the
    key stays readable in a traceback.
    """
    return f"{type(tag).__name__}.{tag.name}"


ANCHORS: dict[str, AnchorLadder] = {}
"""Populated ladders, keyed by `anchor_key(tag)`. Use `register` to add one."""


def register(ladder: AnchorLadder) -> None:
    """Add a ladder to the registry under its own tag's key.

    Takes the ladder rather than a `(tag, ladder)` pair on purpose: the ladder
    already knows its tag, so there is no second place to state it and no way
    to file one tag's ladder under another's key. Re-registering the same tag
    raises — in a hand-maintained registry, a duplicate is a copy-paste slip,
    not an intentional override.
    """
    key = anchor_key(ladder.tag)
    if key in ANCHORS:
        raise ValueError(
            f"{key} already has a ladder ({ANCHORS[key].rungs[0].name}...). "
            "Two ladders for one tag is a copy-paste slip, not an override."
        )
    ANCHORS[key] = ladder


def ladder_for(tag: Role | Theme) -> AnchorLadder | None:
    """The ladder registered for `tag`, or `None`."""
    return ANCHORS.get(anchor_key(tag))


def missing_tags() -> list[Role | Theme]:
    """Every `Role`/`Theme` value with no ladder yet.

    What a pre-run check reports on. An unanchored tag isn't fatal — the
    prompt tells the model to fall back to its own consistent judgment — but
    it is the list of places the corpus's power scale is ungrounded, so it
    should be seen before a full run rather than discovered in the output.

    Goes through `anchor_key` rather than testing `tag in ANCHORS`, so the
    `Role`/`Theme` name collision documented on `anchor_key` cannot make an
    unanchored tag look anchored.
    """
    return [tag for tag in (*Role, *Theme) if anchor_key(tag) not in ANCHORS]


def ladders_for(
    tags: Iterable[Role | Theme], *, limit: int | None = None
) -> list[AnchorLadder]:
    """The ladders for `tags`, in the order given, unanchored tags dropped.

    What lets a generation call be shown the handful of ladders a card
    actually needs instead of all 66. Showing the whole set is what flattened
    `power_rating` into a band around 7 (`docs/lessons-learned.md`); this is
    the selector that replaced it.

    Deduplicates by `anchor_key` rather than by the tag, for the reason that
    function documents: `Role.LIFEGAIN == Theme.LIFEGAIN` is `True`, so a card
    tagged with both would otherwise contribute one ladder instead of two.

    Input order is preserved rather than sorted, and the caller owns it:
    `schema.MetadataPassOne.ladder_tags` leads with the card's `primary_role`,
    then the rest of the roles, then the themes. Roles precede themes because a
    role is what the card *does*, the closest thing to the "strongest effect"
    the rubric asks the model to calibrate against; the primary role precedes
    the rest because it is the only one the model was asked to rank. Sorting
    here instead would replace a ranking with the arbitrary order the
    vocabulary happens to be written in.

    `limit` truncates after deduplication. A heavily tagged card can match
    seven ladders, which is most of the way back to the dilution this exists
    to prevent.
    """
    selected: list[AnchorLadder] = []
    seen: set[str] = set()

    for tag in tags:
        key = anchor_key(tag)
        if key in seen:
            continue
        seen.add(key)

        ladder = ladder_for(tag)
        if ladder is None:
            continue

        selected.append(ladder)
        if limit is not None and len(selected) >= limit:
            break

    return selected


def render_ladders(ladders: Sequence[AnchorLadder]) -> str:
    """Render `ladders` as a calibration block, with the instructions above them.

    Returns an empty string for an empty sequence — the caller decides what an
    uncalibrated card deserves. `render_anchors` uses this for the whole
    registry; `prompt.render_rating_request` uses it for one card's matches.
    """
    if not ladders:
        return ""

    rendered = "\n\n".join(ladder.render() for ladder in ladders)
    return (
        "Calibration ladders — fixed reference points. For each role or theme "
        "below, five cards mark what each band of the 1-10 power scale looks "
        "like for that tag, lowest to highest.\n\n"
        "To score `power_rating`: find a ladder for a role or theme the card "
        "you are rating shares, read the card against those five rungs, and "
        "place it where it falls between them. Compare against the rungs — do "
        "not score against an abstract sense of the scale. Where a card shares "
        "several tags with ladders, use the one its strongest effect belongs "
        "to.\n\n" + rendered
    )


def render_anchors() -> str:
    """Render *every* populated ladder as one calibration block.

    Returns an empty string when `ANCHORS` is empty — the caller decides
    whether that's acceptable (fine for a smoke test; not for a full-corpus
    run, per the module docstring above).

    **No longer on the generation path.** Its only caller is
    `prompt.build_system_prompt`, which is the retired one-call prompt that
    nothing but tests now invokes — the benchmark makes production's two calls
    (`prompt.py`). Production shows one card's matching ladders via
    `ladders_for` + `render_ladders` instead.
    """
    return render_ladders(list(ANCHORS.values()))


# ---------------------------------------------------------------------------
# The ladders.
# ---------------------------------------------------------------------------
#
# One complete five-rung ladder per `Role` and `Theme` member, lowest band
# first. The comment above each ladder states the axis it measures -- what
# actually changes from rung to rung. That line is load-bearing: a proposed
# replacement rung can be a fine card and still be a bad rung, because it
# grades the tag on a different property than the four cards around it. Check
# a swap against the axis, not just against the band rubric.
#
# Two rules the set as a whole obeys, and that an edit must preserve:
#
#   No card appears in two ladders. Nothing forbids it -- a card legitimately
#   fills several roles -- but a shared rung is one fewer independent
#   reference point, and for the `Role`/`Theme` pairs that share a word
#   (Lifegain, Equipment) the same card on both sides actively fails to teach
#   the distinction the two vocabularies exist to draw.
#
#   No card carries two different ratings. This is the harder rule and the
#   reason several otherwise-good candidates were passed over: a card rated 9
#   under one tag and 8 under another teaches, in the same prompt, that the
#   scale depends on which ladder you read -- the exact inconsistency anchors
#   exist to prevent.

# Role.MANA_FIXING -- Even through 7-8. The 9-10 rung is the softest in the
# registry: pure fixing never warps a format, so the top rung marks the ceiling
# of the effect rather than a game-warping card.
register(AnchorLadder(
    tag=Role.MANA_FIXING,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Shimmering Grotto", 1,
                   "Fixing that charges a mana per use and produces nothing otherwise."),
        AnchorCard(PowerBand.B3_4, "Vivid Crag", 3,
                   "Free fixing with a countdown: two charge counters and the "
                   "any-color ability is spent."),
        AnchorCard(PowerBand.B5_6, "Exotic Orchard", 6,
                   "Free, untapped, any color -- if an opponent's lands can make it."),
        AnchorCard(PowerBand.B7_8, "Birds of Paradise", 8,
                   "One mana, any color, every turn, starting on turn one."),
        AnchorCard(PowerBand.B9_10, "Chromatic Lantern", 9,
                   "Every land you control taps for any color, and the Lantern itself "
                   "does too."),
    ],
))


# Role.REMOVAL -- Even. Axis is cost, condition, and breadth: conditional
# sorcery -> unconditional instant -> removal on a reusable body -> any
# permanent at instant speed -> one mana, exile, no condition.
register(AnchorLadder(
    tag=Role.REMOVAL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Vengeance", 1,
                   "Assassinate for one more mana."),
        AnchorCard(PowerBand.B3_4, "Murder", 4,
                   "Unconditional creature removal at instant speed for three, and "
                   "nothing else."),
        AnchorCard(PowerBand.B5_6, "Ravenous Chupacabra", 6,
                   "Unconditional creature removal stapled to a 2/2 body that can be "
                   "blinked, recurred, or copied."),
        AnchorCard(PowerBand.B7_8, "Assassin's Trophy", 8,
                   "Two mana, instant, any permanent an opponent controls -- they get "
                   "one basic."),
        AnchorCard(PowerBand.B9_10, "Deadly Rollick", 9,
                   "Free exile removal while you control your commander -- removal "
                   "that costs no turn."),
    ],
))


# Role.BOARD_WIPE -- Even. How much dies, how reliably, and what it costs you:
# one damage that kills nothing -> everything of yours dies too, for six at
# sorcery speed -> three permanent types, a full turn late -> four mana, every
# creature, no regeneration -> the sweeper that answers the boards the others
# cannot, for three mana. The old 9-10 rung was one-sided (Cyclonic Rift),
# which made the top of this ladder a statement about sidedness; with that card
# moved to Role.BOUNCE the top rung grades cheapness and finality instead,
# which is the axis the other four rungs were already on.
register(AnchorLadder(
    tag=Role.BOARD_WIPE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Tremor", 1,
                   "One damage to each creature without flying -- a sweeper that kills "
                   "nothing in Commander."),
        AnchorCard(PowerBand.B3_4, "Planar Cleansing", 4,
                   "Destroys every nonland permanent -- including all of yours -- for "
                   "six mana at sorcery speed."),
        AnchorCard(PowerBand.B5_6, "Nevinyrral's Disk", 5,
                   "Wipes creatures, artifacts, and enchantments -- one full turn "
                   "after you play it."),
        AnchorCard(PowerBand.B7_8, "Wrath of God", 8,
                   "Four mana, every creature dies, no regeneration -- the "
                   "definitional sweeper."),
        AnchorCard(PowerBand.B9_10, "Toxic Deluge", 9,
                   "Three mana and X life: every creature gets -X/-X, so "
                   "indestructible and regeneration do not save anything."),
    ],
))


# Role.CARD_DRAW -- Even. The axis is cards per mana, then one-shot versus
# engine, then engine that also taxes the table.
register(AnchorLadder(
    tag=Role.CARD_DRAW,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Jayemdae Tome", 1,
                   "Four mana to play, four mana a turn, one card each time."),
        AnchorCard(PowerBand.B3_4, "Divination", 3,
                   "Three mana, draw two, sorcery speed."),
        AnchorCard(PowerBand.B5_6, "Mind's Eye", 5,
                   "Every card an opponent draws is a card you may buy for one mana."),
        AnchorCard(PowerBand.B7_8, "Blue Sun's Zenith", 7,
                   "Draw as many cards as you have mana, at instant speed, and it "
                   "shuffles back in."),
        AnchorCard(PowerBand.B9_10, "Necropotence", 10,
                   "Life becomes cards, one for one, with no cap."),
    ],
))


# Role.CARD_SELECTION -- Even until the top: the 7-8 to 9-10 jump is the
# ladder's weakest, since Sylvan Library is a draw engine that selects rather
# than pure selection.
register(AnchorLadder(
    tag=Role.CARD_SELECTION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Soothsaying", 1,
                   "Pay mana to rearrange the top of your library -- no card, no "
                   "tempo, just order."),
        AnchorCard(PowerBand.B3_4, "Sleight of Hand", 4,
                   "One mana: look at two cards, keep one, bottom the other."),
        AnchorCard(PowerBand.B5_6, "Ponder", 6,
                   "One mana: reorder the top three, shuffle if you want, then draw."),
        AnchorCard(PowerBand.B7_8, "Mirri's Guile", 7,
                   "One mana, and every upkeep you arrange your next three draws."),
        AnchorCard(PowerBand.B9_10, "Sylvan Library", 9,
                   "Two extra cards every draw step; keep them for four life each."),
    ],
))


# Role.TUTOR -- Even. Axis is cost and breadth: unusable engine -> overpriced
# unconditional -> one mana with a real cost attached -> one mana to the top of
# the library -> two mana straight to hand.
register(AnchorLadder(
    tag=Role.TUTOR,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Rhystic Tutor", 1,
                   "An unconditional tutor -- unless any of three opponents pays two "
                   "mana."),
        AnchorCard(PowerBand.B3_4, "Diabolic Tutor", 4,
                   "Four mana, any card, straight to hand, no condition."),
        AnchorCard(PowerBand.B5_6, "Eladamri's Call", 6,
                   "Two mana, instant speed, any creature card into hand."),
        AnchorCard(PowerBand.B7_8, "Diabolic Intent", 8,
                   "Two mana and a creature you were done with, for any card in the "
                   "deck."),
        AnchorCard(PowerBand.B9_10, "Survival of the Fittest", 9,
                   "One green mana and a discard turns any creature in the deck into "
                   "any other, at instant speed, repeatedly."),
    ],
))


# Role.RECURSION -- Even. Axis: one creature to hand -> a body with a creature
# attached -> any card to hand -> the same on a body, in the color that reuses
# it best -> an engine that returns cards from the graveyard every turn.
# Graveyard-to-BATTLEFIELD effects are Role.REANIMATION and are deliberately
# absent -- that boundary is what displaced the old 9-10 rung.
register(AnchorLadder(
    tag=Role.RECURSION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Raise Dead", 2,
                   "One creature card from your graveyard back to hand, and nothing "
                   "else."),
        AnchorCard(PowerBand.B3_4, "Gravedigger", 4,
                   "A 2/2 body that brings a creature card back with it."),
        AnchorCard(PowerBand.B5_6, "Regrowth", 6,
                   "Two mana, any card type, from your graveyard to your hand."),
        AnchorCard(PowerBand.B7_8, "Eternal Witness", 7,
                   "Regrowth on a body, in the color that reuses it best."),
        AnchorCard(PowerBand.B9_10, "Genesis", 9,
                   "Five mana, and from then on, from the graveyard, {2}{G} each "
                   "upkeep returns a creature card to your hand -- forever."),
    ],
))


# Role.GRAVEYARD_HATE -- Even. Axis: one card exiled -> one graveyard, once ->
# a cheap static shutoff of what graveyards do -> total exile, static -> total,
# static, one-sided, and free on turn zero.
register(AnchorLadder(
    tag=Role.GRAVEYARD_HATE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Crypt Incursion", 2,
                   "Three mana to exile the creatures out of one graveyard and gain a "
                   "pile of life."),
        AnchorCard(PowerBand.B3_4, "Tormod's Crypt", 4,
                   "Free to cast, and it exiles one player's graveyard when you need "
                   "it to."),
        AnchorCard(PowerBand.B5_6, "Scavenger Grounds", 5,
                   "A land that can exile every graveyard on the table once."),
        AnchorCard(PowerBand.B7_8, "Bojuka Bog", 7,
                   "A land that exiles an opponent's graveyard when it enters."),
        AnchorCard(PowerBand.B9_10, "Ashiok, Dream Render", 9,
                   "Exiles a player's whole graveyard every turn, and opponents cannot "
                   "search their libraries at all."),
    ],
))


# Role.PROTECTION -- Even. Axis: one creature, one turn -> repeatable on one
# creature -> the whole team -> the whole board against everything -> your
# board, your life total, and the turn cycle.
register(AnchorLadder(
    tag=Role.PROTECTION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Ranger's Guile", 1,
                   "One mana: a single creature gets +1/+1 and hexproof until end of "
                   "turn."),
        AnchorCard(PowerBand.B3_4, "Rootborn Defenses", 3,
                   "Team-wide indestructible for three, plus populate."),
        AnchorCard(PowerBand.B5_6, "Selfless Spirit", 6,
                   "A 2/1 flier that can be sacrificed to make your whole team "
                   "indestructible."),
        AnchorCard(PowerBand.B7_8, "Mother of Runes", 8,
                   "Every turn, one creature becomes untouchable by the color of your "
                   "choice."),
        AnchorCard(PowerBand.B9_10, "Veil of Summer", 9,
                   "One mana: your spells cannot be countered, and you and your "
                   "permanents cannot be targeted by blue or black this turn."),
    ],
))


# Role.COUNTERSPELL -- Even. Axis: overpriced -> conditional at two ->
# unconditional at two -> free -> free-adjacent and it ramps you.
register(AnchorLadder(
    tag=Role.COUNTERSPELL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Induce Paranoia", 1,
                   "Four mana to counter one spell, plus some mill if you paid black."),
        AnchorCard(PowerBand.B3_4, "Dissipate", 4,
                   "Three mana, counter anything, and the spell is exiled instead of "
                   "buried."),
        AnchorCard(PowerBand.B5_6, "Counterspell", 6,
                   "Two mana, counter anything, no conditions."),
        AnchorCard(PowerBand.B7_8, "Mystic Confluence", 7,
                   "Five mana, three modes: counter, bounce, draw -- pick any mix."),
        AnchorCard(PowerBand.B9_10, "Mana Drain", 9,
                   "Counter any spell, then add that spell's mana value as colorless "
                   "on your next main phase."),
    ],
))


# Role.STAX_PIECE -- Even. Axis: taxes one player -> delays everyone slightly
# -> taxes spell count -> squeezes mana every turn -> rewrites what a deck's
# lands and untap step do. The 5-6 rung was an attack tax (Ghostly Prison),
# which is a deterrent first and a stax piece second; with that card moved to
# Theme.PILLOW_FORT a spell tax takes the band, and the ladder's axis reads as
# taxes on casting rather than on attacking.
register(AnchorLadder(
    tag=Role.STAX_PIECE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Squeeze", 1,
                   "Four mana: sorcery spells cost three more."),
        AnchorCard(PowerBand.B3_4, "Root Maze", 3,
                   "Artifacts and lands enter tapped -- yours included."),
        AnchorCard(PowerBand.B5_6, "Thorn of Amethyst", 6,
                   "Noncreature spells cost one more -- for everyone, including you."),
        AnchorCard(PowerBand.B7_8, "Archon of Emeria", 7,
                   "One spell per turn for everyone, and opponents' nonbasic lands "
                   "come in tapped."),
        AnchorCard(PowerBand.B9_10, "The Tabernacle at Pendrell Vale", 9,
                   "Every creature on the battlefield costs its controller one mana "
                   "each upkeep or dies."),
    ],
))


# Role.LAND_DESTRUCTION -- Mostly even; the tightest joint is 1-2 to 3-4, where
# Demolish is simply Stone Rain for one more mana -- an honest but small step.
# Axis: overpriced one-for-one -> on-rate one-for-one -> land-slot answer ->
# free and unconditional -> the whole table's mana at once.
register(AnchorLadder(
    tag=Role.LAND_DESTRUCTION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Craterize", 1,
                   "Four mana. Destroy target land. That is the whole card."),
        AnchorCard(PowerBand.B3_4, "Stone Rain", 3,
                   "Three mana, destroy one land."),
        AnchorCard(PowerBand.B5_6, "Sinkhole", 6,
                   "Two mana, destroy any land."),
        AnchorCard(PowerBand.B7_8, "Strip Mine", 8,
                   "A colorless land that sacrifices to destroy any land, with no "
                   "replacement offered."),
        AnchorCard(PowerBand.B9_10, "Jokulhaups", 9,
                   "Destroy every artifact, creature, and land."),
    ],
))


# Role.HAND_DISRUPTION -- The registry's weakest top band: hand disruption
# scales badly against three opponents, so even the 9-10 rung answers one
# player. Rungs 1-2 through 7-8 are evenly spaced, with the axis turning at 5-6
# from 'one opponent, one card' to 'the whole table's hand'; 7-8 to 9-10 is a
# jump in scale, not in kind.
register(AnchorLadder(
    tag=Role.HAND_DISRUPTION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Necrogen Spellbomb", 1,
                   "One mana, then a black mana, to make one player discard one card "
                   "at random."),
        AnchorCard(PowerBand.B3_4, "Hymn to Tourach", 4,
                   "Two mana, two cards at random, from one player."),
        AnchorCard(PowerBand.B5_6, "Thoughtseize", 5,
                   "One mana, any nonland card from one player's hand, for two life."),
        AnchorCard(PowerBand.B7_8, "Sire of Insanity", 7,
                   "At every end step, each player discards their hand."),
        AnchorCard(PowerBand.B9_10, "Mind Twist", 9,
                   "{X}{B}: one player discards X cards at random."),
    ],
))


# Role.TOKEN_GENERATOR -- Even. Axis: two small bodies once -> two small bodies
# at a good rate -> four bodies across two cards -> a body every turn forever
# -> a game-ending board from one card.
register(AnchorLadder(
    tag=Role.TOKEN_GENERATOR,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Sarpadian Empires, Vol. VII", 2,
                   "Five mana down, then three more per 1/1 -- the worst rate in the "
                   "format."),
        AnchorCard(PowerBand.B3_4, "Dragon Fodder", 4,
                   "Two 1/1 Goblins for two mana at sorcery speed."),
        AnchorCard(PowerBand.B5_6, "Secure the Wastes", 6,
                   "X 1/1 Warriors at instant speed."),
        AnchorCard(PowerBand.B7_8, "Bitterblossom", 8,
                   "One evasive 1/1 flier every upkeep, forever, for two mana and a "
                   "life a turn."),
        AnchorCard(PowerBand.B9_10, "Grave Titan", 9,
                   "Ten power across five bodies for six mana, and two more Zombies "
                   "every attack."),
    ],
))


# Role.ANTHEM -- Even. Axis: +0/+1 -> +1/+1 -> +2/+2 -> keywords that change
# how combat works -> a static pump big enough that the board it sits on wins
# games. Every rung pumps while it merely sits there -- that, and not
# permanence, is the line against Role.MASS_PUMP, where a permanent whose pump
# exists only during an attack belongs (Shared Animosity moved there).
register(AnchorLadder(
    tag=Role.ANTHEM,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Veteran Armorer", 1,
                   "Two mana for a 1/2 that gives your other creatures +0/+1."),
        AnchorCard(PowerBand.B3_4, "Glorious Anthem", 4,
                   "Three mana, +1/+1 to your whole team, permanently."),
        AnchorCard(PowerBand.B5_6, "Dictate of Heliod", 6,
                   "+2/+2 to your team, with flash."),
        AnchorCard(PowerBand.B7_8, "Eldrazi Monument", 7,
                   "+1/+1, flying and indestructible for your whole team, for five "
                   "colorless, paid for with a creature every upkeep."),
        AnchorCard(PowerBand.B9_10, "Beastmaster Ascension", 9,
                   "Three mana, and after seven attack triggers it sits there giving "
                   "the whole team +5/+5 -- a wide board stops needing anything else."),
    ],
))


# Role.EXTRA_COMBAT -- Even. Axis: one extra combat, overpriced -> one extra
# combat on rate -> a modal, instant-speed version -> an extra combat every
# turn attached to a body -> an unbounded number of them.
register(AnchorLadder(
    tag=Role.EXTRA_COMBAT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Fury of the Horde", 2,
                   "Seven mana, or exile two red cards from your hand, for one extra "
                   "combat."),
        AnchorCard(PowerBand.B3_4, "Relentless Assault", 4,
                   "Four mana: untap your attackers and take an extra combat."),
        AnchorCard(PowerBand.B5_6, "Seize the Day", 5,
                   "Untap one creature and take an extra combat, for four; flashback "
                   "for three more."),
        AnchorCard(PowerBand.B7_8, "Aurelia, the Warleader", 8,
                   "A 3/4 flying, vigilant, hasty body that grants an extra combat the "
                   "first time she attacks each turn."),
        AnchorCard(PowerBand.B9_10, "Aggravated Assault", 9,
                   "{3}{R}{R}: untap all your creatures and take an extra combat -- as "
                   "many times as you can pay."),
    ],
))


# Role.EXTRA_TURNS -- Even. Axis: an extra turn that kills you -> one that
# costs a whole turn's mana -> one with a real restriction -> the clean five-
# mana version -> one that never leaves your deck.
register(AnchorLadder(
    tag=Role.EXTRA_TURNS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Chance for Glory", 2,
                   "Indestructible for the team, an extra turn, and then you lose."),
        AnchorCard(PowerBand.B3_4, "Beacon of Tomorrows", 4,
                   "Eight mana for one extra turn, and it shuffles itself back into "
                   "your library."),
        AnchorCard(PowerBand.B5_6, "Time Sieve", 6,
                   "Sacrifice five artifacts for an extra turn, as often as you can "
                   "rebuild."),
        AnchorCard(PowerBand.B7_8, "Temporal Trespass", 7,
                   "An eight-mana extra turn that usually costs three."),
        AnchorCard(PowerBand.B9_10, "Magistrate's Scepter", 9,
                   "Three charge counters, an extra turn, repeat -- and any untapper "
                   "makes it infinite."),
    ],
))


# Role.LIFEGAIN -- Even, with the caveat that the top rung earns its place by
# converting life into a kill rather than by gaining the most. Gaining life
# alone never reaches the top band.
register(AnchorLadder(
    tag=Role.LIFEGAIN,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Healing Salve", 1,
                   "Three life, once, for one mana."),
        AnchorCard(PowerBand.B3_4, "Angelic Accord", 3,
                   "Gain four in a turn and get a 4/4 flying Angel at end step."),
        AnchorCard(PowerBand.B5_6, "Soul Warden", 5,
                   "One life every time another creature enters, from any player."),
        AnchorCard(PowerBand.B7_8, "Sanguine Sacrament", 8,
                   "Gain twice X, then tuck it to do it again later."),
        AnchorCard(PowerBand.B9_10, "Exquisite Blood", 9,
                   "Every point of life any opponent loses, you gain -- passively, "
                   "with no cap and no trigger of your own required."),
    ],
))


# Role.THEFT -- Even. Axis: borrow a creature for a turn -> keep a permanent
# for six mana -> keep a creature for four -> keep anything for two -> take a
# whole turn, or the whole table's best permanents.
register(AnchorLadder(
    tag=Role.THEFT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Act of Aggression", 2,
                   "The same borrow, with Phyrexian mana shaving the cost."),
        AnchorCard(PowerBand.B3_4, "Ray of Command", 3,
                   "Four mana at instant speed to take a creature for the turn, "
                   "untapped."),
        AnchorCard(PowerBand.B5_6, "Blatant Thievery", 6,
                   "One permanent from each opponent, any type."),
        AnchorCard(PowerBand.B7_8, "Bribery", 8,
                   "Search an opponent's library and put the best thing in it onto the "
                   "battlefield under your control."),
        AnchorCard(PowerBand.B9_10, "Mindslaver", 9,
                   "You control an opponent's entire next turn: their cards, their "
                   "attacks, their sacrifices."),
    ],
))


# Role.MANA_ROCK -- Net mana added, and how soon: an artifact that produces
# mana and nets you none -> one extra mana, a turn or two late -> one on curve,
# or two for three -> two or three mana for one to four -> free, or three off
# one, fast enough to buy a turn nobody else gets. Weakest joint is 7-8 to
# 9-10, where the axis stops being rate and becomes what the rate makes
# possible.
register(AnchorLadder(
    tag=Role.MANA_ROCK,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Mana Cylix", 1,
                   "An artifact that produces mana and nets you none -- it changes one "
                   "mana's color, one at a time."),
        AnchorCard(PowerBand.B3_4, "Guardian Idol", 4,
                   "Two mana, enters tapped, taps for one colorless -- and becomes a "
                   "2/2 for {2} when mana stops mattering."),
        AnchorCard(PowerBand.B5_6, "Worn Powerstone", 5,
                   "Three mana for two a turn, but it enters tapped, so the second "
                   "mana arrives a turn later."),
        AnchorCard(PowerBand.B7_8, "Sol Ring", 8,
                   "One mana for an artifact that taps for two -- it pays itself back "
                   "the turn it lands and every turn after."),
        AnchorCard(PowerBand.B9_10, "Grim Monolith", 9,
                   "Two mana for three colorless, repeatable for {4} -- fast mana that "
                   "funds a whole turn by itself."),
    ],
))


# Role.MANA_DORK -- Mana per turn against what the body costs: four mana for
# one green (1-2) -> two mana for one green per wall you control (3-4) -> one
# mana for one green, on turn one (5-6) -> two mana for any color, behind
# hexproof (7-8) -> two mana for one green per Elf on the table (9-10). Every
# rung is a nonartifact creature, which is the line against Role.MANA_ROCK: an
# artifact that taps for mana grades there, and no rung on either ladder is an
# artifact creature answering to both. Weakest joint is 5-6 to 7-8, where the
# axis stops being cost and becomes flexibility.
register(AnchorLadder(
    tag=Role.MANA_DORK,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Skyshroud Troopers", 1,
                   "Four mana for a 3/3 whose tap adds one green -- it costs more than "
                   "the acceleration it is buying."),
        AnchorCard(PowerBand.B3_4, "Overgrown Battlement", 4,
                   "Two mana for a 0/4 that taps for one green per defender you "
                   "control."),
        AnchorCard(PowerBand.B5_6, "Llanowar Elves", 6,
                   "One mana on turn one for a body that taps for green every turn "
                   "after -- the rate every other dork is read against."),
        AnchorCard(PowerBand.B7_8, "Sylvan Caryatid", 7,
                   "Two mana for a hexproof defender that taps for any color -- ramp "
                   "that spot removal cannot touch."),
        AnchorCard(PowerBand.B9_10, "Priest of Titania", 9,
                   "Two mana for a creature that taps for one green per Elf on the "
                   "battlefield -- every Elf, not just yours."),
    ],
))


# Role.LAND_RAMP -- How many lands reach the battlefield, how fast, and what it
# costs to get them: three or four mana for one tapped basic -> one or two
# basics at full price -> two mana for a land plus a body or a second card ->
# an untapped dual, or two Forests, for two to four -> every land in your
# graveyard at once. Two kinds of card are deliberately absent, both because
# they grade a different property: extra-land-drop effects (Exploration, Azusa)
# permit a drop rather than putting a land onto the battlefield, and are graded
# under Theme.LANDS_MATTER; land tutors that sacrifice the land they replace
# (Crop Rotation) net no lands at all, which is why one held this ladder's top
# rung and no longer does. Weakest joint is 5-6 to 7-8.
register(AnchorLadder(
    tag=Role.LAND_RAMP,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Untamed Wilds", 2,
                   "Three mana for a single untapped basic -- one land, at a full mana "
                   "over the going rate for it."),
        AnchorCard(PowerBand.B3_4, "Burnished Hart", 3,
                   "Six mana across two turns for two tapped basics, in any deck at "
                   "all."),
        AnchorCard(PowerBand.B5_6, "Cultivate", 6,
                   "Three mana: one basic onto the battlefield tapped and a second one "
                   "into your hand."),
        AnchorCard(PowerBand.B7_8, "Skyshroud Claim", 7,
                   "Four mana for two Forest cards onto the battlefield untapped."),
        AnchorCard(PowerBand.B9_10, "Splendid Reclamation", 9,
                   "Four mana: every land card in your graveyard returns to the "
                   "battlefield at once -- a land count no other rung can reach, in a "
                   "deck built to fill the yard."),
    ],
))


# Role.RITUAL -- Net mana off one card, right now: a one-shot that nets nothing
# and only moves mana between turns -> one extra mana, or none plus a body ->
# two extra mana, or three once a condition is met -> two or three extra mana
# off a one-mana card -> an unbounded amount off one card, scaled by something
# the table or the graveyard gives you. Weakest joint is 3-4 to 5-6, where the
# step is a single mana. Burst mana deliberately does not count as ramp:
# nothing on this ladder produces a second time.
register(AnchorLadder(
    tag=Role.RITUAL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Vessel of Volatility", 1,
                   "Two mana to cast and two more to sacrifice, for four red -- it "
                   "stores mana across turns and adds none."),
        AnchorCard(PowerBand.B3_4, "Priest of Gix", 3,
                   "Three mana for a 2/1 that adds three black when it enters -- the "
                   "mana comes straight back, the body stays."),
        AnchorCard(PowerBand.B5_6, "Rite of Flame", 5,
                   "One mana for two red, plus one more for each copy already in any "
                   "graveyard."),
        AnchorCard(PowerBand.B7_8, "Lotus Petal", 7,
                   "A free artifact you sacrifice for one mana of any color -- a "
                   "stored ritual that costs nothing to hold."),
        AnchorCard(PowerBand.B9_10, "Songs of the Damned", 9,
                   "One mana: add one black for each creature card in your graveyard."),
    ],
))


# Role.IMPULSE_DRAW -- Cards seen per card spent, and how long you have to use
# them: two cards with a condition and a one-turn window (1-2) -> one or three
# cards that expire at end of turn (3-4) -> two cards for two mana until your
# next end step, or X cards at instant speed (5-6) -> a permanent that exiles
# one card every turn (7-8) -> an engine that exiles every turn and converts
# the exiled cards into mana as well (9-10). Not true card advantage: every
# rung still has to pay the card's cost before the window closes, which is the
# whole reason this is a separate role from Role.CARD_DRAW. Weakest joint is
# 5-6 to 7-8.
register(AnchorLadder(
    tag=Role.IMPULSE_DRAW,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Clockwork Percussionist", 1,
                   "A one-mana hasty body that exiles one card when it dies, playable "
                   "until the end of your next turn."),
        AnchorCard(PowerBand.B3_4, "Abbot of Keral Keep", 4,
                   "Two mana for a prowess body that exiles one card you may play this "
                   "turn."),
        AnchorCard(PowerBand.B5_6, "Commune with Lava", 6,
                   "X plus two mana at instant speed: exile X cards, playable until "
                   "the end of your next turn."),
        AnchorCard(PowerBand.B7_8, "Chandra, Torch of Defiance", 8,
                   "A four-mana planeswalker whose first ability exiles a card to "
                   "cast, or burns each opponent for two if you do not."),
        AnchorCard(PowerBand.B9_10, "Etali, Primal Storm", 9,
                   "Six mana: every attack exiles the top card of every library and "
                   "lets you cast those spells for free."),
    ],
))


# Role.ARTIFACT_ENCHANTMENT_REMOVAL -- Breadth of what it can answer, and what
# the answer costs: one type only, all of them at once, your own included
# (1-2) -> one type, on rate (3-4) -> either type, two mana, instant (5-6) ->
# either type
# with exile, split second, or every artifact at once (7-8) -> every artifact
# and enchantment on the table, or a destroy trigger that repeats all game
# (9-10). Targeted creature removal is Role.REMOVAL and is deliberately absent
# -- Assassin's Trophy stays there.
register(AnchorLadder(
    tag=Role.ARTIFACT_ENCHANTMENT_REMOVAL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Tranquil Domain", 1,
                   "Two mana to destroy all non-Aura enchantments -- it cannot touch an "
                   "artifact at all, and it takes your own enchantments with it."),
        AnchorCard(PowerBand.B3_4, "Shatter", 4,
                   "Two mana, instant, destroys an artifact -- and nothing else."),
        AnchorCard(PowerBand.B5_6, "Hull Breach", 6,
                   "Two mana, and the third mode destroys an artifact AND an "
                   "enchantment at once."),
        AnchorCard(PowerBand.B7_8, "Revoke Existence", 7,
                   "Two mana to exile an artifact or enchantment, so nothing comes "
                   "back."),
        AnchorCard(PowerBand.B9_10, "Aura Shards", 9,
                   "Every creature you play may destroy an artifact or enchantment an "
                   "opponent controls -- forever."),
    ],
))


# Role.BOUNCE -- What goes back, at what price, and whether the tempo is
# actually yours: six mana for one creature (1-2) -> one or two mana for one
# creature, sorcery speed or no upside (3-4) -> two mana with a card or a body
# attached (5-6) -> repeatable, or one player's whole board (7-8) -> everyone's
# board, or every land, at once (9-10). Bounce is tempo rather than removal, so
# the axis is how many turns it buys and from how many players; blink effects
# that return a permanent to the battlefield are Theme.BLINK and are absent.
register(AnchorLadder(
    tag=Role.BOUNCE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Swirling Torrent", 1,
                   "Six mana to put a creature on top of its owner's library, bounce "
                   "one to hand, or do both to two different creatures."),
        AnchorCard(PowerBand.B3_4, "Symbol of Unsummoning", 3,
                   "Three mana at sorcery speed: bounce one creature and draw a card."),
        AnchorCard(PowerBand.B5_6, "Man-o'-War", 6,
                   "Three mana for a 2/2 whose arrival returns a creature to its "
                   "owner's hand."),
        AnchorCard(PowerBand.B7_8, "River's Rebuke", 8,
                   "Six mana: every nonland permanent one player controls goes back to "
                   "their hand."),
        AnchorCard(PowerBand.B9_10, "Cyclonic Rift", 10,
                   "For seven mana at instant speed, every nonland permanent your "
                   "opponents control goes back to hand -- and yours stay."),
    ],
))


# Role.REANIMATION -- What the spell costs against what it can reach: four or
# five mana for one creature out of your own graveyard -> three or four mana,
# still sorcery speed -> two or three mana with a condition or a sacrifice
# attached -> one or two mana for any creature in any graveyard -> every
# creature in every graveyard at once. The axis is cost per body, which is why
# the notes have to carry that cost IS the power here. Graveyard-to-HAND
# effects belong to Role.RECURSION and are deliberately absent. Weakest joint
# is 5-6 to 7-8.
register(AnchorLadder(
    tag=Role.REANIMATION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Miraculous Recovery", 2,
                   "Five mana for one creature out of your own graveyard, at instant "
                   "speed with a +1/+1 counter -- the worst rate on the ladder."),
        AnchorCard(PowerBand.B3_4, "Zombify", 4,
                   "Four mana, any creature in your graveyard, onto the battlefield."),
        AnchorCard(PowerBand.B5_6, "Victimize", 6,
                   "Three mana, sacrifice one creature, get two back -- and the "
                   "sacrificed creature is often something you wanted in the graveyard "
                   "anyway."),
        AnchorCard(PowerBand.B7_8, "Reanimate", 8,
                   "One mana for any creature in any graveyard, paid for in life equal "
                   "to its mana value."),
        AnchorCard(PowerBand.B9_10, "Rise of the Dark Realms", 9,
                   "Nine mana: every creature card in every graveyard onto the "
                   "battlefield under your control."),
    ],
))


# Role.CLONE -- What it can copy and what it leaves behind: a copy of something
# you already control, or one that dies with its enchantment (1-2) -> any
# creature, for four or five mana (3-4) -> any creature for two or three, or
# one out of a graveyard (5-6) -> any permanent, or an artifact, for three or
# four (7-8) -> five copies at once, or a copy every single turn (9-10). The
# axis is breadth times repeatability; the body's own stats never matter, since
# it has none until it copies something.
register(AnchorLadder(
    tag=Role.CLONE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Mirror Image", 2,
                   "Three mana, and it can only copy a creature you already control."),
        AnchorCard(PowerBand.B3_4, "Sculpting Steel", 3,
                   "Three mana to enter as a copy of any artifact on the battlefield."),
        AnchorCard(PowerBand.B5_6, "Cackling Counterpart", 5,
                   "Three mana at instant speed for a token copy of a creature you "
                   "control, and it can be cast again from the graveyard."),
        AnchorCard(PowerBand.B7_8, "Clever Impersonator", 7,
                   "Four mana to copy any nonland permanent on the battlefield -- "
                   "enchantment, artifact, planeswalker, anything."),
        AnchorCard(PowerBand.B9_10, "Kiki-Jiki, Mirror Breaker", 9,
                   "Five mana, then tap: a hasty token copy of a nonlegendary creature "
                   "you control, every turn."),
    ],
))


# Role.ALT_WIN_CONDITION -- How plausible the condition is, and how much of the
# deck it costs to meet it: a condition no real game produces (1-2) -> a
# condition a dedicated deck meets eventually, slowly (3-4) -> a condition the
# deck was already going to meet, but which dies to a removal spell (5-6) -> a
# condition a whole archetype can be built toward and defended (7-8) -> two
# mana and an empty library, or a spell you simply cast twice (9-10). Every
# rung says you win the game on the card itself, which is what makes this role
# decidable at all.
register(AnchorLadder(
    tag=Role.ALT_WIN_CONDITION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Barren Glory", 1,
                   "You win if you control no permanents other than this and have no "
                   "cards in hand -- a condition you have to build a whole deck to "
                   "fake."),
        AnchorCard(PowerBand.B3_4, "Coalition Victory", 4,
                   "Eight mana in all five colors: win if you control a land of each "
                   "basic type and a creature of each color."),
        AnchorCard(PowerBand.B5_6, "Laboratory Maniac", 5,
                   "Instead of losing to an empty library, you win -- if the Maniac is "
                   "still on the battlefield when you draw."),
        AnchorCard(PowerBand.B7_8, "Hellkite Tyrant", 7,
                   "A six-mana flying trampler that steals artifacts on damage and "
                   "wins if you control twenty."),
        AnchorCard(PowerBand.B9_10, "Approach of the Second Sun", 9,
                   "Seven mana, gain seven life, and the spell goes seventh from the "
                   "top -- cast it a second time and you win the game."),
    ],
))


# Role.MASS_PUMP -- How much the team gains for one card, and whether it
# converts a board into a kill: +1/+0 or +2/+0 to attackers only (1-2) -> +2/+2
# for three mana (3-4) -> +3/+3 and trample, the Overrun rate (5-6) -> a
# permanent that pumps every combat, or +X/+X off your biggest creature (7-8)
# -> a pump that turns any board into lethal the turn it lands (9-10). The pump
# lasts one turn on every rung: a permanent may sit here when its pump exists
# only during an attack (Shared Animosity), but one that pumps while it merely
# sits there is Role.ANTHEM (Beastmaster Ascension moved there). Weakest joint
# is 7-8 to 9-10.
register(AnchorLadder(
    tag=Role.MASS_PUMP,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Chorus of Woe", 1,
                   "One mana for +1/+0 to your creatures until end of turn."),
        AnchorCard(PowerBand.B3_4, "Rally the Peasants", 4,
                   "Three mana for +2/+0, and it can be cast a second time from your "
                   "graveyard."),
        AnchorCard(PowerBand.B5_6, "Overrun", 6,
                   "Five mana: +3/+3 and trample to your whole team -- the rate every "
                   "other mass pump is measured against."),
        AnchorCard(PowerBand.B7_8, "Shared Animosity", 8,
                   "Three mana that pumps on every attack instead of once, but only "
                   "while attacking and only for same-type attackers: a wide tribal "
                   "board turns lethal, a mixed one barely moves."),
        AnchorCard(PowerBand.B9_10, "Craterhoof Behemoth", 9,
                   "Eight mana for a haste trampler that gives your whole team trample "
                   "and +X/+X, where X is how many creatures you control."),
    ],
))


# Role.LIFE_DRAIN -- Life taken off the whole table per card, and how often:
# one life per opponent behind a condition nobody meets (1-2) -> one per
# opponent, repeatable, two or three mana a go (3-4) -> three per opponent
# once, or one per opponent as a side effect of what the deck already does
# (5-6) -> five or more per opponent off one card (7-8) -> every opponent's
# life total at once, scaled by mana (9-10). Judged in a four-player pod, where
# "each opponent loses" is three times the number printed -- which is the whole
# reason this role grades differently from Role.DIRECT_DAMAGE.
register(AnchorLadder(
    tag=Role.LIFE_DRAIN,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Blight Pile", 1,
                   "Three mana a use: each opponent loses one life per creature with "
                   "defender you control -- usually just this one."),
        AnchorCard(PowerBand.B3_4, "Archers' Parapet", 4,
                   "A 0/5 defender that drains one from each opponent for two mana and "
                   "a tap."),
        AnchorCard(PowerBand.B5_6, "Blood Tithe", 5,
                   "Four mana: each opponent loses three life and you gain all of it "
                   "-- nine life swing at a four-player table."),
        AnchorCard(PowerBand.B7_8, "Kokusho, the Evening Star", 8,
                   "When this dies, each opponent loses five life and you gain that "
                   "much -- fifteen life swung by a creature dying."),
        AnchorCard(PowerBand.B9_10, "Debt to the Deathless", 9,
                   "Each opponent loses twice X life and you gain all of it -- six "
                   "times X at a four-player table."),
    ],
))


# Role.DIRECT_DAMAGE -- Damage per card, and what it can point at: one to three
# damage with a condition or a land sacrificed (1-2) -> two to four damage for
# two or three mana (3-4) -> three damage for one mana, or X damage at one
# target (5-6) -> X damage split across several targets, or a repeatable pinger
# (7-8) -> X damage to every creature and every player at once (9-10). Judged
# at 40 life in a pod, which is why burn aimed at faces grades low and scalable
# or board-wide damage grades high. Weakest joint is 5-6 to 7-8.
register(AnchorLadder(
    tag=Role.DIRECT_DAMAGE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Needle Drop", 1,
                   "One damage, only to something already damaged this turn, and it "
                   "draws a card."),
        AnchorCard(PowerBand.B3_4, "Shock", 4,
                   "One mana, two damage to any target."),
        AnchorCard(PowerBand.B5_6, "Lightning Bolt", 6,
                   "One mana, three damage, any target, instant."),
        AnchorCard(PowerBand.B7_8, "Walking Ballista", 8,
                   "A colorless creature that stores X counters and converts each one "
                   "into one damage at any target, at will."),
        AnchorCard(PowerBand.B9_10, "Earthquake", 9,
                   "X damage to every creature without flying and to every player, for "
                   "X plus one mana."),
    ],
))


# Theme.ARISTOCRATS -- Even steps: one-shot drain -> overcosted repeatable ->
# cheap repeatable -> the two-mana standard -> a card that turns any sac outlet
# into a kill. Weakest joint is 7-8 to 9-10, where the ladder changes from
# 'drain payoff' to 'combo engine'.
register(AnchorLadder(
    tag=Theme.ARISTOCRATS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Dying Wish", 2,
                   "An Aura that pays off only when the creature it is on dies -- card "
                   "disadvantage for one drain."),
        AnchorCard(PowerBand.B3_4, "Falkenrath Noble", 4,
                   "The repeatable drain effect at five mana instead of two -- right "
                   "ability, wrong rate."),
        AnchorCard(PowerBand.B5_6, "Bastion of Remembrance", 6,
                   "Repeatable drain on an enchantment, so creature removal can't turn "
                   "it off, plus a body to sacrifice."),
        AnchorCard(PowerBand.B7_8, "Blood Artist", 8,
                   "Two mana, and it drains on *every* creature death on the table, "
                   "not just yours -- a board wipe becomes a twenty-point swing."),
        AnchorCard(PowerBand.B9_10, "Grave Pact", 9,
                   "Every creature you sacrifice makes all three opponents sacrifice "
                   "too -- an aristocrats deck's sac outlet becomes a repeatable table "
                   "wipe."),
    ],
))


# Theme.VOLTRON -- Reads evenly as 'how much closer does this get one creature
# to killing a player': raw stats -> stats plus keywords -> evasion -> evasion
# plus protection plus value -> lethal on its own. This ladder collects cards
# that stack a single creature by any method -- Equipment, Aura or keyword --
# and is weighted to Equipment; two rungs are Auras and stay that way.
# Theme.AURAS asks the same question of Auras alone, so the two ladders are
# deliberately not disjoint and an Aura can carry both tags. The old 1-2 rung
# went to Theme.AURAS in that split; the two Sword rungs stayed.
register(AnchorLadder(
    tag=Theme.VOLTRON,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Bone Saw", 1,
                   "A free Equipment that costs one mana to attach and gives +1/+0."),
        AnchorCard(PowerBand.B3_4, "Sword of Vengeance", 4,
                   "Six mana across cast and equip for +2/+0 and four keywords, none "
                   "of which is evasion or protection."),
        AnchorCard(PowerBand.B5_6, "Aether Tunnel", 5,
                   "Unconditional unblockable for two mana -- the cheapest honest "
                   "version of the evasion rung."),
        AnchorCard(PowerBand.B7_8, "Sword of Feast and Famine", 8,
                   "Protection from two colors dodges most removal and most blockers, "
                   "and untapping all your lands on the hit funds a second threat the "
                   "same turn."),
        AnchorCard(PowerBand.B9_10, "Eldrazi Conscription", 9,
                   "+10/+10, trample, and annihilator 2 -- one connection removes a "
                   "player from the game and strips two permanents doing it."),
    ],
))


# Theme.SPELLSLINGER -- Graded by what one instant or sorcery buys you: a
# temporary pump -> two damage -> a 2/2 flier -> a card -> a copy of the whole
# spell. Even, and the top rung is clearly a different order of effect.
register(AnchorLadder(
    tag=Theme.SPELLSLINGER,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Fire Urchin", 1,
                   "One power per spell, until end of turn -- the smallest possible "
                   "payoff."),
        AnchorCard(PowerBand.B3_4, "Trail of Evidence", 4,
                   "A Clue per spell -- card advantage at a rate you have to pay for "
                   "twice."),
        AnchorCard(PowerBand.B5_6, "Talrand, Sky Summoner", 6,
                   "Every instant or sorcery leaves a 2/2 flier behind, converting a "
                   "control shell's spells into a board."),
        AnchorCard(PowerBand.B7_8, "Archmage Emeritus", 8,
                   "Draws a card for every instant or sorcery you cast *or copy*, so "
                   "the deck stops running out of spells."),
        AnchorCard(PowerBand.B9_10, "Thousand-Year Storm", 9,
                   "The third spell of the turn resolves three times, the fourth four "
                   "-- one turn of ordinary spells becomes lethal."),
    ],
))


# Theme.TOKENS -- This ladder is the go-wide engine: cards that make or
# multiply a token board, not the individual token-maker slot. Even progression
# from one body to doubling to a finisher; the 5-6 rung (an anthem) is the odd
# effect type out.
register(AnchorLadder(
    tag=Theme.TOKENS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Tukatongue Thallid", 1,
                   "One extra 1/1, eventually -- the smallest unit of going wide."),
        AnchorCard(PowerBand.B3_4, "Sengir Autocrat", 3,
                   "Four bodies for four mana, with a drawback if it leaves."),
        AnchorCard(PowerBand.B5_6, "Intangible Virtue", 6,
                   "Every token you control, now and later, is a 2/2 with vigilance -- "
                   "the cheapest way to turn chaff into a threat."),
        AnchorCard(PowerBand.B7_8, "Anointed Procession", 8,
                   "The identical effect in white."),
        AnchorCard(PowerBand.B9_10, "Divine Visitation", 9,
                   "Every token you would make is a 4/4 flying vigilance Angel "
                   "instead."),
    ],
))


# Theme.BIG_MANA -- This ladder represents the ENGINE side of Big Mana --
# sources that produce outsized mana -- not the payload side (X spells, Genesis
# Wave). Mixing engines and payloads on one ladder would compare two different
# things; the payload side belongs to SynergyTag.X_SPELL and whichever payoff
# role the spell itself earns. Even steps, measured as mana produced per card.
register(AnchorLadder(
    tag=Theme.BIG_MANA,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Thran Turbine", 1,
                   "Two mana a turn that you are not allowed to spend on spells."),
        AnchorCard(PowerBand.B3_4, "Dreamstone Hedron", 3,
                   "Six mana for three a turn, cashable for three cards later."),
        AnchorCard(PowerBand.B5_6, "Magus of the Coffers", 6,
                   "Cabal Coffers on a body -- enormous mana in the deck built for it, "
                   "nothing anywhere else."),
        AnchorCard(PowerBand.B7_8, "Gilded Lotus", 7,
                   "Five mana for three of any one color a turn."),
        AnchorCard(PowerBand.B9_10, "Mana Reflection", 9,
                   "Doubles the mana from every permanent you tap."),
    ],
))


# Theme.REANIMATOR -- A reanimator deck needs a fat creature in the graveyard
# and a way to cash it in. This ladder grades how far a card advances that
# loop: moves creatures the wrong way, out of the graveyard (1-2) -> stocks the
# yard one card at a time, slowly (3-4) -> one card that finds a target and
# buries it (5-6) -> three creatures buried at once, or a target whose arrival
# takes over the game (7-8) -> one mana that puts the single best card in the
# deck exactly where the deck wants it (9-10). The individual graveyard-to-
# battlefield spells are Role.REANIMATION and are deliberately absent -- that
# split is what replaced this ladder's original cost curve, every rung of
# which graded a reanimation spell rather than the deck around it.
register(AnchorLadder(
    tag=Theme.REANIMATOR,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Vile Rebirth", 1,
                   "One mana to exile a creature card from a graveyard for a 2/2 token "
                   "-- it empties the yard the deck is trying to fill."),
        AnchorCard(PowerBand.B3_4, "Corpse Churn", 4,
                   "Two mana: mill three, then take one creature card from your "
                   "graveyard to your hand."),
        AnchorCard(PowerBand.B5_6, "Final Parting", 6,
                   "Five mana: one card to your hand and one card to your graveyard, "
                   "both chosen from your library."),
        AnchorCard(PowerBand.B7_8, "Archon of Cruelty", 8,
                   "Eight mana to cast, and every arrival or attack strips an opponent "
                   "of a creature, a card and three life while you gain both."),
        AnchorCard(PowerBand.B9_10, "Entomb", 9,
                   "One mana, instant speed: any card in your library straight into "
                   "your graveyard."),
    ],
))


# Theme.GROUP_HUG -- Graded by how much more the gift helps you than the three
# people you give it to. Honest but compressed: group hug has no ban-adjacent
# card, so the 9-10 rung is 'the card the archetype is built around' rather
# than a broken one.
register(AnchorLadder(
    tag=Theme.GROUP_HUG,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Vision Skeins", 2,
                   "Two cards for everyone, once, with nothing attached to make it "
                   "yours."),
        AnchorCard(PowerBand.B3_4, "Howling Mine", 4,
                   "The cheapest clean version of the effect, and it can be tapped "
                   "down on opponents' turns."),
        AnchorCard(PowerBand.B5_6, "Mikokoro, Center of the Sea", 5,
                   "A repeatable draw-for-everyone that costs you no card in your "
                   "deck."),
        AnchorCard(PowerBand.B7_8, "Heartbeat of Spring", 7,
                   "Doubles everyone's land mana, but you are the only player whose "
                   "deck is built to dump a hand in one turn."),
        AnchorCard(PowerBand.B9_10, "Kynaios and Tiro of Meletis", 9,
                   "Every one of your end steps draws you a card and lets the table "
                   "play lands -- you are the only player drawing every turn "
                   "unconditionally."),
    ],
))


# Theme.GROUP_SLUG -- Graded by damage per turn across three opponents against
# 120 total life. Even, and the 9-10 rung is the only card that converts the
# drip into a clock.
register(AnchorLadder(
    tag=Theme.GROUP_SLUG,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Dogged Pursuit", 1,
                   "Four mana for three damage a turn cycle, against 120 life on the "
                   "table."),
        AnchorCard(PowerBand.B3_4, "Ill-Gotten Inheritance", 4,
                   "Three damage per upkeep plus a four-damage sacrifice mode to "
                   "finish someone."),
        AnchorCard(PowerBand.B5_6, "Polluted Bonds", 6,
                   "Two life off every land an opponent plays, and two back to you."),
        AnchorCard(PowerBand.B7_8, "Painful Quandary", 8,
                   "Every opponent spell costs a card or five life -- the tax is large "
                   "enough that they pay in life and die to it."),
        AnchorCard(PowerBand.B9_10, "Nekusar, the Mindrazer", 9,
                   "Every player draws more, and every card an opponent draws is a "
                   "point of damage -- a single wheel deals fifteen to the table."),
    ],
))


# Theme.STAX -- The theme side: cards that make a resource-denial DECK work
# (asymmetry, lock engines, grind), as distinct from Role.STAX_PIECE's
# individual tax permanents. Even progression from a one-shot inconvenience to
# a hard lock; the 3-4 and 5-6 rungs are the least distinct pair.
register(AnchorLadder(
    tag=Theme.STAX,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Crack the Earth", 2,
                   "Everyone loses one permanent, once, and then it is over."),
        AnchorCard(PowerBand.B3_4, "Aether Barrier", 3,
                   "Every creature spell costs a permanent unless its caster pays one "
                   "more."),
        AnchorCard(PowerBand.B5_6, "Static Orb", 6,
                   "Nobody untaps more than two permanents a turn, which cuts the "
                   "whole table's mana to a trickle."),
        AnchorCard(PowerBand.B7_8, "Smokestack", 8,
                   "Every player sacrifices a permanent per soot counter each upkeep, "
                   "and you choose when to add counters -- the board grinds to nothing "
                   "while you keep tokens to feed it."),
        AnchorCard(PowerBand.B9_10, "Stasis", 9,
                   "Nobody untaps, ever, as long as you can pay one blue a turn -- the "
                   "game simply stops for everyone but the player who built around it."),
    ],
))


# Theme.ARTIFACTS -- Graded by what the deck's artifact count buys: a narrow
# anthem -> a colored cost reducer -> a colorless one -> converting artifacts
# into mana -> converting them into mana, a body and a free spell. Even, with a
# clear break at the top.
register(AnchorLadder(
    tag=Theme.ARTIFACTS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Somber Hoverguard", 1,
                   "Its whole artifact synergy is being cheaper -- the payoff is a "
                   "vanilla 3/4 flier."),
        AnchorCard(PowerBand.B3_4, "Tempered Steel", 4,
                   "A +2/+2 anthem, but only for the artifact creatures."),
        AnchorCard(PowerBand.B5_6, "Cloud Key", 5,
                   "One mana off every artifact spell, in any color deck."),
        AnchorCard(PowerBand.B7_8, "Mystic Forge", 7,
                   "Cast artifacts and colorless spells off the top of your library, "
                   "with a way to dig past the rest."),
        AnchorCard(PowerBand.B9_10, "Metalworker", 9,
                   "Reveal artifacts from hand, add two mana each -- a three-mana card "
                   "that adds six or more."),
    ],
))


# Theme.ENCHANTRESS -- Graded by how reliably the deck's enchantments turn into
# cards or mana. Even, with a real jump at the top from 'draws cards' to
# 'produces the mana to cast the whole hand'.
register(AnchorLadder(
    tag=Theme.ENCHANTRESS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Sage's Reverie", 2,
                   "Four mana for one burst of cards, and only if you already have a "
                   "board of Auras."),
        AnchorCard(PowerBand.B3_4, "Mesa Enchantress", 4,
                   "A card per enchantment cast, on a three-mana 0/2 the table removes "
                   "on sight."),
        AnchorCard(PowerBand.B5_6, "Eidolon of Blossoms", 6,
                   "Constellation draws on *every* enchantment that enters, including "
                   "ones the deck copies or returns, and it replaces itself "
                   "immediately."),
        AnchorCard(PowerBand.B7_8, "Enchantress's Presence", 8,
                   "The draw trigger on an enchantment instead of a creature, so "
                   "creature removal and board wipes cannot turn it off."),
        AnchorCard(PowerBand.B9_10, "Serra's Sanctum", 9,
                   "A land that taps for one white per enchantment -- the deck's board "
                   "becomes its mana base, and the turns stop having a ceiling."),
    ],
))


# Theme.TRIBAL -- Graded by what naming a creature type is worth: a one-shot
# pump -> a slow anthem -> an anthem with selection -> exponential stats -> a
# card per creature. Even; the 9-10 rung swaps the axis from combat to cards,
# which the note has to justify.
register(AnchorLadder(
    tag=Theme.TRIBAL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Roar of the Crowd", 2,
                   "Damage equal to your tribe count, once, for four mana."),
        AnchorCard(PowerBand.B3_4, "Door of Destinies", 4,
                   "Four mana that does nothing on arrival and grows only as you cast "
                   "more of the type afterward."),
        AnchorCard(PowerBand.B5_6, "Adaptive Automaton", 5,
                   "An anthem that becomes a member of the tribe it is buffing."),
        AnchorCard(PowerBand.B7_8, "Kindred Dominance", 8,
                   "A wrath that your entire board simply ignores."),
        AnchorCard(PowerBand.B9_10, "Cavern of Souls", 9,
                   "Names a type and makes every spell of it uncounterable, off a land "
                   "that also fixes colors."),
    ],
))


# Theme.COUNTERS -- Graded by whether the card gives counters a keyword, adds
# counters, or multiplies them. The 1-2 and 3-4 rungs are 'payoffs for having
# counters' and the top three are 'multipliers', which is the ladder's one
# seam.
register(AnchorLadder(
    tag=Theme.COUNTERS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Scute Mob", 1,
                   "Four counters a turn, on one 1/1, starting several turns from now."),
        AnchorCard(PowerBand.B3_4, "Abzan Falconer", 4,
                   "Gives every creature with a counter flying, which is real evasion "
                   "for a board that is already big."),
        AnchorCard(PowerBand.B5_6, "Juniper Order Ranger", 5,
                   "Every creature that enters gets a counter, and so does this one."),
        AnchorCard(PowerBand.B7_8, "Hardened Scales", 8,
                   "One green mana, and every counter placement gets one extra -- the "
                   "cheapest multiplier in the format."),
        AnchorCard(PowerBand.B9_10, "Cathars' Crusade", 9,
                   "Every creature you cast puts a counter on every creature you "
                   "control, which snowballs past answerable in two turns."),
    ],
))




# Theme.SUPERFRIENDS -- Graded by how much extra loyalty or activation a card
# buys the planeswalkers already in the deck. Even, though the 5-6 rung is a
# one-shot among permanents.
register(AnchorLadder(
    tag=Theme.SUPERFRIENDS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Forge of Heroes", 1,
                   "One loyalty counter, once, and only if your commander is the "
                   "planeswalker -- support that almost never fires."),
        AnchorCard(PowerBand.B3_4, "Oath of Gideon", 4,
                   "Every planeswalker enters one loyalty higher, plus two blockers on "
                   "arrival."),
        AnchorCard(PowerBand.B5_6, "Brokers Ascendancy", 5,
                   "A free loyalty counter on every walker every turn, asking nothing "
                   "of you -- the cost is the three-color identity."),
        AnchorCard(PowerBand.B7_8, "Spark Double", 8,
                   "Copy your best planeswalker, with an extra counter and no legend "
                   "rule -- a staple because it doubles the deck's best card."),
        AnchorCard(PowerBand.B9_10, "Doubling Season", 9,
                   "Planeswalkers enter with twice the loyalty, which means they "
                   "ultimate the turn they land."),
    ],
))


# Theme.STORM -- Graded by what the storm count converts into. Even, and the
# 9-10 rung is the only card where the count feeds itself instead of just
# cashing out.
register(AnchorLadder(
    tag=Theme.STORM,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Ground Rift", 1,
                   "A storm spell whose copies buy nothing at all -- the count "
                   "converted into zero."),
        AnchorCard(PowerBand.B3_4, "Empty the Warrens", 4,
                   "Two Goblins per copy, which at storm five is a real board -- next "
                   "turn."),
        AnchorCard(PowerBand.B5_6, "Grapeshot", 6,
                   "One damage per copy, aimed freely, so it can finish several "
                   "opponents or kill the whole board's creatures."),
        AnchorCard(PowerBand.B7_8, "Flusterstorm", 8,
                   "Count into an uncounterable tax wall -- the storm card that "
                   "protects the turn rather than ending it."),
        AnchorCard(PowerBand.B9_10, "Song of Creation", 9,
                   "Two cards per spell cast -- an engine where each spell literally "
                   "pays for the next two."),
    ],
))


# Theme.WHEEL -- Graded by how much the refill favors you over the three
# opponents it also refills. Even steps, and the top rung breaks the pattern by
# making the symmetry disappear entirely -- which the note has to sell.
register(AnchorLadder(
    tag=Theme.WHEEL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Sway of the Stars", 1,
                   "A perfectly symmetric reset for ten mana -- the refill that helps "
                   "three opponents exactly as much as you."),
        AnchorCard(PowerBand.B3_4, "Reforge the Soul", 4,
                   "A five-mana wheel to seven, or three mana if it happens to be your "
                   "draw for the turn."),
        AnchorCard(PowerBand.B5_6, "Windfall", 6,
                   "Three mana, and everyone draws up to the largest hand on the table "
                   "-- so it is best cast when yours is empty and theirs is not."),
        AnchorCard(PowerBand.B7_8, "Echo of Eons", 7,
                   "A Timetwister you get to cast twice -- the second cast, from the "
                   "graveyard, is the whole point."),
        AnchorCard(PowerBand.B9_10, "Notion Thief", 9,
                   "Opponents draw nothing and you draw instead -- a wheel that used "
                   "to refill four players now draws you twenty-one cards."),
    ],
))


# Theme.BLINK -- Graded by how many enter-the-battlefield triggers one card
# buys and at what speed. Even, with the top rung the only one that is
# unbounded.
register(AnchorLadder(
    tag=Theme.BLINK,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Flicker", 2,
                   "One blink, sorcery speed, two mana, no rider."),
        AnchorCard(PowerBand.B3_4, "Endless Sands", 3,
                   "A blink stapled to a land slot -- slow and mana-hungry, but it "
                   "costs no card."),
        AnchorCard(PowerBand.B5_6, "Conjurer's Closet", 6,
                   "Blinks a creature every one of your end steps -- repeatable, and "
                   "it survives creature removal."),
        AnchorCard(PowerBand.B7_8, "Restoration Angel", 8,
                   "A 3/4 flash flier that blinks something on the way in -- a staple "
                   "because the body alone would be playable."),
        AnchorCard(PowerBand.B9_10, "Ghostly Flicker", 9,
                   "Blinks two permanents including lands -- the card every infinite "
                   "blink loop in the format is built on."),
    ],
))


# Theme.GRAVEYARD_VALUE -- Graded by how much of the graveyard the card gets
# back and how often. Even, and the seam is at 5-6, where the ladder moves from
# 'one card back' to 'the graveyard is a second hand'.
register(AnchorLadder(
    tag=Theme.GRAVEYARD_VALUE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Sanitarium Skeleton", 2,
                   "A 1/1 that buys itself back from the graveyard for three mana."),
        AnchorCard(PowerBand.B3_4, "Tortured Existence", 4,
                   "Unlimited recursion that costs a card every time -- filler, "
                   "because the graveyard never actually grows your hand."),
        AnchorCard(PowerBand.B5_6, "Crucible of Worlds", 6,
                   "Every land in your graveyard is playable again, which turns "
                   "fetches, sacrifice lands and discard into permanent value."),
        AnchorCard(PowerBand.B7_8, "Muldrotha, the Gravetide", 8,
                   "A land, a creature, an artifact and an enchantment from the "
                   "graveyard every single turn -- the yard becomes a second hand."),
        AnchorCard(PowerBand.B9_10, "Yawgmoth's Will", 9,
                   "For one turn every card in your graveyard is castable, which in a "
                   "deck that has been filling it all game is an entire second game's "
                   "worth of spells."),
    ],
))


# Theme.MILL -- Graded against 99-card libraries times three opponents, which
# is why the low rungs are so clearly unplayable. Even, and the top two rungs
# are both multipliers rather than mill itself -- the honest shape of the
# archetype in Commander.
register(AnchorLadder(
    tag=Theme.MILL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Tome Scour", 1,
                   "Five cards off one opponent's ninety-nine, for one mana, once."),
        AnchorCard(PowerBand.B3_4, "Traumatize", 4,
                   "Halves one opponent's library in a single five-mana spell."),
        AnchorCard(PowerBand.B5_6, "Hedron Crab", 5,
                   "Three cards per land drop, forever, off a one-mana body."),
        AnchorCard(PowerBand.B7_8, "Mesmeric Orb", 8,
                   "Every untap of every permanent by every player mills them -- "
                   "across a four-player game that is dozens of cards a turn cycle "
                   "without you spending a card again."),
        AnchorCard(PowerBand.B9_10, "Bruvac the Grandiloquent", 9,
                   "Doubles all mill you cause, for every opponent -- which turns "
                   "'mill half their library' into 'mill their library'."),
    ],
))


# Theme.LIFEGAIN -- The payoff side of the Lifegain pair: cards that TRIGGER on
# gaining life, not cards that gain it (that is Role.LIFEGAIN). Graded by what
# the trigger converts life into -- counters, damage, or a board. Even.
register(AnchorLadder(
    tag=Theme.LIFEGAIN,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Kalastria Nightwatch", 1,
                   "Gaining life grants it flying until end of turn -- the trigger "
                   "converted into nothing at all."),
        AnchorCard(PowerBand.B3_4, "Epicure of Blood", 3,
                   "One life lost by each opponent per trigger -- the right "
                   "conversion, two mana too expensive."),
        AnchorCard(PowerBand.B5_6, "Sanguine Bond", 6,
                   "Every point gained is a point an opponent loses."),
        AnchorCard(PowerBand.B7_8, "Karlov of the Ghost Council", 8,
                   "Two counters per trigger, and the counters buy exile removal -- "
                   "the payoff that also answers the board."),
        AnchorCard(PowerBand.B9_10, "Archangel of Thune", 9,
                   "Every single life gain puts a +1/+1 counter on your whole board, "
                   "permanently -- with a lifelink creature this is exponential in one "
                   "combat."),
    ],
))


# Theme.EQUIPMENT -- The payoff side of the Equipment pair: cards that CARE
# about Equipment, not Equipment cards themselves (those are read off the
# type line, not tagged as a role -- see schema.py's module docstring).
# Graded by how much equip cost and card economy the payoff removes. Even.
register(AnchorLadder(
    tag=Theme.EQUIPMENT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Myr Adapter", 1,
                   "A body that grows with Equipment attached to it -- a payoff that "
                   "gives the deck nothing it did not already have."),
        AnchorCard(PowerBand.B3_4, "The Lonely Mountain", 3,
                   "An Equipment payoff that costs a land slot -- an untapped land "
                   "plus a token engine that gets cheaper with every piece."),
        AnchorCard(PowerBand.B5_6, "Bruenor Battlehammer", 6,
                   "The first equip each turn is free and equipped creatures get +2/+0 "
                   "-- it removes the tax on the deck's main action."),
        AnchorCard(PowerBand.B7_8, "Puresteel Paladin", 8,
                   "Draws a card per Equipment that enters, and with three artifacts "
                   "every equip cost becomes zero."),
        AnchorCard(PowerBand.B9_10, "Sigarda's Aid", 9,
                   "Equipment enters at instant speed and attaches itself free."),
    ],
))


# Theme.TREASURE -- Graded by how many Treasures the card produces per turn and
# whether it needs anything to do it. Even, and the top rung is the only one
# that scales with the whole table rather than with your own actions.
register(AnchorLadder(
    tag=Theme.TREASURE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Prized Statue", 1,
                   "Two Treasures, two mana, and a death trigger in between -- the "
                   "floor of Treasure production."),
        AnchorCard(PowerBand.B3_4, "Pirate's Prize", 3,
                   "Draw two and make one Treasure -- a fine effect at a mana cost "
                   "that makes it filler."),
        AnchorCard(PowerBand.B5_6, "Brass's Bounty", 5,
                   "A Treasure for every permanent you control, in one seven-mana "
                   "burst."),
        AnchorCard(PowerBand.B7_8, "Bootleggers' Stash", 7,
                   "Every land you control taps for a Treasure -- a doubling of your "
                   "entire mana base."),
        AnchorCard(PowerBand.B9_10, "Smothering Tithe", 9,
                   "A Treasure for every card every opponent draws unless they each "
                   "pay two -- in a pod that is three to ten Treasures per turn cycle "
                   "from one card."),
    ],
))


# Theme.DISCARD -- The self-discard side: discarding your own cards as a
# resource, as distinct from Role.HAND_DISRUPTION's attacks on opponents'
# hands. Graded by whether discarding costs you a card, breaks even, or
# profits. Even.
register(AnchorLadder(
    tag=Theme.DISCARD,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Dwarven Armorer", 1,
                   "Pay mana, tap, and throw away a card for a single counter."),
        AnchorCard(PowerBand.B3_4, "Prophetic Ravings", 3,
                   "Haste, and a repeatable loot on whatever it enchants."),
        AnchorCard(PowerBand.B5_6, "Faithless Looting", 6,
                   "One mana to discard two and draw two, and then it does it again "
                   "from the graveyard."),
        AnchorCard(PowerBand.B7_8, "Bone Miser", 8,
                   "Every discarded card becomes a card, a 2/2 Zombie, or two black "
                   "mana depending on its type -- the discard stops being a cost."),
        AnchorCard(PowerBand.B9_10, "Archfiend of Ifnir", 9,
                   "Every discard and cycle puts a -1/-1 counter on all creatures "
                   "opponents control."),
    ],
))


# Theme.LANDS_MATTER -- How much a land drop is worth, and how many land drops
# the card gives you: a temporary pump on one creature per land (1-2) -> a 2/2
# token per land, for five mana (3-4) -> a 4/4 per land, or an extra land drop
# every turn (5-6) -> mana off every land drop, or two extra drops a turn (7-8)
# -> a board or a graveyard's worth of lands converted into a win (9-10). The
# landfall trigger itself is now SynergyTag.LANDFALL; this ladder grades the
# payoff and the enablers together, since a Lands Matter deck needs both.
register(AnchorLadder(
    tag=Theme.LANDS_MATTER,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Steppe Lynx", 1,
                   "A 0/1 for one mana that gets +2/+2 until end of turn each time a "
                   "land you control enters."),
        AnchorCard(PowerBand.B3_4, "Emeria Angel", 4,
                   "Four mana for a 3/3 flier that makes a 1/1 flier per land drop."),
        AnchorCard(PowerBand.B5_6, "Exploration", 6,
                   "One mana: you may play an additional land every turn, for the rest "
                   "of the game."),
        AnchorCard(PowerBand.B7_8, "Lotus Cobra", 8,
                   "Two mana for a 2/1 that adds one mana of any color every time a "
                   "land you control enters."),
        AnchorCard(PowerBand.B9_10, "Scute Swarm", 9,
                   "Three mana: every land drop makes an Insect, and once you control "
                   "six lands every land drop copies the Swarm itself instead."),
    ],
))


# Theme.PILLOW_FORT -- How much of an attack it actually stops, and for how
# long: one damage source prevented, or one turn bought (1-2) -> a cap or a tax
# with an upkeep or a narrow condition (3-4) -> two generic mana per attacker,
# permanently (5-6) -> a tax nobody can pay, or a hard cap of one attacker
# (7-8) -> attacking you stops being possible at all (9-10). Graded in a four-
# player pod, where a tax is paid three times over and a deterrent redirects
# three attackers rather than one. Weakest joint is 3-4 to 5-6.
register(AnchorLadder(
    tag=Theme.PILLOW_FORT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Forbidding Spirit", 2,
                   "A three-mana body that taxes attacks at {2} each -- until your "
                   "next turn, and then never again."),
        AnchorCard(PowerBand.B3_4, "Crawlspace", 4,
                   "No more than two creatures can attack you each combat -- "
                   "colorless, and permanent."),
        AnchorCard(PowerBand.B5_6, "Windborn Muse", 5,
                   "A four-mana 2/3 flier that taxes every attack against you {2} per "
                   "attacker."),
        AnchorCard(PowerBand.B7_8, "Norn's Annex", 7,
                   "Attackers must pay white mana or two life per creature -- and you "
                   "can cast it for life instead of white mana."),
        AnchorCard(PowerBand.B9_10, "Moat", 9,
                   "Four mana: creatures without flying can't attack. No payment, no "
                   "exception."),
    ],
))


# Theme.AURAS -- The Aura-specific build -- bogles -- graded by how much one
# Aura advances it: a small static pump with no protection and a two-for-one
# risk (1-2) -> +2/+2 or cheap evasion (3-4) -> a pump that survives removal or
# makes the creature unblockable (5-6) -> an Aura that scales with the other
# enchantments, or protects the creature wearing it (7-8) -> an Aura that makes
# any creature lethal on the spot (9-10). The Aura-ness is the point: every
# rung is card-disadvantage if the creature dies, which is the risk the
# archetype is built to cover. Theme.VOLTRON asks the same question of any
# stacking method and is weighted to Equipment, Auras included -- the overlap
# is deliberate, and a card can carry both tags. What this ladder adds is the
# comparison that matters to the build: an Aura against other Auras, rather
# than against a Sword that survives its creature.
register(AnchorLadder(
    tag=Theme.AURAS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Holy Strength", 1,
                   "One mana for +1/+2 on one creature, and a two-for-one when that "
                   "creature is removed."),
        AnchorCard(PowerBand.B3_4, "Arcane Flight", 3,
                   "One mana for +1/+1 and flying."),
        AnchorCard(PowerBand.B5_6, "Rancor", 6,
                   "One mana for +2/+0 and trample, and it returns to your hand "
                   "whenever it leaves the battlefield."),
        AnchorCard(PowerBand.B7_8, "Ethereal Armor", 7,
                   "One mana: +1/+1 for every enchantment you control, plus first "
                   "strike."),
        AnchorCard(PowerBand.B9_10, "All That Glitters", 9,
                   "Two mana: +1/+1 for each artifact and enchantment you control."),
    ],
))


# Theme.MINUS_COUNTERS -- How many -1/-1 counters the card puts out, and
# whether anything is paid for them: one counter on one creature, once (1-2) ->
# a delayed kill that reads a counter already there (3-4) -> a payoff that
# turns each counter into a token (5-6) -> a scalable board wipe made of
# counters (7-8) -> an engine that converts counters into cards and kills
# anything (9-10). Both halves of the archetype are on one ladder on purpose:
# the counters and the payoffs that read them are useless apart, so two rungs
# grade sources, two grade payoffs, and the top rung is both. Two mass counter
# sources (Midnight Banshee, Carnifex Demon) held 5-6 and 9-10 and were
# replaced: they graded output a third time over, leaving no payoff rung.
register(AnchorLadder(
    tag=Theme.MINUS_COUNTERS,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Virulent Wound", 2,
                   "One mana, instant: one -1/-1 counter, plus a poison counter if the "
                   "creature dies this turn."),
        AnchorCard(PowerBand.B3_4, "Crumbling Ashes", 3,
                   "Two mana: each upkeep, destroy a creature that already has a -1/-1 "
                   "counter on it."),
        AnchorCard(PowerBand.B5_6, "Nest of Scarabs", 6,
                   "Three mana: every -1/-1 counter you hand out also makes a 1/1 "
                   "Insect, so the removal and the board are one card."),
        AnchorCard(PowerBand.B7_8, "Black Sun's Zenith", 8,
                   "X plus two mana: X -1/-1 counters on every creature on the "
                   "battlefield."),
        AnchorCard(PowerBand.B9_10, "The Scorpion God", 9,
                   "Five mana: three mana a go puts a counter on any other creature, "
                   "every creature that dies holding one draws you a card, and it "
                   "returns to your hand when it dies -- the archetype in one card."),
    ],
))


# Theme.SELF_MILL -- How many of your own cards reach the graveyard per card
# spent, and whether you get to use them: one card at a time, awkwardly (1-2)
# -> three cards and a small refund (3-4) -> four or five cards with one of
# them kept (5-6) -> a repeatable engine that mills every time you draw (7-8)
# -> your whole library in one activation (9-10). Opponent-directed mill is
# Theme.MILL and is deliberately absent: every rung here empties your own
# library on purpose. Weakest joint is 7-8 to 9-10, where the axis jumps from
# rate to combo.
register(AnchorLadder(
    tag=Theme.SELF_MILL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Millikin", 2,
                   "Tap and mill one card to add one colorless -- self-mill at the "
                   "rate of a bad mana rock."),
        AnchorCard(PowerBand.B3_4, "Mental Note", 4,
                   "One mana: mill two and draw a card, so the self-mill costs you "
                   "nothing but the slot."),
        AnchorCard(PowerBand.B5_6, "Stitcher's Supplier", 6,
                   "A one-mana body that mills three when it enters and three more "
                   "when it dies."),
        AnchorCard(PowerBand.B7_8, "Stinkweed Imp", 7,
                   "Dredge 5: every draw can instead mill five and return the Imp to "
                   "your hand, over and over."),
        AnchorCard(PowerBand.B9_10, "Undercity Informer", 9,
                   "Three mana, then one mana and a creature: bury every card above "
                   "the first land in a library, repeatably."),
    ],
))


# Theme.VEHICLES -- How much easier the card makes the deck's actual problem,
# which is crewing: a one-mana trick whose whole contribution is a single crew
# token (1-2) -> a land that animates a Vehicle without crewing it at all (3-4)
# -> a body that crews big AND pumps the Vehicles (5-6) -> a permanent that
# makes every Vehicle cheaper to crew, or makes crew bodies every turn (7-8) ->
# a card the archetype is built around: it returns Vehicles from the graveyard,
# or draws two a turn while making its own crew (9-10). Payoffs and enablers
# only, mirroring the shipped Theme.EQUIPMENT ladder -- Vehicle as a card type
# is read off the type line, not tagged. Every rung has to touch crewing; a
# card that merely counts Vehicles (Road Rage) grades a different property.
register(AnchorLadder(
    tag=Theme.VEHICLES,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Defend the Rider", 1,
                   "One mana, instant: a one-turn protection trick, or a lone 1/1 Pilot "
                   "token that crews as though its power were 3 -- one crew body, for a "
                   "whole card."),
        AnchorCard(PowerBand.B3_4, "Mech Hangar", 4,
                   "A land that fixes for Pilot and Vehicle spells and, for three mana, "
                   "turns a Vehicle into a creature without crewing it."),
        AnchorCard(PowerBand.B5_6, "Cloudspire Captain", 5,
                   "Three mana: your Mounts and Vehicles get +1/+1, and it crews as "
                   "though its power were two greater."),
        AnchorCard(PowerBand.B7_8, "Kotori, Pilot Prodigy", 7,
                   "Three mana: every Vehicle you control has crew 2, so almost any "
                   "creature can crew anything."),
        AnchorCard(PowerBand.B9_10, "Greasefang, Okiba Boss", 9,
                   "Three mana: at the beginning of combat, return a Vehicle from your "
                   "graveyard to the battlefield with haste."),
    ],
))


# Theme.FORCED_COMBAT -- How many of the table's creatures it points away from
# you, and for how long: one creature goaded once, off an attack or an arrival
# (1-2) -> one creature permanently goaded, or a one-shot steal-and-goad (3-4)
# -> every creature forced to attack, once, or a lure on one body (5-6) -> a
# permanent that makes all opponents attack each other every turn (7-8) -> a
# card that goads a whole board every combat and stops them answering it
# (9-10). The point is always the same: in a four-player pod, two opponents
# fighting each other is better than any removal spell.
register(AnchorLadder(
    tag=Theme.FORCED_COMBAT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Taunting Kobold", 2,
                   "A one-mana hasty 0/1 that goads a creature each time it attacks."),
        AnchorCard(PowerBand.B3_4, "Bloodthirsty Blade", 4,
                   "Two mana, and it can be attached to a creature an opponent "
                   "controls: that creature gets +2/+0 and is goaded, permanently."),
        AnchorCard(PowerBand.B5_6, "Grand Melee", 6,
                   "Four mana: every creature attacks each combat if able, and every "
                   "creature blocks each combat if able."),
        AnchorCard(PowerBand.B7_8, "Disrupt Decorum", 7,
                   "Four mana: goad every creature you do not control."),
        AnchorCard(PowerBand.B9_10, "Marisi, Breaker of the Coil", 9,
                   "Four mana: your opponents cannot cast spells during combat, and "
                   "every creature of yours that connects goads that player's entire "
                   "board."),
    ],
))


# Theme.INFECT -- How many of the ten poison counters a card can realistically
# deliver: a ground body with no evasion that trades with any blocker (1-2) ->
# a small evasive or value infect body (3-4) -> one mana of infect granted to
# something that was already attacking (5-6) -> an unblockable or near-
# unblockable infect creature (7-8) -> a card that converts an existing board
# into ten poison in one attack (9-10). Poison ignores the format's 40 life, so
# this ladder is graded on delivery rather than damage -- an unblockable 1/1 is
# worth more than a 3/3 that cannot get through.
register(AnchorLadder(
    tag=Theme.INFECT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Vector Asp", 1,
                   "A one-mana 1/1 that only has infect on turns you pay black mana "
                   "for it."),
        AnchorCard(PowerBand.B3_4, "Plague Stinger", 4,
                   "A 1/1 flier with infect for two mana -- evasion, but only one "
                   "poison a turn."),
        AnchorCard(PowerBand.B5_6, "Glistener Elf", 6,
                   "A one-mana 1/1 with infect -- the creature the whole archetype "
                   "starts with, because pump spells do the rest."),
        AnchorCard(PowerBand.B7_8, "Tainted Strike", 7,
                   "One mana at instant speed: any creature gets +1/+0 and infect for "
                   "the turn."),
        AnchorCard(PowerBand.B9_10, "Triumph of the Hordes", 9,
                   "Four mana: your creatures get +1/+1, trample and infect -- so any "
                   "board of three or four creatures is lethal poison."),
    ],
))


# Theme.FOOD -- Food per card, and whether anything cares that it is Food: one
# token, once, for a whole card (1-2) -> one token plus a body or a repeatable
# outlet (3-4) -> a repeatable Food engine for one or two mana (5-6) -> a card
# that turns Food into a recurring drain, or makes Food off the deck's other
# plan (7-8) -> an engine that multiplies every token the deck makes (9-10).
# Food is gain-three-life until something converts it, so the axis is really
# the conversion: three life is worth nothing at 40 against three opponents.
register(AnchorLadder(
    tag=Theme.FOOD,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Crumb and Get It", 1,
                   "One mana: a combat trick that gives an opponent a Food token to "
                   "make itself better."),
        AnchorCard(PowerBand.B3_4, "The Underworld Cookbook", 4,
                   "One mana: tap and discard a card to make a Food -- and {4} plus "
                   "sacrificing the Cookbook itself buys a creature out of your "
                   "graveyard."),
        AnchorCard(PowerBand.B5_6, "Trail of Crumbs", 6,
                   "Two mana: a Food on arrival, and sacrificing a Food digs two cards "
                   "deep for a permanent."),
        AnchorCard(PowerBand.B7_8, "Cauldron Familiar", 7,
                   "A one-mana Cat that drains each opponent for one when it enters, "
                   "and comes back from the graveyard for a Food."),
        AnchorCard(PowerBand.B9_10, "Academy Manufactor", 9,
                   "Three colorless mana: every Clue, Food or Treasure you would "
                   "create becomes one of each instead."),
    ],
))


# Theme.CLUES -- Clues per card, and how cheaply they turn back into cards: one
# Clue for three mana and a tap (1-2) -> one Clue plus a body or a small payoff
# (3-4) -> a Clue every turn, or a Clue plus a real body (5-6) -> a Clue off
# something the deck already does every turn, or every investigate doubled
# (7-8) -> a Clue off every land drop, or every token tripled (9-10). A Clue is
# two mana for a card, so the whole axis is how many you get for free and how
# much the deck does with them besides cashing them in.
register(AnchorLadder(
    tag=Theme.CLUES,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Confront the Unknown", 2,
                   "One mana: investigate, and pump a creature by the number of Clues "
                   "you control."),
        AnchorCard(PowerBand.B3_4, "Thraben Inspector", 4,
                   "A one-mana 1/2 that investigates when it enters."),
        AnchorCard(PowerBand.B5_6, "Tamiyo's Journal", 5,
                   "Five mana: investigate at every upkeep, and three Clues can be "
                   "cashed for any card in your library."),
        AnchorCard(PowerBand.B7_8, "Lonis, Cryptozoologist", 7,
                   "Two mana: investigate whenever another nontoken creature of yours "
                   "enters, and sacrifice X Clues to steal a permanent off an "
                   "opponent's library."),
        AnchorCard(PowerBand.B9_10, "Tireless Tracker", 9,
                   "Three mana: investigate on every land you play, and every Clue you "
                   "cash in makes it permanently bigger."),
    ],
))


# Theme.ENERGY -- Energy in against what the energy buys: two or three counters
# and a trivial sink (1-2) -> energy plus a cantrip or a land (3-4) -> a
# repeatable sink that converts energy into stats or damage (5-6) -> a card
# that makes energy off something the deck does anyway, or buys protection with
# it (7-8) -> six energy for a free permanent off the top of the deck, or a
# copy of your attacker every turn (9-10). Energy is a closed economy: nothing
# outside these cards produces it, so each rung is graded on whether it is a
# producer, a sink, or both.
register(AnchorLadder(
    tag=Theme.ENERGY,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Greenbelt Rampager", 1,
                   "A one-mana 3/4 that returns to your hand unless you pay two energy "
                   "-- and each bounce hands you one energy back."),
        AnchorCard(PowerBand.B3_4, "Attune with Aether", 4,
                   "One mana: a basic land to your hand and two energy."),
        AnchorCard(PowerBand.B5_6, "Longtusk Cub", 6,
                   "Two mana: two energy for every combat connection, and two energy "
                   "buys a permanent +1/+1 counter."),
        AnchorCard(PowerBand.B7_8, "Bristling Hydra", 7,
                   "Four mana: three energy on arrival, and three energy puts a +1/+1 "
                   "counter on it and makes it hexproof until end of turn."),
        AnchorCard(PowerBand.B9_10, "Aetherworks Marvel", 9,
                   "Four colorless: an energy whenever a permanent of yours hits a "
                   "graveyard, and six energy casts a card off the top six of your "
                   "library for free."),
    ],
))


# ---------------------------------------------------------------------------
# Game stage anchors.
#
# One set for the whole corpus, not one per tag -- and that difference is
# structural rather than a saving. `power_rating` needs a ladder per `Role` and
# `Theme` because "good" is only meaningful inside a category: a 7 for
# `Mana Fixing` and a 7 for `Removal` are claims about different axes, and the
# per-tag ladder is what makes them commensurable. `game_stage` asks where on
# the game's clock a card does its work, and that clock is the same clock for
# every card in the format. A per-tag ladder would teach the opposite -- that
# turn three means something different depending on the archetype.
#
# The consequence that matters more than the token count: because the set is
# card-independent, it rides in pass one's *fixed* system block. There is no
# `ladders_for` lookup, no cap, and no selection step, so unlike `power_rating`
# this calibration cannot be mis-selected by a bad tag
# (`docs/lessons-learned.md`, the third failure).
#
# **Profiles, not five-rung ladders per axis.** The measured failure is the
# shape of the curve and a disconnect from castability, not ignorance of what
# a "2" means: on S-001 the 14B's `early` score *rose* with mana value
# (r = +0.41), rating a seven-drop above a one-drop on turn two. One row
# carrying all three numbers and the reason the curve bends that way addresses
# both, and costs less than fifteen separate band rungs would.
#
# Three rules this set obeys:
#
#   Each axis spans the scale. `tests/test_anchors.py` checks it, because a set
#   whose `mid` values all sit between 4 and 7 silently teaches the clustering
#   it exists to break.
#
#   The curve shapes are balanced. Five fall, six rise, two are flat and one
#   peaks. An earlier draft was nine rising out of thirteen, which would have
#   taught the exact monotonic-ramp bias these anchors are here to correct --
#   the honest value for most *cost-exception* cards happens to be back-loaded,
#   so balancing took deliberately adding front-loaded plain cards.
#
#   Six of the fourteen are cards whose mana value misstates their cost to
#   function, one per direction in `knowledge_pipeline/cost_profile.py`. They
#   teach that rule by example; the rubric states it once in prose.
#
# Reusing a card that is already a `power_rating` rung is allowed here, and
# four of these are. The no-reuse rule on the ladders exists so one card cannot
# carry two different *power ratings*; timing and power are different fields
# measured on different scales, so Sol Ring at `power_rating=8` and Sol Ring at
# 10/8/6 state nothing contradictory.


@dataclass(frozen=True)
class GameStageAnchor:
    """One reference profile: a card, its early/mid/late scores, and why."""

    name: str
    early: float
    mid: float
    late: float
    note: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("A game stage anchor needs a card name.")
        if not self.note.strip():
            raise ValueError(
                f"{self.name} needs a note: the profile has to say *why* the "
                "curve has that shape, or the model can only copy the numbers."
            )
        for axis, value in (
            ("early", self.early),
            ("mid", self.mid),
            ("late", self.late),
        ):
            if not 1 <= value <= 10:
                raise ValueError(
                    f"{self.name}'s {axis} score {value} is outside the 1-10 "
                    "scale this anchor is meant to calibrate."
                )

    @property
    def profile(self) -> tuple[float, float, float]:
        return (self.early, self.mid, self.late)

    def render(self) -> str:
        return (
            f"  {self.name:<22} {self.early:>4.0f} /{self.mid:>3.0f} /"
            f"{self.late:>3.0f}   {self.note}"
        )


GAME_STAGE_ANCHORS: tuple[GameStageAnchor, ...] = (
    # Front-loaded: the half the model under-produces.
    GameStageAnchor("Sol Ring", 10, 8, 6,
                    "Two mana on turn one, and still a mana rock at parity "
                    "later -- accelerates hardest early but never dead."),
    GameStageAnchor("Llanowar Elves", 9, 4, 2,
                    "Turn-one mana; by turn nine it is a 1/1 that does nothing "
                    "the rest of the board does not already do."),
    GameStageAnchor("Lightning Bolt", 8, 5, 3,
                    "One mana kills most early threats; three damage is close "
                    "to irrelevant against 40 life and late-game creatures."),
    GameStageAnchor("Cultivate", 7, 5, 2,
                    "Ramp and fixing exactly when both are scarce; a dead draw "
                    "once the lands are already on the battlefield."),
    GameStageAnchor("Duress", 6, 3, 2,
                    "Strips a key spell before it is cast; useless once the "
                    "hands it was meant to attack are already empty."),
    # Flat: a card that is simply always worth the same.
    GameStageAnchor("Force of Will", 7, 7, 7,
                    "Mana value 5, but the alternative cost makes it castable "
                    "on turn one -- equally live whenever the threat appears."),
    GameStageAnchor("Command Tower", 5, 5, 5,
                    "A land: no mana cost, played on turn one, and exactly as "
                    "useful on turn twenty as it was then."),
    # Peaked: strongest once the game is going but before it ends.
    GameStageAnchor("Rhystic Study", 6, 9, 8,
                    "Needs a few turns of opponents' spells to pay off, then "
                    "dominates the middle of the game."),
    # Back-loaded, and the cost-exception cases.
    GameStageAnchor("Swords to Plowshares", 7, 8, 8,
                    "One mana answers almost anything at any point; marginally "
                    "better later only because the threats are bigger."),
    GameStageAnchor("Shark Typhoon", 5, 7, 8,
                    "Cycling gives it a cheap early mode that is not casting "
                    "it; the full six-mana enchantment is a late-game engine."),
    GameStageAnchor("Treasure Cruise", 2, 6, 8,
                    "Mana value 8 and nearly uncastable early, but Delve makes "
                    "it a one-mana draw-three once the graveyard is full."),
    GameStageAnchor("Colossus Hammer", 1, 3, 6,
                    "Mana value 1 and Equip {8}: castable on turn one and "
                    "does nothing at all until very late."),
    GameStageAnchor("Craterhoof Behemoth", 1, 4, 9,
                    "Eight mana does nothing early; with a board it ends the "
                    "game the turn it lands."),
    GameStageAnchor("Torment of Hailfire", 1, 5, 10,
                    "Its {X} makes a mana value of 2 a lie -- it does nothing "
                    "early and wins the game once mana is abundant."),
)


def game_stage_anchor_names() -> frozenset[str]:
    """Every card used as a game stage profile, by name.

    Same contract and same purpose as `sample.anchor_card_names`: these cards
    are printed into the system prompt of every call, so the benchmark must
    not draw them as test cards.
    """
    return frozenset(anchor.name for anchor in GAME_STAGE_ANCHORS)


def render_game_stage_anchors() -> str:
    """The `game_stage` calibration block of pass one's system prompt."""
    if not GAME_STAGE_ANCHORS:
        return ""

    profiles = "\n".join(anchor.render() for anchor in GAME_STAGE_ANCHORS)
    return (
        "Game stage calibration — fixed reference points for `game_stage`, "
        "shown as early / mid / late on the same 1-10 scale you are scoring "
        "on, with the reason each curve has the shape it does.\n\n"
        "These are not a ladder to pick one rung from: read the card you are "
        "scoring against the whole set and place its curve among them. Note "
        "that the shapes genuinely differ — some cards fall off, some stay "
        "flat all game, and some do nothing until late. Do not default to "
        "scores that rise from early to late.\n\n"
        f"{profiles}"
    )
