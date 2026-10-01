"""Turn a reviewed selection into the source that goes into `anchors.py`.

The review page hands back a **selection**, not code: which card each rung
settled on, and any rating the reviewer overrode. Generating the Python here
rather than in the page's JavaScript keeps one implementation of the format, and
keeps it somewhere tests can reach — the output is pasted into a module that
every `power_rating` in the corpus depends on, so "it looked right in the
browser" is not enough.

Selection format, as the page emits it::

    {
      "reviewed": ["Role.RAMP", "Theme.TOKENS"],
      "picks": {
        "Role.RAMP": {
          "1-2": {"name": "Untamed Wilds", "power_rating": 2},
          ...
        }
      }
    }

Only tags listed in `reviewed` are rendered. An unreviewed tag is left out
rather than guessed, which is the same rule `anchors.py` enforces by keeping a
tag either fully anchored or openly unanchored — a half-reviewed ladder in the
registry is indistinguishable from a reviewed one once it is in the corpus.

Picks carry the card's **name**, not its index in the rung. An index silently
means a different card the moment the pool is re-ranked or a candidate is
dropped; a name that no longer exists raises instead.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from ..metadata_generator.anchors import BAND_ORDER
from .pool import BANDS, Candidate, CandidatePool, Ladder

BAND_MEMBER: dict[str, str] = {band.value: f"PowerBand.{band.name}" for band in BAND_ORDER}

HEADER = '''
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
'''.strip()


class SelectionError(ValueError):
    """The selection does not fit the pool it claims to be a selection of."""


def load_selection(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _literal(text: str, indent: int) -> str:
    """`text` as a Python string literal, wrapped and implicitly concatenated."""
    if '"' in text:
        raise SelectionError(
            f"note contains a double quote, which this renderer does not escape: {text!r}"
        )
    width = 88 - indent
    lines = textwrap.wrap(text, width - 3, break_on_hyphens=False, break_long_words=False) or [""]
    if len(lines) == 1:
        return f'"{lines[0]}"'
    pad = " " * indent
    parts = [f'"{lines[0]} "']
    parts += [f'{pad}"{line} "' for line in lines[1:-1]]
    parts.append(f'{pad}"{lines[-1]}"')
    return "\n".join(parts)


def _rating_literal(rating: float) -> str:
    """Ratings are whole numbers in practice; don't write `8.0` for an 8."""
    return str(int(rating)) if float(rating).is_integer() else str(rating)


def _chosen(ladder: Ladder, band: str, pick: dict[str, Any]) -> tuple[Candidate, float]:
    rung = next((r for r in ladder.rungs if r.band == band), None)
    if rung is None:
        raise SelectionError(f"{ladder.enum_member} has no {band} rung to select from")
    name = pick.get("name")
    candidate = next((c for c in rung.candidates if c.name == name), None)
    if candidate is None:
        raise SelectionError(
            f"{ladder.enum_member} [{band}]: {name!r} is not a candidate in this rung. The pool "
            "and the selection are out of step -- re-review rather than guessing."
        )
    rating = float(pick.get("power_rating", candidate.power_rating))
    low, high = (int(x) for x in band.split("-"))
    if not low <= rating <= high:
        raise SelectionError(
            f"{ladder.enum_member} [{band}]: {name} is rated {rating}, outside the band it "
            "illustrates. A rung sitting in a different band than its own teaches the opposite "
            "of what it is for."
        )
    return candidate, rating


def render_ladder(ladder: Ladder, picks: dict[str, Any]) -> str:
    """One `register(AnchorLadder(...))` call, axis comment included."""
    axis = ladder.coherence or "axis not recorded"
    out = [textwrap.fill(f"{ladder.enum_member} -- {axis}", 79,
                         initial_indent="# ", subsequent_indent="# "),
           "register(AnchorLadder(",
           f"    tag={ladder.enum_member},",
           "    rungs=["]
    for band in BANDS:
        if band not in picks:
            raise SelectionError(
                f"{ladder.enum_member} is marked reviewed but has no pick for {band}. A ladder is "
                "complete or absent: a gap is an unanchored band the model fills with a guess."
            )
        candidate, rating = _chosen(ladder, band, picks[band])
        out.append(f'        AnchorCard({BAND_MEMBER[band]}, "{candidate.name}", '
                   f'{_rating_literal(rating)},')
        out.append(" " * 19 + _literal(candidate.note, 19) + "),")
    out += ["    ],", "))"]
    return "\n".join(out)


def render_registry(
    pool: CandidatePool, selection: dict[str, Any], *, header: bool = True
) -> str:
    """Every reviewed ladder as source, ready to paste into `anchors.py`."""
    reviewed = list(selection.get("reviewed", []))
    picks = selection.get("picks", {})
    by_member = {l.enum_member: l for l in pool.ladders}

    unknown = [m for m in reviewed if m not in by_member]
    if unknown:
        raise SelectionError(f"selection reviews ladders the pool does not contain: {unknown}")

    # Registry order follows the pool, which follows the enums -- not the order
    # the reviewer happened to work in, so a rerun produces the same file.
    ordered = [l for l in pool.ladders if l.enum_member in set(reviewed)]
    if not ordered:
        return "# No ladder marked reviewed. Nothing to paste yet."

    blocks = [render_ladder(l, picks.get(l.enum_member, {})) for l in ordered]
    body = "\n\n\n".join(blocks) + "\n"

    pending = [l.enum_member for l in pool.ladders if l.enum_member not in set(reviewed)]
    if pending:
        body += (
            "\n# Still unreviewed, and therefore deliberately unanchored "
            f"({len(pending)}):\n"
            + textwrap.fill(", ".join(pending), 76, initial_indent="#   ",
                            subsequent_indent="#   ")
            + "\n"
        )
    return f"{HEADER}\n\n{body}" if header else body


def selection_problems(pool: CandidatePool, selection: dict[str, Any]) -> list[str]:
    """Whole-set rules checked across the *chosen* cards only.

    A pool may hold two candidates that disagree about a card's rating without
    harm; it becomes a defect the moment both are selected. These are the same
    two rules `pool.validate_pool` describes, applied to the outcome.
    """
    reviewed = set(selection.get("reviewed", []))
    picks = selection.get("picks", {})
    chosen: list[tuple[str, str, str, float]] = []
    for ladder in pool.ladders:
        if ladder.enum_member not in reviewed:
            continue
        for band, pick in picks.get(ladder.enum_member, {}).items():
            try:
                candidate, rating = _chosen(ladder, band, pick)
            except SelectionError as error:
                chosen.append((ladder.enum_member, band, f"!{error}", 0.0))
                continue
            chosen.append((ladder.enum_member, band, candidate.name, rating))

    problems: list[str] = []
    ratings: dict[str, tuple[float, str]] = {}
    placed: dict[str, str] = {}
    for member, band, name, rating in chosen:
        if name.startswith("!"):
            problems.append(name[1:])
            continue
        spot = f"{member} [{band}]"
        known, first = ratings.setdefault(name, (rating, spot))
        if known != rating:
            problems.append(
                f"{name} is chosen at {known} in {first} and {rating} in {spot}; all ladders share "
                "one cached prompt, so one of the two has to be a different card"
            )
        if name in placed:
            problems.append(
                f"{name} anchors both {placed[name]} and {spot}; a shared rung is one fewer "
                "independent reference point"
            )
        placed[name] = spot
    return problems
