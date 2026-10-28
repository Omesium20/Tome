"""Check the shipped anchor ladders against the card corpus.

Maintainer tool, run by hand after editing `anchors.py`. **Nothing in the
pipeline imports this**, and it never writes: one `SELECT` against
`KNOWLEDGE_DATABASE_URL` and a report on stdout.

    python -m knowledge_pipeline.metadata_generator.verify_anchors

It answers one question that reading cannot: *is every rung a real card the
format can actually play?* A rung's name is rendered verbatim into the cached
system prompt of every generation call — ~31,830 of them — so a misremembered
name is not a typo. It is a permanent, invisible error in every rating
calibrated against it, and the card that teaches the model what a 9-10
Counterspell looks like is worth nothing if the format has it banned. The pass
that produced the first ladders ran this check on 1,020 candidates and rejected
two for being Commander-banned, which is exactly the class of mistake nobody
notices by reading.

**What this does not check, and why.** The whole-set and structural rules —
five rungs per ladder, a rating inside its band, no card at two different
ratings, no card anchoring two ladders, every `Role`/`Theme` member anchored —
are enforced offline by `AnchorLadder.__post_init__` and by
`tests/test_anchors.py`. They need no database, so they do not belong here.
Together the two are the gates on an anchor edit: this module says the cards
are real, those tests say the set is coherent.

Oracle text is likewise absent on purpose. `AnchorLadder.render()` emits only
band, name, rating and note, so no oracle text ever reaches a prompt from
`anchors.py` and there is no paraphrase to catch. The tool this replaced
checked it because its own JSON pool format carried a copy.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.knowledge.models import Card
from database.knowledge.session import new_session

from . import anchors as _anchors
from .anchors import ANCHORS, game_stage_anchor_names, missing_tags

# `GAME_STAGE_ANCHORS` is read through the module rather than bound here on
# import. `ANCHORS` is a dict that tests mutate in place, so a direct binding
# follows it; the profile set is a tuple, and a direct binding would not --
# leaving two places to patch and one of them easy to forget.


@dataclass
class AnchorProblem:
    """One rung that failed a check, and what was wrong with it."""

    where: str
    problem: str

    def __str__(self) -> str:
        return f"{self.where}: {self.problem}"


def anchor_names() -> set[str]:
    """Every distinct card name printed into a prompt as a reference point.

    Both sets, not just the ladders. A `game_stage` profile is rendered into
    the pass-one system block exactly as a rung is rendered into pass two's,
    so a misremembered name there is the same permanent, invisible error in
    every call — and it is a database question, which is the one thing this
    tool exists to answer.
    """
    return {
        rung.name for ladder in ANCHORS.values() for rung in ladder.rungs
    } | set(game_stage_anchor_names())


def verify_anchors(session: Session | None = None) -> list[AnchorProblem]:
    """Check every rung in `ANCHORS` and every `game_stage` profile.

    Opens its own read session when none is passed. Only reads — this module
    never writes to the corpus.

    Rungs are matched by **name**, since `AnchorCard` carries no `oracle_id`:
    the name is what the prompt shows and therefore the thing that has to be
    right. A name matching several `Card` rows is reported rather than
    resolved, because picking one silently would defeat the point.
    """
    if session is None:
        with new_session() as owned:
            return verify_anchors(owned)

    names = anchor_names()
    rows = session.scalars(select(Card).where(Card.name.in_(names))).all()

    by_name: dict[str, list[Card]] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)

    problems: list[AnchorProblem] = []

    def check(spot: str, name: str) -> None:
        matches = by_name.get(name, [])
        if not matches:
            problems.append(AnchorProblem(
                spot, "not in the corpus under that name; run the Scryfall "
                      "importer, or fix the name"))
            return
        if len(matches) > 1:
            ids = ", ".join(sorted(str(m.oracle_id) for m in matches))
            problems.append(AnchorProblem(
                spot, f"matches {len(matches)} cards ({ids}); the rung has to name "
                      "one of them unambiguously"))
            return
        legality = matches[0].legalities.get("commander", "unknown")
        if legality != "legal":
            problems.append(AnchorProblem(
                spot, f"not Commander-legal ({legality}); an anchor the format "
                      "cannot play is a reference point for nothing"))

    for key, ladder in ANCHORS.items():
        for rung in ladder.rungs:
            check(f"{key} [{rung.band.value}] {rung.name}", rung.name)

    for anchor in _anchors.GAME_STAGE_ANCHORS:
        check(f"game_stage [{anchor.early:.0f}/{anchor.mid:.0f}/"
              f"{anchor.late:.0f}] {anchor.name}", anchor.name)

    return problems


def main(argv: list[str] | None = None) -> int:
    del argv  # no options: there is one registry and one thing to ask of it
    rungs = sum(len(ladder.rungs) for ladder in ANCHORS.values())
    print(
        f"{len(ANCHORS)} ladders, {rungs} rungs, "
        f"{len(_anchors.GAME_STAGE_ANCHORS)} game_stage profiles, "
        f"{len(anchor_names())} distinct cards"
    )

    unanchored = missing_tags()
    if unanchored:
        print(f"\n{len(unanchored)} Role/Theme members have no ladder:")
        for tag in unanchored:
            print(f"  - {tag}")
        print("  (a tag with no ladder is rated freehand — see anchors.py)")

    problems = verify_anchors()
    if problems:
        print(f"\n{len(problems)} problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        "\nEvery rung and game_stage profile resolves to a Commander-legal "
        "card in the corpus."
    )
    return 0 if not unanchored else 1


if __name__ == "__main__":
    sys.exit(main())
