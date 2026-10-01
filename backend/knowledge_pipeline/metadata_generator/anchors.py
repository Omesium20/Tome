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
`Theme` member.** The 255 rungs were chosen in three stages. A corpus search
proposed candidates rung by rung -- four per rung, after a second pass widened
a first pass that had offered two -- each one resolved against the `cards`
table, so every name, oracle text and legality here is the corpus's own rather
than a recollection. Those candidates were then ranked on the properties that
make a *ruler mark* reliable, which are not the properties that make a card
good: whether the tag is visible in the card's own oracle text, whether the
rating sits unambiguously inside its band, how fast the text reads, and how
much the rung differs from its neighbours. A human then made the final call on
every rung -- the step this whole mechanism rests on, and not a formality: the
ranking left the top two candidates exactly tied in 101 of the 260 rungs it
ranked, so most rungs were chosen rather than computed.

That whole process is a tool rather than a one-off:
`knowledge_pipeline/anchor_bench` validates a candidate pool against the corpus,
ranks it, builds the review page, and renders the reviewed result back into the
calls below. Revising a rung — or recalibrating the lot after a rubric change —
starts there, not from scratch. The pool behind these ladders was not kept: it
would be stale against a later corpus, and the tool exists so a fresh one is
cheap.

`Theme.FLYING` has no ladder because the member no longer exists -- see
`schema.py`, which records why it was dropped rather than anchored.

Add or replace a ladder with `register`, one complete ladder per enum value.
Do not assign into `ANCHORS` directly -- see `anchor_key` for why the registry
is keyed by a qualified string rather than by the enum member:

    register(AnchorLadder(
        tag=Role.RAMP,
        rungs=[
            AnchorCard(PowerBand.B1_2, "Wayfarer's Bauble", 2,
                       "Ramp that costs two cards' worth of tempo to fetch one land."),
            ...
            AnchorCard(PowerBand.B9_10, "Mana Vault", 9, "..."),
        ],
    ))
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .schema import Role, Theme


class PowerBand(StrEnum):
    """One rung of the ladder: a two-point slice of the 1-10 scale.

    Bands are two points wide rather than one so a ladder stays five cards
    instead of ten. Five is enough to interpolate against and short enough to
    keep in a cached system block across 51 tags; ten would double the block
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


def render_anchors() -> str:
    """Render populated ladders as the calibration block of the system prompt.

    Returns an empty string when `ANCHORS` is empty — the caller decides
    whether that's acceptable (fine for a smoke test; not for a full-corpus
    run, per the module docstring above).
    """
    if not ANCHORS:
        return ""

    ladders = "\n\n".join(ladder.render() for ladder in ANCHORS.values())
    return (
        "Calibration ladders — fixed reference points. For each role or theme "
        "below, five cards mark what each band of the 1-10 power scale looks "
        "like for that tag, lowest to highest.\n\n"
        "To score `power_rating`: find a ladder for a role or theme the card "
        "you are rating shares, read the card against those five rungs, and "
        "place it where it falls between them. Compare against the rungs — do "
        "not score against an abstract sense of the scale. Where a card shares "
        "several tags with ladders, use the one its strongest effect belongs "
        "to.\n\n" + ladders
    )


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

# Role.RAMP -- Even: dead weight -> one land on curve -> two lands with fixing
# -> free acceleration -> explosive acceleration. Weakest joint is 5-6 to 7-8,
# where the axis changes from land count to raw speed.
register(AnchorLadder(
    tag=Role.RAMP,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Untamed Wilds", 2,
                   "Three mana for one untapped basic -- a full turn behind the "
                   "two-mana version."),
        AnchorCard(PowerBand.B3_4, "Burnished Hart", 3,
                   "Six mana total across two turns for two tapped lands, playable in "
                   "any deck."),
        AnchorCard(PowerBand.B5_6, "Sakura-Tribe Elder", 6,
                   "Two mana, one tapped land, and a body that eats an attack first."),
        AnchorCard(PowerBand.B7_8, "Ancient Tomb", 8,
                   "A land that taps for two -- acceleration that costs no card and "
                   "two life per use."),
        AnchorCard(PowerBand.B9_10, "Gaea's Cradle", 9,
                   "A land that taps for as much green as you have creatures -- ramp "
                   "that scales with the board."),
    ],
))


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
        AnchorCard(PowerBand.B9_10, "Chrome Mox", 9,
                   "Free fixing and free acceleration, paid for by exiling a card from "
                   "your hand."),
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


# Role.BOARD_WIPE -- Even. The jump from 7-8 to 9-10 is the largest and is
# deliberate: symmetrical wipes reset a game, one-sided ones end it.
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
        AnchorCard(PowerBand.B9_10, "Cyclonic Rift", 10,
                   "For seven mana at instant speed, every nonland permanent your "
                   "opponents control goes back to hand -- and yours stay."),
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


# Role.RECURSION -- Even. Axis: one creature to hand -> body attached -> any
# card to hand -> permanents back onto the battlefield once per combat -> the
# whole graveyard castable.
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
        AnchorCard(PowerBand.B9_10, "Command the Dreadhorde", 9,
                   "Every creature and planeswalker in every graveyard, onto your "
                   "side, paid for in life."),
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


# Role.COMBO_PIECE -- Weakest ladder in the registry by nature: a combo piece
# is only as strong as the combo it belongs to, so the 1-2 and 3-4 rungs are
# 'enables nothing worth assembling' rather than a weak version of the same
# effect.
register(AnchorLadder(
    tag=Role.COMBO_PIECE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Twiddle", 2,
                   "One mana: tap or untap one artifact, creature, or land."),
        AnchorCard(PowerBand.B3_4, "Illusionist's Bracers", 4,
                   "Copies the equipped creature's activated abilities, for two to "
                   "cast and three to equip."),
        AnchorCard(PowerBand.B5_6, "Dramatic Reversal", 6,
                   "Two mana at instant speed: untap every nonland permanent you "
                   "control."),
        AnchorCard(PowerBand.B7_8, "Rings of Brighthearth", 7,
                   "Pay two to copy any activated ability you use, including a land's."),
        AnchorCard(PowerBand.B9_10, "Thassa's Oracle", 9,
                   "When it enters, an empty library wins the game outright."),
    ],
))


# Role.WIN_CONDITION -- Even. Axis: an alternate win nobody can assemble -> one
# that needs an unrelated deck -> one that needs only time -> one that kills a
# table from a board -> one that kills a table from a board you already have.
register(AnchorLadder(
    tag=Role.WIN_CONDITION,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Barren Glory", 1,
                   "You win at upkeep if you control no permanents besides this and "
                   "have no cards in hand."),
        AnchorCard(PowerBand.B3_4, "Test of Endurance", 3,
                   "Win at upkeep at 50 or more life."),
        AnchorCard(PowerBand.B5_6, "Laboratory Maniac", 5,
                   "You win instead of losing when you would draw from an empty "
                   "library."),
        AnchorCard(PowerBand.B7_8, "Kokusho, the Evening Star", 8,
                   "Fifteen life swing when it dies, and it wants to die."),
        AnchorCard(PowerBand.B9_10, "Craterhoof Behemoth", 9,
                   "Every creature you control gains trample and +X/+X for the number "
                   "of creatures you have, with haste."),
    ],
))


# Role.STAX_PIECE -- Even. Axis: taxes one player -> delays everyone slightly
# -> taxes attacks or spell count -> squeezes mana every turn -> rewrites what
# a deck's lands and untap step do.
register(AnchorLadder(
    tag=Role.STAX_PIECE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Squeeze", 1,
                   "Four mana: sorcery spells cost three more."),
        AnchorCard(PowerBand.B3_4, "Root Maze", 3,
                   "Artifacts and lands enter tapped -- yours included."),
        AnchorCard(PowerBand.B5_6, "Ghostly Prison", 6,
                   "Creatures can't attack you unless their controller pays {2} per "
                   "attacker."),
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
# how combat works -> a permanent, compounding pump on every creature that
# enters.
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
        AnchorCard(PowerBand.B9_10, "Overwhelming Stampede", 9,
                   "Your whole team gets your biggest creature's power, plus trample."),
    ],
))


# Role.EQUIPMENT -- Even. Axis: raw stats at a bad rate -> raw stats at a good
# rate -> keywords instead of stats -> a keyword package that ends games -> an
# Equipment that draws cards instead of fighting.
register(AnchorLadder(
    tag=Role.EQUIPMENT,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Bone Saw", 1,
                   "Free to cast, {1} to equip, and it gives +1/+0."),
        AnchorCard(PowerBand.B3_4, "Vulshok Morningstar", 3,
                   "Two mana, equip {2}, +2/+2."),
        AnchorCard(PowerBand.B5_6, "Loxodon Warhammer", 5,
                   "+3/+0, trample, and lifelink for three, equip {3}."),
        AnchorCard(PowerBand.B7_8, "Hammer of Nazahn", 7,
                   "Indestructible creatures, and every Equipment you cast attaches "
                   "itself free."),
        AnchorCard(PowerBand.B9_10, "Skullclamp", 9,
                   "One mana, equip {1}: the equipped creature gets +1/-1, and when it "
                   "dies you draw two cards."),
    ],
))


# Role.AURA -- Even on one axis -- how much an Aura adds measured against the
# two-for-one it risks -- from stats that hand an opponent a free card, to
# stats worth the risk, to an Aura that survives removal, to one that doubles a
# turn's mana, to one that wins on contact. The 7-8 alternate steps off that
# axis deliberately: Song of the Dryads is an Aura used as removal, which is
# the other job the card type does in Commander.
register(AnchorLadder(
    tag=Role.AURA,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Holy Strength", 1,
                   "One mana, +1/+2."),
        AnchorCard(PowerBand.B3_4, "Unflinching Courage", 4,
                   "+2/+2, trample, and lifelink for three mana."),
        AnchorCard(PowerBand.B5_6, "Rancor", 6,
                   "+2/+0 and trample for one mana, and it returns to your hand when "
                   "it leaves the battlefield."),
        AnchorCard(PowerBand.B7_8, "Song of the Dryads", 8,
                   "Three mana turns any permanent -- a commander, a planeswalker, an "
                   "artifact -- into a colorless Forest that its controller keeps."),
        AnchorCard(PowerBand.B9_10, "Angelic Destiny", 9,
                   "+4/+4, flying and first strike, and it returns to your hand when "
                   "the creature dies."),
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
# plus protection plus value -> lethal on its own.
register(AnchorLadder(
    tag=Theme.VOLTRON,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Unholy Strength", 2,
                   "Three extra power on one creature, and a two-for-one the moment "
                   "that creature is removed."),
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


# Theme.LANDFALL -- Graded by what one land drop buys: a temporary pump -> a
# 2/2 -> a flexible 2/2-or-counters -> a mana -> doubling every trigger you
# own. Even, with the 7-8 rung (mana) a different currency than the rest.
register(AnchorLadder(
    tag=Theme.LANDFALL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Steppe Lynx", 1,
                   "Two power per land drop, until end of turn, on a body that is 0/1 "
                   "otherwise."),
        AnchorCard(PowerBand.B3_4, "Zendikar's Roil", 4,
                   "A 2/2 per land drop, from a permanent that survives creature "
                   "removal -- five mana before it makes anything."),
        AnchorCard(PowerBand.B5_6, "Rampaging Baloths", 5,
                   "A 4/4 per land drop on a 6/6 trampler."),
        AnchorCard(PowerBand.B7_8, "Lotus Cobra", 8,
                   "Turns every land drop into extra mana of any color, so the ramp "
                   "the deck already plays accelerates itself."),
        AnchorCard(PowerBand.B9_10, "Avenger of Zendikar", 9,
                   "Makes a Plant for every land you control, then pumps all of them "
                   "with each further land -- one card is the whole win condition."),
    ],
))


# Theme.BIG_MANA -- This ladder represents the ENGINE side of Big Mana --
# sources that produce outsized mana -- not the payload side (X spells, Genesis
# Wave). Mixing engines and payloads on one ladder would compare two different
# things; the payload side belongs to Role.WIN_CONDITION and
# SynergyTag.X_SPELL. Even steps, measured as mana produced per card.
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


# Theme.REANIMATOR -- A clean cost curve for the same effect: five mana -> four
# -> three-for-two -> two -> one. That is as even as a theme ladder gets, and
# the notes have to carry why cost *is* the power here.
register(AnchorLadder(
    tag=Theme.REANIMATOR,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Miraculous Recovery", 2,
                   "The same five-mana rate, at instant speed, with a counter taped "
                   "on."),
        AnchorCard(PowerBand.B3_4, "Bond of Revival", 3,
                   "Five mana, one creature, but it can attack immediately."),
        AnchorCard(PowerBand.B5_6, "Victimize", 6,
                   "Three mana, sacrifice one creature, get two back -- and the "
                   "sacrificed creature is often something you wanted in the graveyard "
                   "anyway."),
        AnchorCard(PowerBand.B7_8, "Animate Dead", 8,
                   "Two mana, any graveyard, any creature -- it turns the format's "
                   "biggest bodies into two-drops."),
        AnchorCard(PowerBand.B9_10, "Living Death", 9,
                   "Trades every graveyard for every battlefield -- a one-card game "
                   "win in the deck built for it."),
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


# Theme.SACRIFICE -- The outlet side of the Aristocrats/Sacrifice pair: what
# lets you sacrifice, and what it pays. Graded by whether the outlet is free
# and what it returns -- even, with the top rung shifting from 'outlet' to
# 'what sacrificing does to the table'.
register(AnchorLadder(
    tag=Theme.SACRIFICE,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Skull Catapult", 1,
                   "Taxed, tapped, once per turn, for two damage -- the floor of what "
                   "an outlet can charge you."),
        AnchorCard(PowerBand.B3_4, "Barrage of Expendables", 3,
                   "Free to deploy, but every sacrifice costs mana -- filler, because "
                   "the outlet taxes the loop it exists to enable."),
        AnchorCard(PowerBand.B5_6, "Viscera Seer", 6,
                   "One mana, free to activate, and every sacrifice scrys -- it "
                   "smooths every draw for the rest of the game."),
        AnchorCard(PowerBand.B7_8, "Ashnod's Altar", 8,
                   "A free, colorless outlet that *adds two mana* per sacrifice -- it "
                   "pays you to use it, which is what makes loops possible."),
        AnchorCard(PowerBand.B9_10, "Yawgmoth, Thran Physician", 9,
                   "Every sacrifice becomes a removal shot and a card -- the outlet "
                   "that is itself the deck's engine and win condition."),
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


# Theme.CONTROL -- Deliberately avoids counterspells and pure tax permanents so
# the ladder does not collapse into Role.COUNTERSPELL or Role.STAX_PIECE. What
# remains is the control DECK's two real needs: not dying to combat, and out-
# drawing three opponents. Uneven by construction -- the bottom two rungs are
# defensive and the top two are card advantage.
register(AnchorLadder(
    tag=Theme.CONTROL,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Circle of Protection: Red", 1,
                   "Repeatable damage prevention that reads only one color of the "
                   "table -- defense with a hole in it."),
        AnchorCard(PowerBand.B3_4, "Crawlspace", 4,
                   "Caps the table at two attackers per combat -- cheap, permanent, "
                   "and only half an answer."),
        AnchorCard(PowerBand.B5_6, "Windborn Muse", 5,
                   "A tax on attacking you, attached to a body that blocks -- the "
                   "pillow-fort effect in creature form."),
        AnchorCard(PowerBand.B7_8, "Esper Sentinel", 8,
                   "One white mana: each opponent's first noncreature spell each turn "
                   "draws you a card unless they pay a tax equal to this creature's "
                   "power."),
        AnchorCard(PowerBand.B9_10, "Consecrated Sphinx", 9,
                   "Two cards for every card each opponent draws -- in a pod that is "
                   "six cards per turn cycle, from one permanent."),
    ],
))


# Theme.AGGRO -- Judged in a four-player pod against 120 total life, so the
# usual 1v1 aggro payoffs (Hellrider, Goblin Guide) are deliberately absent --
# a card that beats one opponent is not an anchor here. The ladder is therefore
# about mass damage, not efficient single attackers, and reads evenly on that
# axis.
register(AnchorLadder(
    tag=Theme.AGGRO,
    rungs=[
        AnchorCard(PowerBand.B1_2, "Inspired Charge", 2,
                   "A one-shot team pump for four mana -- the kind of damage a pod's "
                   "120 total life shrugs off."),
        AnchorCard(PowerBand.B3_4, "Hall of Triumph", 3,
                   "A colorless anthem for one color of creatures -- permanent, tiny, "
                   "mostly outclassed."),
        AnchorCard(PowerBand.B5_6, "Overrun", 6,
                   "Plus three, three and trample to the whole team -- the honest "
                   "middle of 'attack and win'."),
        AnchorCard(PowerBand.B7_8, "Beastmaster Ascension", 7,
                   "Plus five, five to the whole team once seven creatures have "
                   "attacked -- a staple finisher for go-wide decks."),
        AnchorCard(PowerBand.B9_10, "Triumph of the Hordes", 9,
                   "Infect for the whole team, so ten poison kills a player off a "
                   "board that could never deal forty damage."),
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
# about Equipment, not Equipment cards themselves (that is Role.EQUIPMENT).
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
