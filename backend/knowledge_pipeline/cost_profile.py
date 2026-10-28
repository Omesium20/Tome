"""Which cards' `mana_value` misstates what they actually cost to function.

`game_stage.early` asks when a card first does something meaningful. For most
cards that is governed by mana value, but for **17% of the Commander-legal
corpus (5,426 of 31,830)** it is not, and the gap runs in both directions:
Convoke can make a five-drop castable on turn three, while a one-mana
Equipment with an equip cost does nothing until turn four or later.

This module answers the deterministic half of that question -- *is this one of
those cards, and which way does the error run* -- so the prompt can state it as
a fact rather than hoping the model notices. The judgment half, what the timing
then is, stays with the model. That split is the one `CLAUDE.md` requires: the
backend owns what code can decide, the model owns what it cannot.

**Grouped by direction of error, not by keyword.** A keyword list gets two
important cases backwards. `Delve` reads like cost reduction, but an empty
early graveyard makes it a cheap *late* card. `{X}` is worse: `{X}{B}{B}`
reports mana value 3 and is a late-game card wearing an early-game cost. Both
belong with the things that are *more* expensive than they look.

**Two entry points, one classifier.** `facts_from_card` reads a corpus `Card`;
`facts_from_prompt` re-derives the same facts from a rendered card-facts block.
The second exists because `benchmark/report.py` needs to split plain-cost cards
from exceptions to compute its correlation, and that module is required to
reach neither the database nor a model (`CLAUDE.md`) -- but it does hold the
stored `user_prompt`, which carries every field below except `layout`.

**It lives here, at the package root, rather than under `metadata_generator/`
for the same reason.** That package's `__init__` imports `pipeline`, which
pulls in pydantic, the Anthropic SDK and a database session factory, so
importing any submodule of it drags all three in -- `benchmark/store.py`
already documents hitting exactly this and duplicating vocabulary literals to
avoid it. Nothing here imports anything heavier than `re`, and the one `Card`
reference is guarded by `TYPE_CHECKING`, so both the generator and the
reporting layer can depend on it without either inheriting the other's
dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-light on purpose, see module docstring
    from database.knowledge.models import Card

# The marker `report.py` matches on. Exported as a constant so the metric and
# the renderer cannot drift apart on a string literal.
COST_NOTE_PREFIX = "Cost note:"


class CostDirection(StrEnum):
    """Which way a card's mana value misleads about its timing."""

    CHEAPER = "cheaper"
    """Castable for less than its mana value, given the right board or hand."""

    EARLY_MODE = "early_mode"
    """Not cast early, but has a cheap early use that is not casting it."""

    LATER = "later"
    """Costs more than its mana value before it actually does its job."""

    STRUCTURAL = "structural"
    """Mana value is not a measure of this card's cost at all."""


@dataclass(frozen=True)
class CostSignal:
    """One reason a card's mana value misstates its timing."""

    direction: CostDirection
    reason: str
    """A clause for the rendered note. Factual, never an instruction."""


# ---------------------------------------------------------------------------
# The keyword tables.
#
# Every name below was taken from a count against the corpus, not from memory
# -- `keywords` is Scryfall's own list and its spellings are exact ("Level Up",
# "Jump-start", "Start your engines!"). A name that never appears costs
# nothing; a misspelled one silently never fires, which is why these were
# checked rather than written out.
# ---------------------------------------------------------------------------

_CHEAPER_KEYWORDS: dict[str, str] = {
    "Convoke": "Convoke can pay part of it with creatures",
    "Improvise": "Improvise can pay part of it with artifacts",
    "Affinity": "Affinity reduces it",
    "Emerge": "Emerge reduces it by sacrificing a creature",
    "Offering": "Offering reduces it by sacrificing a creature",
    "Evoke": "Evoke is a cheaper alternative cost",
    "Prowl": "Prowl is a cheaper alternative cost",
    "Spectacle": "Spectacle is a cheaper alternative cost",
    "Surge": "Surge is a cheaper alternative cost",
    "Freerunning": "Freerunning is a cheaper alternative cost",
    "Dash": "Dash is a cheaper alternative cost",
    "Blitz": "Blitz is a cheaper alternative cost",
    "Warp": "Warp is a cheaper alternative cost",
    "Prototype": "Prototype is a smaller alternative cost",
    "Miracle": "Miracle is much cheaper when drawn first",
    "Morph": "Morph casts it face down for {3}",
    "Megamorph": "Megamorph casts it face down for {3}",
    "Disguise": "Disguise casts it face down for {3}",
    "Mutate": "Mutate is usually a cheaper alternative cost",
    "Ninjutsu": "Ninjutsu puts it onto the battlefield for a different cost",
}

_EARLY_MODE_KEYWORDS: dict[str, str] = {
    "Cycling": "Cycling gives it a cheap early use",
    "Typecycling": "Typecycling gives it a cheap early use",
    "Landcycling": "Landcycling gives it a cheap early use",
    "Basic landcycling": "Basic landcycling gives it a cheap early use",
    "Channel": "Channel gives it a cheap early use",
    "Foretell": "Foretell splits the cost across two turns",
    "Plot": "Plot pays part of the cost a turn early",
    "Madness": "Madness lets it be discarded early for a different cost",
}

_LATER_KEYWORDS: dict[str, str] = {
    # Attach/activate costs paid after the permanent is already out.
    "Equip": "an Equip cost must be paid before it does anything",
    "Reconfigure": "a Reconfigure cost must be paid before it does anything",
    "Fortify": "a Fortify cost must be paid before it does anything",
    "Level Up": "Level Up must be paid repeatedly before it does its job",
    "Monstrosity": "Monstrosity is a second cost paid later",
    "Adapt": "Adapt is a second cost paid later",
    "Outlast": "Outlast is a second cost paid later",
    "Exhaust": "Exhaust is a second cost paid later",
    "Boast": "Boast is a second cost paid later",
    "Crew": "Crew must be paid before it can attack",
    "Saddle": "Saddle must be paid before it does its job",
    "Station": "Station must be paid repeatedly before it does its job",
    "Craft": "Craft is a second, later cost",
    "Transmute": "Transmute is a separate activation cost",
    "Echo": "Echo must be paid again the following turn",
    "Max speed": "it needs several turns of buildup first",
    "Start your engines!": "it needs several turns of buildup first",
    # Second uses from the graveyard -- later by construction.
    "Flashback": "Flashback is a second, later cost",
    "Escape": "Escape is a second, later cost",
    "Unearth": "Unearth is a second, later cost",
    "Disturb": "Disturb is a second, later cost",
    "Jump-start": "Jump-start is a second, later cost",
    "Retrace": "Retrace is a second, later cost",
    "Encore": "Encore is a second, later cost",
    "Embalm": "Embalm is a second, later cost",
    "Eternalize": "Eternalize is a second, later cost",
    "Aftermath": "Aftermath is a second, later cost",
    "Scavenge": "Scavenge is a second, later cost",
    # The mode worth having costs more than the printed cost.
    "Kicker": "its Kicker mode costs more",
    "Multikicker": "its Multikicker mode costs more",
    "Entwine": "its Entwine mode costs more",
    "Overload": "its Overload mode costs more",
    "Buyback": "its Buyback mode costs more",
    "Replicate": "its Replicate mode costs more",
    "Escalate": "its Escalate mode costs more",
    "Strive": "its Strive mode costs more",
    "Spree": "its Spree modes cost more",
    "Cleave": "its Cleave mode costs more",
    "Conspire": "its Conspire mode costs more",
    "Bestow": "its Bestow mode costs more",
    "Awaken": "its Awaken mode costs more",
    "Casualty": "Casualty is an extra cost",
    "Bargain": "Bargain is an extra cost",
    # Delayed, or dependent on a resource the early game does not have.
    "Suspend": "Suspend delays it by several turns",
    "Impending": "Impending delays what it does by several turns",
    "Delve": "Delve only reduces it once the graveyard is stocked",
}

# `transform` is deliberately absent: on an ordinary double-faced card the
# front face's cost *is* the cost, so mana value is right and a note would be
# noise on 388 cards. Only the layouts where mana value genuinely describes
# something other than one castable face are listed.
_TWO_HALVES = "it has two castable halves, each costing less than the total"

_LAYOUT_REASONS: dict[str, str] = {
    "split": _TWO_HALVES,
    "adventure": _TWO_HALVES,
    "prepare": _TWO_HALVES,
    "modal_dfc": "its back face is a separate, cheaper option, often a land",
}

# Reminder text, which is always parenthesised. Stripped before any oracle
# pattern runs, and that is not a tidying step -- it is load-bearing. Suspend
# and Cascade both *describe* casting something "without paying its mana cost"
# in their reminder text, which matched a free-cast pattern on 231 cards that
# have no such discount themselves. Measured against the corpus: the loose
# pattern hits 543 cards raw and 312 with reminders removed.
_REMINDER = re.compile(r"\([^)]*\)")

_COSTS_LESS = re.compile(r"costs? \{[^}]+\} less to cast", re.IGNORECASE)
_ADDITIONAL_COST = re.compile(r"as an additional cost to cast", re.IGNORECASE)

# Self-referential free/alternative casting only. The loose "without paying
# its mana cost" also catches cards that let you cast *something else* free
# (Cascade, Omniscience-likes) -- a power consideration, not a statement about
# this card's own cost to function. These two phrasings are the ones that are
# about the card carrying them: 107 cards and 16 respectively.
_ALTERNATIVE_COST = re.compile(
    r"rather than pay this spell's mana cost", re.IGNORECASE
)
_FREE_CAST = re.compile(
    r"cast this spell without paying its mana cost", re.IGNORECASE
)


@dataclass(frozen=True)
class CostFacts:
    """The card fields this module reads, and nothing else.

    A plain value object rather than a `Card` so the classifier can run from a
    rendered prompt as readily as from the corpus -- see the module docstring
    for why `report.py` needs the second path.
    """

    name: str = ""
    mana_cost: str = ""
    type_line: str = ""
    oracle_text: str = ""
    keywords: frozenset[str] = field(default_factory=frozenset)
    layout: str = "normal"


def facts_from_card(card: Card) -> CostFacts:
    """Read the classifier's inputs off a corpus `Card`."""
    return CostFacts(
        name=card.name,
        mana_cost=card.mana_cost or "",
        type_line=card.type_line or "",
        oracle_text=card.oracle_text or "",
        keywords=frozenset(card.keywords or ()),
        layout=card.layout or "normal",
    )


_FIELD = {
    "Name": "name",
    "Mana cost": "mana_cost",
    "Type line": "type_line",
}


def facts_from_prompt(card_facts: str) -> CostFacts:
    """Re-derive the inputs from a `render_card_facts` block.

    `layout` is the one field the rendered block does not carry, so the
    two-halves layouts are recovered from a `//` in the **mana cost**. Not from
    the name: an ordinary `transform` card also prints both face names joined
    by `//`, and keying on that misclassified 388 werewolves as split cards.
    Mana cost separates them exactly -- split 123/123, adventure 146/151,
    prepare 46/46, transform 0/388.

    The bounded gap is the 60 modal DFCs whose back face is a land and so carry
    one cost: those read as plain here and as an exception from a `Card`. That
    is 0.2% of the corpus, and the alternative -- giving `report.py` a database
    handle -- would break a separation `CLAUDE.md` states outright.
    """
    values: dict[str, str] = {}
    keywords: frozenset[str] = frozenset()
    oracle: list[str] = []
    in_oracle = False

    for line in card_facts.splitlines():
        head, sep, rest = line.partition(": ")
        if in_oracle and not (sep and head in {*_FIELD, "Keywords", "Legal in",
                                               "Color identity", "Mana value",
                                               "Power/Toughness", "Loyalty",
                                               "Defense"}):
            oracle.append(line)
            continue
        in_oracle = False
        if line.startswith("Oracle text:"):
            in_oracle = True
        elif head == "Keywords" and sep:
            keywords = frozenset(k.strip() for k in rest.split(",") if k.strip())
        elif head in _FIELD and sep:
            values[_FIELD[head]] = rest.strip()

    mana_cost = _absent_if_placeholder(values.get("mana_cost", ""))
    return CostFacts(
        name=_absent_if_placeholder(values.get("name", "")),
        mana_cost=mana_cost,
        type_line=values.get("type_line", ""),
        oracle_text=_absent_if_placeholder("\n".join(oracle).strip()),
        keywords=keywords,
        layout="split" if "//" in mana_cost else "normal",
    )


def _absent_if_placeholder(value: str) -> str:
    """Empty for `render_card_facts`'s stand-ins for a missing field.

    That renderer prints `(none)` for a card with no mana cost and
    `(vanilla - no rules text)` for one with no oracle text, so a naive parse
    reads "this card has no mana cost" as "this card costs `(none)`" -- which
    is truthy, and silently loses the signal on exactly the cards (Pacts,
    suspend-only spells) where it matters most.

    The test is "one parenthesised line and nothing else" rather than merely a
    leading `(`, which is not a nicety: plenty of real oracle text *opens* with
    reminder text, and the looser rule discarded the entire rules text of every
    Class and Saga in the corpus.
    """
    wrapped = value.startswith("(") and value.endswith(")") and "\n" not in value
    return "" if wrapped else value


def cost_signals(facts: CostFacts) -> tuple[CostSignal, ...]:
    """Every way this card's mana value misstates its timing, or `()`.

    An empty result is the common case and the meaningful one: it marks a
    *plain-cost* card, which is the subset the benchmark's
    `corr(mana_value, early)` is computed over.
    """
    signals: list[CostSignal] = []
    text = _REMINDER.sub("", facts.oracle_text)
    is_land = "Land" in facts.type_line

    def add(direction: CostDirection, reason: str) -> None:
        if not any(s.reason == reason for s in signals):
            signals.append(CostSignal(direction, reason))

    for keyword, reason in _CHEAPER_KEYWORDS.items():
        if keyword in facts.keywords:
            add(CostDirection.CHEAPER, reason)
    for keyword, reason in _EARLY_MODE_KEYWORDS.items():
        if keyword in facts.keywords:
            add(CostDirection.EARLY_MODE, reason)
    for keyword, reason in _LATER_KEYWORDS.items():
        if keyword in facts.keywords:
            add(CostDirection.LATER, reason)

    if _COSTS_LESS.search(text):
        add(CostDirection.CHEAPER, "it reduces its own cost")
    if _ALTERNATIVE_COST.search(text):
        add(CostDirection.CHEAPER, "it has an alternative cost that is not mana")
    if _FREE_CAST.search(text):
        add(CostDirection.CHEAPER, "it can be cast for free under a condition")
    if _ADDITIONAL_COST.search(text):
        add(CostDirection.LATER, "it has an additional cost to cast")
    if "{X}" in facts.mana_cost:
        add(
            CostDirection.LATER,
            "mana value counts {X} as zero, so the real cost is whatever X is paid",
        )

    if is_land:
        add(
            CostDirection.STRUCTURAL,
            "a land has no mana cost and can be played from turn one",
        )
    elif not facts.mana_cost:
        add(
            CostDirection.STRUCTURAL,
            "it has no mana cost, so it cannot be cast normally at all",
        )

    layout_reason = _LAYOUT_REASONS.get(facts.layout)
    if layout_reason and not is_land:
        add(CostDirection.STRUCTURAL, layout_reason)

    return tuple(signals)


def has_cost_exception(facts: CostFacts) -> bool:
    """Whether mana value misstates this card's timing. The metric's filter."""
    return bool(cost_signals(facts))


# At most this many clauses reach the note. A card with six of them has a
# complicated cost, and the oracle text saying so is already in the prompt --
# the note's job is to flag the class, not to re-derive the card.
MAX_NOTE_CLAUSES = 3


def cost_note(facts: CostFacts) -> str | None:
    """The one factual line `render_card_facts` appends, or `None`.

    Deliberately a statement about the card rather than an instruction about
    how to score it. `docs/lessons-learned.md` records a pass-one rubric bullet
    that backfired because it read as emphasis; data does not carry that risk,
    and the rubric is where the scoring rule belongs.
    """
    signals = cost_signals(facts)
    if not signals:
        return None

    directions = {s.direction for s in signals}
    if directions == {CostDirection.STRUCTURAL}:
        lead = "mana value is not a measure of this card's cost"
    elif CostDirection.LATER in directions and CostDirection.CHEAPER in directions:
        lead = "mana value misstates the cost to function in both directions"
    elif CostDirection.LATER in directions:
        lead = "mana value understates the cost to function"
    elif CostDirection.CHEAPER in directions:
        lead = "mana value overstates the cost to cast"
    else:
        lead = "mana value is not when this card first does something"

    clauses = "; ".join(s.reason for s in signals[:MAX_NOTE_CLAUSES])
    return f"{COST_NOTE_PREFIX} {lead} — {clauses}."
