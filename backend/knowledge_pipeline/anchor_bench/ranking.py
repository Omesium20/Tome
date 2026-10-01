"""Rank the candidates inside each rung. Triage for a human pass, not a verdict.

The point of an anchor is to be a **ruler mark**, not a good card, so this
scores the properties that make a ruler mark reliable — and those are not the
properties that make a card strong. A format staple everyone recognises can be
a worse rung than an obscure card of the same power, because the model can
substitute reputation absorbed in pretraining for actually applying the rubric.

`rank_pool` adds `score`, `rank`, `verdict` and one `recommended` flag per rung.
It changes nothing else, and nothing in this package applies a recommendation:
on the pool that produced the shipped ladders the top two candidates finished
**exactly tied in 101 of 260 rungs** and within 0.75 in 204. The ranking
reliably separates a clearly-worse candidate from the rest; it rarely picks a
winner among near-equals. Treating it as a decision would quietly replace the
review the whole mechanism depends on.

**Provenance is never an input.** Not `source`, not `profile`, not pool order.
This is the one rule to keep if the signals are ever rewritten, and it was
learned the hard way — see the comment on `profile` in `score_candidate`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .pool import Candidate, CandidatePool, Ladder, Rung


@dataclass(frozen=True)
class Signal:
    """One scored property, and the weight it carries."""

    name: str
    weight: float
    rationale: str


SIGNALS: tuple[Signal, ...] = (
    Signal("text_evidence", 2.0,
           "The ladder's own search predicate is the machine-readable form of 'is this card an "
           "instance of this tag?'. A candidate whose oracle text satisfies it is readable from "
           "the card alone, which is exactly what the model gets; one that does not is leaning "
           "on knowledge the model may not have."),
    Signal("band_extreme", 1.0,
           "Bottom and top bands only. A 1 is a safer floor than a 2 and a 10 a safer ceiling "
           "than a 9, because the outer value cannot be confused with the band next door. Middle "
           "bands touch a neighbour on both sides, so the signal does not apply there."),
    Signal("brevity", 1.0,
           "A rung the model must parse three paragraphs to understand is a worse ruler than one "
           "it reads in a line. Penalised, not just unrewarded, past about 420 characters."),
    Signal("variety", 1.0,
           "Half a point each for a mana value and a card type no other candidate in the rung "
           "shares. Rewards spread at the point where the choice is actually made."),
    Signal("conflict", -3.0,
           "Hard penalty. A card anchored in another ladder at a different rating teaches, inside "
           "one cached prompt, the inconsistency anchors exist to prevent."),
)

# Words too common to be evidence of anything.
STOP = frozenset({
    "a", "an", "the", "you", "your", "of", "to", "for", "or", "and", "that", "this", "it", "its",
    "with", "from", "up", "then", "each", "target", "control", "controls",
})

BREVITY_SHORT = 200
BREVITY_LONG = 420


def predicate_terms(search: str) -> list[list[str]]:
    """The quoted patterns in a ladder's search, as bags of content words.

    Matching a pattern as a literal substring is too brittle to be fair: the
    Ramp ladder's predicate says ``'%basic land card%'`` and Cultivate says
    "basic land **cards**", so an exact test scored a textbook ramp spell as
    having no evidence of being ramp. On the heaviest-weighted signal a false
    negative is worse than a loose match, so each pattern becomes a set of
    content words and a card matches when it carries nearly all of them. That
    change alone cut false negatives on the shipped pool from 380 to 225.
    """
    out: list[list[str]] = []
    for pattern in re.findall(r"'%?([^']+?)%?'", search or ""):
        words = [w for w in re.findall(r"[a-z]+", pattern.lower())
                 if len(w) > 2 and w not in STOP]
        if len(words) >= 2:
            out.append(words)
    return out


def has_evidence(oracle_text: str, terms: list[list[str]]) -> bool:
    """True when the card's own text carries nearly all of any one pattern."""
    text = (oracle_text or "").lower()
    for words in terms:
        hits = sum(1 for w in words if w in text or f"{w.rstrip('s')}s" in text)
        if hits >= max(2, round(len(words) * 0.7 + 0.49)):
            return True
    return False


def mana_value(mana_cost: str | None) -> float | None:
    """Converted cost from a mana cost string; `None` for a card without one.

    Approximate on purpose — hybrid and Phyrexian symbols count as one, `{X}`
    as zero. This feeds a half-point variety tiebreak, not a rules engine.
    """
    if not mana_cost:
        return None
    total = 0.0
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        if symbol.isdigit():
            total += int(symbol)
        elif symbol.upper() != "X":
            total += 1
    return total


def base_type(type_line: str) -> str:
    """Coarse card type, for the variety signal."""
    front = (type_line or "").split("//")[0]
    for kind in ("Land", "Creature", "Planeswalker", "Instant", "Sorcery", "Enchantment",
                 "Artifact", "Battle"):
        if kind in front:
            return kind
    return "Other"


def conflicting_ratings(pool: CandidatePool) -> dict[str, list[tuple[str, float]]]:
    """Cards a pool rates two different ways, with every rating and its tag."""
    uses: dict[str, list[tuple[str, float]]] = {}
    for ladder, _rung, candidate in pool.all_candidates():
        uses.setdefault(candidate.name, []).append((ladder.enum_member, candidate.power_rating))
    return {name: u for name, u in uses.items() if len({rating for _t, rating in u}) > 1}


def score_candidate(
    candidate: Candidate,
    *,
    rung: Rung,
    terms: list[list[str]],
    conflicts: dict[str, list[tuple[str, float]]],
    tag: str,
) -> tuple[float, list[str]]:
    """`candidate`'s score and the reasons behind it, in reading order."""
    score = 0.0
    why: list[str] = []

    if terms:
        if has_evidence(candidate.oracle_text, terms):
            score += 2.0
            why.append("text matches the tag's own predicate")
        else:
            why.append("tag membership is not visible in the oracle text")

    low, high = (int(x) for x in rung.band.split("-"))
    if candidate.power_rating == low == 1:
        score += 1.0
        why.append("a 1 is an unambiguous floor")
    elif candidate.power_rating == high == 10:
        score += 1.0
        why.append("a 10 is an unambiguous ceiling")

    length = len(candidate.oracle_text or "")
    if length and length < BREVITY_SHORT:
        score += 1.0
        why.append("short, quickly-read rules text")
    elif length > BREVITY_LONG:
        score -= 1.0
        why.append("long rules text makes it a slower ruler")

    # Deliberately NOT scored: `profile` (how well known the card is), and
    # `source` (which pass proposed it).
    #
    # The first version of this scorer awarded a point for `profile == "obscure"`,
    # on the sound reasoning that an obscure card is a better ruler because the
    # model cannot substitute reputation for applying the rubric. But only the
    # second search pass recorded a profile at all -- the 500 candidates from the
    # first pass had no such field -- so the bonus was a structural one-point
    # handicap on every older candidate, which no amount of merit could overcome.
    # Removing it moved the recommendation split from 163 new / 97 old to
    # 129 / 131. A signal one group cannot physically earn is not a signal, it is
    # a thumb on the scale. Before weighting any new criterion, check that every
    # candidate is able to score on it.
    others = [c for c in rung.candidates if c is not candidate]
    own_mv = mana_value(candidate.mana_cost)
    if own_mv is not None and all(mana_value(o.mana_cost) != own_mv for o in others):
        score += 0.5
        why.append("unique mana value in this rung")
    own_type = base_type(candidate.type_line)
    if all(base_type(o.type_line) != own_type for o in others):
        score += 0.5
        why.append("unique card type in this rung")

    if candidate.name in conflicts:
        score -= 3.0
        elsewhere = [f"{t} @ {r}" for t, r in conflicts[candidate.name] if t != tag]
        if elsewhere:
            # Pushed to the front, not appended: the verdict shown in the bench is
            # trimmed to the first few reasons, and this is the only one that can
            # disqualify a candidate outright. Appended, a card with three good
            # properties displayed a verdict full of praise and an unexplained
            # low score -- the reviewer saw no reason to distrust it.
            why.insert(0, "rated differently in " + ", ".join(elsewhere))

    return round(score, 2), why


def rank_pool(pool: CandidatePool) -> dict[str, object]:
    """Score, rank and recommend inside every rung. Mutates `pool`.

    Returns the statistics worth printing — above all how often the top two
    candidates tie, which is the honest measure of how much the ranking is
    actually deciding.
    """
    conflicts = conflicting_ratings(pool)
    scored = 0
    no_evidence = 0
    conflicted = 0
    exact_ties = 0
    close_calls: list[str] = []

    for ladder in pool.ladders:
        terms = predicate_terms(ladder.search)
        for rung in ladder.rungs:
            if not rung.candidates:
                continue
            for candidate in rung.candidates:
                score, why = score_candidate(
                    candidate, rung=rung, terms=terms, conflicts=conflicts,
                    tag=ladder.enum_member,
                )
                candidate.score = score
                # Only the first character: `str.capitalize` lowercases the rest, which
                # turned "Role.MANA_FIXING @ 1" into "role.mana_fixing @ 1" in the one
                # place the reviewer reads why a candidate was marked down.
                reasons = "; ".join(why[:3])
                candidate.verdict = reasons[:1].upper() + reasons[1:] + "."
                candidate.recommended = False
                scored += 1
                if terms and not has_evidence(candidate.oracle_text, terms):
                    no_evidence += 1
                if candidate.name in conflicts:
                    conflicted += 1

            order = sorted(
                range(len(rung.candidates)),
                # Ties break on shorter text, then name, so a rerun of the same
                # pool produces the same ranking rather than pool order leaking
                # in as a hidden provenance signal.
                key=lambda i: (-(rung.candidates[i].score or 0.0),
                               len(rung.candidates[i].oracle_text or ""),
                               rung.candidates[i].name),
            )
            for position, index in enumerate(order, start=1):
                rung.candidates[index].rank = position
            best = order[0]
            rung.candidates[best].recommended = True
            rung.recommended = best

            if len(order) > 1:
                top = rung.candidates[order[0]].score or 0.0
                second = rung.candidates[order[1]].score or 0.0
                if top == second:
                    exact_ties += 1
                if abs(top - second) < 0.75:
                    close_calls.append(
                        f"{ladder.enum_member} [{rung.band}]: "
                        f"{rung.candidates[order[0]].name} {top} vs "
                        f"{rung.candidates[order[1]].name} {second}")

    rungs = sum(len(l.rungs) for l in pool.ladders)
    return {
        "scored": scored,
        "rungs": rungs,
        "no_text_evidence": no_evidence,
        "conflicted_candidates": conflicted,
        "conflicted_cards": sorted(conflicts),
        "exact_ties": exact_ties,
        "close_calls": close_calls,
    }
