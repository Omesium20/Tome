"""The candidate-pool contract, and the checks a pool has to pass.

A pool is one JSON file: for every `Role`/`Theme` member being anchored, five
rungs (one per `PowerBand`), and for every rung a list of candidate cards. It
is the handoff between *proposing* cards — judgment, done by a person or a model
prompted as one — and the mechanical work this package does with them.

Two kinds of validation, deliberately separate:

`validate_pool` is offline and structural: are the bands the five expected ones,
exactly once each; does every rating sit inside the band it is filed under; does
any card carry two different ratings across ladders; does any card appear in two
ladders at all.

`validate_against_corpus` opens the knowledge database and checks each candidate
really is the card it claims to be — the `oracle_id` resolves, the name matches,
the card is Commander-legal, and the oracle text is the corpus's own. This one
matters more than it looks. A rung is quoted verbatim into the cached system
prompt of every generation call, so a misremembered card is not a typo: it is a
permanent, invisible error in every rating calibrated against it. The pass that
produced the shipped ladders ran this on 1020 candidates and rejected two for
being Commander-banned, which is exactly the class of mistake nobody notices by
reading.

The two whole-set rules `validate_pool` enforces are worth stating plainly,
because they are the ones that decided rungs rather than merely flagging typos:

**No card at two different ratings.** Every ladder renders into *one* shared,
cached system block. A card shown as a 9 under one tag and an 8 under another
teaches the model, inside a single prompt, that the scale depends on which
ladder you read — the precise inconsistency anchors exist to remove. A
disagreement that crosses a band boundary cannot be fixed by renumbering; one
of the two rungs has to be a different card.

**No card in two ladders.** Weaker, and not a correctness bug: a card
legitimately fills several roles. But a shared rung is one fewer independent
reference point, and across the `Role`/`Theme` pairs that share a word
(Lifegain, Equipment) the same card on both sides anchors both halves of the
distinction those two vocabularies exist to draw. Enforcing it also surfaces
rungs whose *axis* is wrong rather than merely duplicated, which is how it
earned its keep the first time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.knowledge.models import Card
from database.knowledge.session import new_session

from ..metadata_generator.anchors import BAND_ORDER
from ..metadata_generator.schema import Role, Theme

BANDS: tuple[str, ...] = tuple(band.value for band in BAND_ORDER)
"""The five band labels, taken from `PowerBand` rather than restated here."""

TAGS: dict[str, Role | Theme] = {f"{type(tag).__name__}.{tag.name}": tag for tag in (*Role, *Theme)}
"""Every anchorable tag by qualified name, e.g. `"Role.RAMP"` — the pool's own
`enum_member` key, so a pool naming a member that no longer exists fails loudly
instead of producing a ladder nothing will ever read."""


@dataclass
class PoolProblem:
    """One thing wrong with a pool, located well enough to go fix it."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class Candidate:
    """One proposed card for one rung.

    `note` is the line the prompt will show if this candidate is chosen — it has
    to say *why* the card sits in its band, since that is the only reasoning the
    model sees. `why` is longer and for the human reviewer only; it never
    reaches a prompt.

    `source` and `profile` are provenance, carried through so a reviewer can see
    where a candidate came from, and deliberately **not** scored — see
    `ranking.py`, where ignoring them is the point rather than an oversight.
    """

    name: str
    oracle_id: str
    power_rating: float
    note: str
    why: str = ""
    mana_cost: str | None = None
    type_line: str = ""
    oracle_text: str = ""
    source: str = ""
    profile: str = ""
    # Filled in by `ranking.rank_pool`; absent in a freshly proposed pool.
    score: float | None = None
    rank: int | None = None
    verdict: str = ""
    recommended: bool = False

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Candidate:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> dict[str, Any]:
        out = {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "mana_cost": self.mana_cost,
            "type_line": self.type_line,
            "oracle_text": self.oracle_text,
            "power_rating": self.power_rating,
            "note": self.note,
            "why": self.why,
            "source": self.source,
            "profile": self.profile,
        }
        if self.score is not None:
            out.update(score=self.score, rank=self.rank, verdict=self.verdict,
                       recommended=self.recommended)
        return out


@dataclass
class Rung:
    """One band of one ladder, and everything proposed for it."""

    band: str
    candidates: list[Candidate] = field(default_factory=list)
    variety_note: str = ""
    recommended: int | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Rung:
        return cls(
            band=str(raw.get("band", "")),
            candidates=[Candidate.from_json(c) for c in raw.get("candidates", [])],
            variety_note=raw.get("variety_note", ""),
            recommended=raw.get("recommended"),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "band": self.band,
            "variety_note": self.variety_note,
            "candidates": [c.to_json() for c in self.candidates],
        }
        if self.recommended is not None:
            out["recommended"] = self.recommended
        return out


@dataclass
class Ladder:
    """The five rungs proposed for one tag.

    `coherence` is the ladder's *axis* — what actually changes from rung to rung.
    It is not decoration: a candidate can be a fine card and a bad rung because
    it grades the tag on a different property than the four cards around it, and
    the axis line is the only place that is written down. It is carried into
    `anchors.py` as a comment above each generated ladder for the same reason.

    `search` is the query that found the candidates. `ranking.py` reuses it as
    machine-readable evidence of what the tag means, so it is worth keeping
    accurate rather than approximate.
    """

    enum_member: str
    tag: str = ""
    tag_kind: str = ""
    coherence: str = ""
    search: str = ""
    anchorable: bool = True
    rungs: list[Rung] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Ladder:
        member = str(raw.get("enum_member", ""))
        tag = TAGS.get(member)
        return cls(
            enum_member=member,
            tag=raw.get("tag") or (tag.value if tag else member),
            tag_kind=raw.get("tag_kind") or ("role" if isinstance(tag, Role) else "theme"),
            coherence=raw.get("coherence", ""),
            search=raw.get("search", ""),
            anchorable=bool(raw.get("anchorable", True)),
            rungs=[Rung.from_json(r) for r in raw.get("rungs", [])],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "tag_kind": self.tag_kind,
            "enum_member": self.enum_member,
            "search": self.search,
            "coherence": self.coherence,
            "anchorable": self.anchorable,
            "rungs": [r.to_json() for r in self.rungs],
        }


@dataclass
class CandidatePool:
    """Every ladder under consideration, plus the rubric the reviewer reads."""

    ladders: list[Ladder] = field(default_factory=list)
    band_rubric: dict[str, str] = field(default_factory=dict)
    rubric_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    corpus: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CandidatePool:
        return cls(
            ladders=[Ladder.from_json(l) for l in raw.get("ladders", [])],
            band_rubric=raw.get("band_rubric", {}),
            rubric_notes=list(raw.get("rubric_notes", [])),
            notes=list(raw.get("notes", [])),
            corpus=raw.get("corpus", {}),
            generated_at=raw.get("generated_at", ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "corpus": self.corpus,
            "band_rubric": self.band_rubric,
            "rubric_notes": self.rubric_notes,
            "ladders": [l.to_json() for l in self.ladders],
            "notes": self.notes,
        }

    def all_candidates(self) -> Iterator[tuple[Ladder, Rung, Candidate]]:
        for ladder in self.ladders:
            for rung in ladder.rungs:
                for candidate in rung.candidates:
                    yield ladder, rung, candidate


def load_pool(path: Path | str) -> CandidatePool:
    """Read a pool from JSON. Structural checks are `validate_pool`'s job."""
    return CandidatePool.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def write_pool(pool: CandidatePool, path: Path | str) -> None:
    Path(path).write_text(
        json.dumps(pool.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _band_bounds(band: str) -> tuple[int, int] | None:
    try:
        low, high = band.split("-")
        return int(low), int(high)
    except ValueError:
        return None


def validate_pool(pool: CandidatePool) -> list[PoolProblem]:
    """Every structural problem in `pool`, offline. Empty list means clean."""
    problems: list[PoolProblem] = []

    seen_members: set[str] = set()
    rated: dict[str, tuple[float, str]] = {}
    placed: dict[str, str] = {}

    for ladder in pool.ladders:
        where = ladder.enum_member or "<ladder with no enum_member>"
        if ladder.enum_member not in TAGS:
            problems.append(PoolProblem(
                where, "not a Role or Theme member; the vocabulary in schema.py is closed, "
                       "so a ladder for an unknown tag would never be read"))
        if ladder.enum_member in seen_members:
            problems.append(PoolProblem(where, "two ladders for one tag"))
        seen_members.add(ladder.enum_member)

        bands = [r.band for r in ladder.rungs]
        for band in BANDS:
            if bands.count(band) == 0:
                problems.append(PoolProblem(
                    f"{where} [{band}]", "no rung; a ladder is complete or absent, because the "
                                         "missing band is the one the model has to guess at"))
            elif bands.count(band) > 1:
                problems.append(PoolProblem(f"{where} [{band}]", "two rungs claim this band"))
        for band in bands:
            if band not in BANDS:
                problems.append(PoolProblem(f"{where} [{band}]", f"not a band; expected {BANDS}"))

        for rung in ladder.rungs:
            spot = f"{where} [{rung.band}]"
            if not rung.candidates:
                problems.append(PoolProblem(spot, "no candidates"))
            bounds = _band_bounds(rung.band)
            names = [c.name for c in rung.candidates]
            for name in {n for n in names if names.count(n) > 1}:
                problems.append(PoolProblem(spot, f"{name} is listed twice in this rung"))

            for candidate in rung.candidates:
                if not candidate.name.strip():
                    problems.append(PoolProblem(spot, "a candidate has no name"))
                    continue
                if not candidate.note.strip():
                    problems.append(PoolProblem(
                        spot, f"{candidate.name} has no note; the rung has to say why it sits in "
                              "its band or the model can only pattern-match the name"))
                if bounds and not bounds[0] <= candidate.power_rating <= bounds[1]:
                    problems.append(PoolProblem(
                        spot, f"{candidate.name} is rated {candidate.power_rating}, outside "
                              f"{rung.band}"))

                rating, first = rated.setdefault(candidate.name, (candidate.power_rating, spot))
                if rating != candidate.power_rating:
                    problems.append(PoolProblem(
                        spot, f"{candidate.name} is {rating} in {first} and "
                              f"{candidate.power_rating} here; one prompt cannot teach both"))

    # Cross-ladder reuse is checked on *chosen* rungs, which a pool does not yet
    # have -- so it is only an error when a rung has settled on one candidate.
    for ladder in pool.ladders:
        for rung in ladder.rungs:
            if len(rung.candidates) != 1:
                continue
            name = rung.candidates[0].name
            spot = f"{ladder.enum_member} [{rung.band}]"
            if name in placed:
                problems.append(PoolProblem(
                    spot, f"{name} already anchors {placed[name]}; a shared rung is one fewer "
                          "independent reference point"))
            placed[name] = spot

    return problems


def validate_against_corpus(
    pool: CandidatePool, session: Session | None = None
) -> list[PoolProblem]:
    """Check every candidate against the knowledge database.

    Opens its own read session when none is passed. Only reads — this package
    never writes to the corpus.
    """
    if session is None:
        with new_session() as owned:
            return validate_against_corpus(pool, owned)

    oracle_ids = {c.oracle_id for _l, _r, c in pool.all_candidates() if c.oracle_id}
    rows = session.scalars(select(Card).where(Card.oracle_id.in_(oracle_ids))).all()
    by_id = {row.oracle_id: row for row in rows}

    problems: list[PoolProblem] = []
    for ladder, rung, candidate in pool.all_candidates():
        spot = f"{ladder.enum_member} [{rung.band}] {candidate.name}"
        if not candidate.oracle_id:
            problems.append(PoolProblem(spot, "no oracle_id, so nothing can be verified"))
            continue
        card = by_id.get(candidate.oracle_id)
        if card is None:
            problems.append(PoolProblem(spot, f"oracle_id {candidate.oracle_id} is not in the "
                                              "corpus; run the Scryfall importer, or fix the id"))
            continue
        if card.name != candidate.name:
            problems.append(PoolProblem(spot, f"the corpus calls {candidate.oracle_id} "
                                              f"{card.name!r}"))
        if card.legalities.get("commander") != "legal":
            problems.append(PoolProblem(
                spot, f"not Commander-legal ({card.legalities.get('commander', 'unknown')}); "
                      "an anchor the format cannot play is a reference point for nothing"))
        if candidate.oracle_text and candidate.oracle_text != (card.oracle_text or ""):
            problems.append(PoolProblem(
                spot, "oracle_text does not match the corpus; a rung is quoted verbatim into "
                      "every prompt, so a paraphrase is a permanent invisible error"))
    return problems


def fill_card_facts(pool: CandidatePool, session: Session | None = None) -> int:
    """Overwrite each candidate's printed facts with the corpus's own.

    The cheap way to make `validate_against_corpus` pass honestly: rather than
    trusting a proposed pool's recollection of mana cost, type line and oracle
    text, take them from the database. Returns how many candidates changed.
    """
    if session is None:
        with new_session() as owned:
            return fill_card_facts(pool, owned)

    oracle_ids = {c.oracle_id for _l, _r, c in pool.all_candidates() if c.oracle_id}
    rows = session.scalars(select(Card).where(Card.oracle_id.in_(oracle_ids))).all()
    by_id = {row.oracle_id: row for row in rows}

    changed = 0
    for _ladder, _rung, candidate in pool.all_candidates():
        card = by_id.get(candidate.oracle_id)
        if card is None:
            continue
        facts = (card.name, card.mana_cost, card.type_line, card.oracle_text or "")
        if (candidate.name, candidate.mana_cost, candidate.type_line,
                candidate.oracle_text) != facts:
            candidate.name, candidate.mana_cost, candidate.type_line, candidate.oracle_text = facts
            changed += 1
    return changed


def missing_ladders(pool: CandidatePool) -> list[str]:
    """Anchorable tags the pool proposes nothing for."""
    present = {l.enum_member for l in pool.ladders}
    return [member for member in TAGS if member not in present]


def summarize(pool: CandidatePool) -> dict[str, Any]:
    """Counts worth printing after any step."""
    candidates = [c for _l, _r, c in pool.all_candidates()]
    return {
        "ladders": len(pool.ladders),
        "rungs": sum(len(l.rungs) for l in pool.ladders),
        "candidates": len(candidates),
        "distinct_cards": len({c.name for c in candidates}),
        "unanchored_tags": len(missing_ladders(pool)),
    }
