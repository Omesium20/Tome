"""The 300-card benchmark sample: its file contract, and an auto-picker for it.

`docs/benchmarking-and-testing.md#the-300-card-sample` specifies 300 cards
stratified into three buckets of 100 — easy, medium, hard — where the buckets
mean something specific: easy is "a card no two reasonable raters would disagree
on", hard is "genuinely ambiguous… where local-vs-frontier disagreement is
expected". The gap between tiers *on the hard bucket* is the number the whole
benchmark exists to produce.

**What this module does and does not claim.** Difficulty, as the doc defines it,
is a judgment about Magic. `select_sample` does not make that judgment; it ranks
the corpus on properties that correlate with it — how much rules text a card
has, how much of that text is modal or conditional, how many separate abilities
it carries — and cuts the ranking into terciles. That is a *stand-in*, and the
sample file it writes says so in a `reviewed: false` field that nothing here
ever sets true. Every consumer surfaces that flag, because a hard-bucket gap
computed over unreviewed buckets is provisional in a way a reader must not have
to infer.

Read-only against the corpus — selecting a sample never writes to either
production database.

Two selection rules are not negotiable and are enforced rather than documented:

**Anchor cards are excluded.** All 255 anchor rungs are quoted verbatim into
the system prompt of every call (`metadata_generator/anchors.py`). Scoring a
model on a card whose hand-assigned answer is sitting in its own prompt measures
recall of the prompt, not judgment about the card.

**Buckets are sampled across colors, mana value and card type**, not taken
straight off the difficulty ranking. The doc warns about this directly: an
unstratified "easy" tercile would skew toward vanilla creatures and an
unstratified "hard" one toward long-text blue instants, and a tier comparison
run on an archetype-skewed sample measures the archetype.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.knowledge.models import Card
from database.knowledge.session import new_session

from ..metadata_generator.anchors import ANCHORS, game_stage_anchor_names
from .store import BUCKETS, Bucket

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
"""Whatever a draw is drawing: a corpus card with signals, or a `SampleCard`."""

CARDS_PER_BUCKET = 100
SAMPLE_SIZE = CARDS_PER_BUCKET * len(BUCKETS)

DEFAULT_SAMPLE_PATH: Path = Path(__file__).resolve().parent / "sample.json"
"""Where the sample lives unless `--out`/`--validate`/`run` override it.

Beside the code that builds it, mirroring `store.DEFAULT_DB_PATH` — but unlike
that module's disposable run log, this file is tracked in git rather than
ignored. It is small (300 rows), it is meant to be read and argued with by a
maintainer (`signals`, `reviewed`), and pinning the exact cards a benchmark run
was scored against matters more than the convenience of forgetting it and
regenerating blind from the seed.
"""

# Phrases that make a card's reading genuinely contingent -- the doc's "modal or
# conditional rules text". Counted, not tested pass/fail, and matched against
# whitespace-normalized lowercase text.
#
# Written as regexes with word boundaries after `lessons-learned.md`'s second
# anchor-selection bias, where a literal SQL `LIKE '%basic land card%'` scored
# Cultivate as having no evidence of being Ramp because the card says "basic
# land cards". A substring test is only as good as its author's memory of the
# exact printed wording; `\b...s?\b` is not.
_MODAL_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bchoose (?:one|two|three|up to)\b",
        r"\binstead\b",
        r"\bunless\b",
        r"\bmay\b",
        r"\bfor each\b",
        r"\bwhenever\b",
        r"\bas long as\b",
        r"\brather than\b",
        r"\bif .{0,40}?, (?:you|that|it|then)\b",
        r"\bexcept\b",
        r"\bas though\b",
        r"\bany number of\b",
        r"\bup to\b",
        r"\bwould\b",
    )
)

# Reminder text in parentheses restates a keyword the model already knows; it
# inflates length without adding a decision.
_REMINDER_TEXT = re.compile(r"\([^)]*\)")

_BASE_TYPES: tuple[str, ...] = (
    "Creature",
    "Planeswalker",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
    "Battle",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def anchor_card_names() -> frozenset[str]:
    """Every card used as a calibration reference, by name.

    Both the `power_rating` ladders and the `game_stage` profiles: each is
    printed into the system prompt of every call, so drawing one as a test
    card would score the model on an answer it was handed.

    `AnchorCard` carries a name and a rating, not an `oracle_id` — the ladders
    are hand-written source, and a name is what a human reviews. Names are
    unique per card in the corpus (`oracle_id` is the key, but `name` is 1:1
    with it for non-token cards), so a name set is a sound exclusion filter.
    """
    return frozenset(
        rung.name for ladder in ANCHORS.values() for rung in ladder.rungs
    ) | game_stage_anchor_names()


# ---------------------------------------------------------------------------
# the file contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SampleCard:
    """One of the 300, with the signals that put it in its bucket.

    `signals` is kept rather than discarded so a later human review of the
    bucketing has something to argue with. A bucket assignment with no stated
    reason can only be accepted or redone from scratch.
    """

    oracle_id: str
    name: str
    bucket: Bucket
    signals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SampleCard:
        return cls(
            oracle_id=str(raw.get("oracle_id", "")),
            name=str(raw.get("name", "")),
            bucket=raw.get("bucket", ""),  # validated by `validate_sample`
            signals=dict(raw.get("signals", {})),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "bucket": self.bucket,
            "signals": self.signals,
        }


@dataclass
class CardSample:
    """The 300 cards plus the provenance needed to judge them later."""

    cards: list[SampleCard] = field(default_factory=list)
    reviewed: bool = False
    generated_at: str = ""
    seed: int | None = None
    corpus: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CardSample:
        return cls(
            cards=[SampleCard.from_json(c) for c in raw.get("cards", [])],
            reviewed=bool(raw.get("reviewed", False)),
            generated_at=str(raw.get("generated_at", "")),
            seed=raw.get("seed"),
            corpus=dict(raw.get("corpus", {})),
            notes=list(raw.get("notes", [])),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "seed": self.seed,
            "reviewed": self.reviewed,
            "corpus": self.corpus,
            "notes": self.notes,
            "cards": [c.to_json() for c in self.cards],
        }

    def by_bucket(self, bucket: str) -> list[SampleCard]:
        return [c for c in self.cards if c.bucket == bucket]

    def bucket_counts(self) -> dict[str, int]:
        return {b: len(self.by_bucket(b)) for b in BUCKETS}

    def oracle_ids(self) -> list[str]:
        return [c.oracle_id for c in self.cards]


@dataclass
class SampleProblem:
    """One thing wrong with a sample, located well enough to go fix it."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def load_sample(path: Path | str) -> CardSample:
    """Read a sample from JSON. Checks are `validate_sample`'s job."""
    return CardSample.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def write_sample(sample: CardSample, path: Path | str) -> None:
    Path(path).write_text(
        json.dumps(sample.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
    )


def validate_sample(
    sample: CardSample, *, require_full_size: bool = True
) -> list[SampleProblem]:
    """Structural checks, offline.

    `require_full_size` exists for tests and for inspecting a trimmed sample;
    a real benchmark run wants the full 100-per-bucket stratification, because
    every aggregate in `report.py` is computed per bucket.
    """
    problems: list[SampleProblem] = []

    seen: dict[str, str] = {}
    for index, card in enumerate(sample.cards):
        spot = f"cards[{index}] {card.name or '(unnamed)'}"
        if not card.oracle_id:
            problems.append(SampleProblem(spot, "no oracle_id"))
        elif card.oracle_id in seen:
            problems.append(
                SampleProblem(
                    spot,
                    f"duplicate oracle_id {card.oracle_id} (already used by "
                    f"{seen[card.oracle_id]!r}); one card scored twice in a bucket "
                    "would weight it double in every aggregate",
                )
            )
        else:
            seen[card.oracle_id] = card.name
        if not card.name:
            problems.append(SampleProblem(spot, "no name"))
        if card.bucket not in BUCKETS:
            problems.append(
                SampleProblem(
                    spot,
                    f"bucket {card.bucket!r} is not one of {', '.join(BUCKETS)}; "
                    "benchmark_runs CHECK-constrains this column",
                )
            )

    anchors = anchor_card_names()
    for card in sample.cards:
        if card.name in anchors:
            problems.append(
                SampleProblem(
                    f"cards[{card.name}]",
                    "is an anchor rung, quoted verbatim into every system prompt. "
                    "Scoring a model on a card whose answer is in its own prompt "
                    "measures the prompt",
                )
            )

    if require_full_size:
        counts = sample.bucket_counts()
        for bucket, count in counts.items():
            if count != CARDS_PER_BUCKET:
                problems.append(
                    SampleProblem(
                        f"bucket[{bucket}]",
                        f"holds {count} cards, expected {CARDS_PER_BUCKET}",
                    )
                )

    return problems


def validate_against_corpus(
    sample: CardSample, session: Session | None = None
) -> list[SampleProblem]:
    """Check every sampled card really is the card it claims to be.

    Same shape and same reasoning as `metadata_generator/verify_anchors.py`'s
    check: opens its own read session when none is passed, batches one `IN`
    query rather than a lookup per card, and only reads.
    """
    if session is None:
        with new_session() as owned:
            return validate_against_corpus(sample, owned)

    oracle_ids = {c.oracle_id for c in sample.cards if c.oracle_id}
    rows = session.scalars(select(Card).where(Card.oracle_id.in_(oracle_ids))).all()
    by_id = {row.oracle_id: row for row in rows}

    problems: list[SampleProblem] = []
    for card in sample.cards:
        spot = f"{card.bucket}/{card.name}"
        if not card.oracle_id:
            problems.append(SampleProblem(spot, "no oracle_id, so nothing can be verified"))
            continue
        row = by_id.get(card.oracle_id)
        if row is None:
            problems.append(
                SampleProblem(
                    spot,
                    f"oracle_id {card.oracle_id} is not in the corpus; run the "
                    "Scryfall importer, or rebuild the sample",
                )
            )
            continue
        if row.name != card.name:
            problems.append(
                SampleProblem(spot, f"the corpus calls {card.oracle_id} {row.name!r}")
            )
        if row.legalities.get("commander") != "legal":
            status = row.legalities.get("commander", "unknown")
            problems.append(
                SampleProblem(
                    spot,
                    f"not Commander-legal ({status}); the prompt tells the model to "
                    "judge the card in Commander, so a card the format cannot play "
                    "has no right answer to score against",
                )
            )
    return problems


# ---------------------------------------------------------------------------
# difficulty scoring
# ---------------------------------------------------------------------------


def _normalize_text(oracle_text: str | None) -> str:
    """Lowercase, drop reminder text in parentheses, collapse whitespace."""
    if not oracle_text:
        return ""
    without_reminders = _REMINDER_TEXT.sub(" ", oracle_text)
    return re.sub(r"\s+", " ", without_reminders).strip().lower()


def _ability_lines(oracle_text: str | None) -> int:
    if not oracle_text:
        return 0
    return len([line for line in oracle_text.splitlines() if line.strip()])


def _base_type(type_line: str) -> str:
    """The card's primary type, for stratification.

    Reads the whole type line rather than only the part before the em dash:
    `_BASE_TYPES` is checked in a fixed priority order so a "Artifact Creature"
    lands under Creature consistently, which is what keeps the strata stable.
    """
    for candidate in _BASE_TYPES:
        if candidate in type_line:
            return candidate
    return "Other"


def _color_bucket(color_identity: Sequence[str]) -> str:
    count = len(color_identity)
    if count == 0:
        return "C"
    if count == 1:
        return color_identity[0]
    return f"M{min(count, 5)}"


def _mana_value_band(mana_value: float) -> str:
    if mana_value <= 1:
        return "0-1"
    if mana_value <= 3:
        return "2-3"
    if mana_value <= 5:
        return "4-5"
    return "6+"


def difficulty_signals(card: Card) -> dict[str, Any]:
    """The per-card properties the bucketing is derived from.

    Every signal is a count or a length that any card can earn, which is the
    first anchor-selection bias in `lessons-learned.md` ("a signal that one
    group cannot physically earn is not a signal") stated as a constraint.
    """
    normalized = _normalize_text(card.oracle_text)
    modal_hits = sum(len(pattern.findall(normalized)) for pattern in _MODAL_MARKERS)
    lines = _ability_lines(card.oracle_text)

    return {
        "text_chars": len(normalized),
        "modal_markers": modal_hits,
        "ability_lines": lines,
        "keywords": len(card.keywords or []),
        "is_vanilla": not normalized,
        "base_type": _base_type(card.type_line or ""),
        "color_bucket": _color_bucket(card.color_identity or []),
        "mana_value_band": _mana_value_band(card.mana_value or 0),
    }


def difficulty_score(signals: dict[str, Any]) -> float:
    """Combine the signals into one orderable number.

    Weights are a judgment, stated here rather than buried: modal and
    conditional language is the strongest available proxy for the doc's
    definition of a hard card ("multi-role cards, modal or conditional rules
    text"), length is next, and separate ability lines capture a card doing
    several unrelated things. A vanilla card is pinned to zero — whatever else
    is true of it, nothing about it is ambiguous.

    These weights decide which tercile a card lands in and nothing else. They
    do not feed the benchmark's metrics, so being approximately right is
    enough; being *reviewed* is what the `reviewed` flag tracks.
    """
    if signals.get("is_vanilla"):
        return 0.0

    return (
        4.0 * float(signals.get("modal_markers", 0))
        + 0.02 * float(signals.get("text_chars", 0))
        + 1.5 * float(signals.get("ability_lines", 0))
        + 0.5 * float(signals.get("keywords", 0))
    )


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def _strata_key(signals: dict[str, Any]) -> tuple[str, str, str]:
    """The stratification key: (color bucket, mana-value band, base type).

    One definition, used by both draws below, so the 300-card selection and a
    session's draw from it cannot drift onto different notions of "spread".
    """
    return (
        str(signals.get("color_bucket", "?")),
        str(signals.get("mana_value_band", "?")),
        str(signals.get("base_type", "?")),
    )


def _round_robin_draw(
    items: Sequence[_T],
    *,
    signals_of: Callable[[_T], dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[_T]:
    """Take `count` items spread as evenly as possible across strata.

    The shared engine behind `_stratified_draw` (over scored corpus cards) and
    `draw_session_cards` (over an already-built sample's cards). Generic over
    the item type because both need the identical guarantee and only differ in
    where the signals hang off the item — and a second copy of this loop is
    exactly where the bias documented below would come back.

    Each stratum is shuffled, then drawn from round-robin, so a draw cannot
    come out as all green ramp spells even if the ranking would have allowed
    it.

    **The visit order is shuffled, not sorted, and that is the whole point.**
    A tercile of ~10,500 cards spreads over a few hundred strata, so a quota of
    100 is filled before one pass finishes — and with strata visited in sorted
    order, the pass never reaches the end of the alphabet. The first build of
    this function did exactly that and produced buckets holding no white or
    blue cards at all, with red appearing twice in 300: `("B", ...)` through
    `("M5", ...)` filled the quota and `("R", ...)`, `("U", ...)`, `("W", ...)`
    were unreachable.

    That is the first bias in `lessons-learned.md` wearing different clothes —
    "a signal that one group cannot physically earn is not a signal" becomes
    "a stratum the draw order never reaches is not a stratum". Shuffling the
    keys with the passed `rng` keeps the draw reproducible from the seed while
    making every stratum equally reachable. Re-shuffled per pass so a second
    pass doesn't inherit the first one's order either.
    """
    strata: dict[tuple[str, str, str], list[_T]] = {}
    for item in items:
        strata.setdefault(_strata_key(signals_of(item)), []).append(item)

    for members in strata.values():
        rng.shuffle(members)

    picked: list[_T] = []
    # sorted() first so the starting point is deterministic, then shuffled so it
    # is not alphabetical; `rng` is seeded, so this is reproducible either way.
    keys = sorted(strata)
    while len(picked) < count:
        rng.shuffle(keys)
        drew_any = False
        for key in keys:
            if len(picked) >= count:
                break
            members = strata[key]
            if members:
                picked.append(members.pop())
                drew_any = True
        if not drew_any:
            break

    return picked


def _stratified_draw(
    scored: list[tuple[float, Card, dict[str, Any]]],
    *,
    count: int,
    rng: random.Random,
) -> list[tuple[Card, dict[str, Any]]]:
    """`count` corpus cards from one difficulty tercile, spread across strata.

    What `select_sample` calls. A thin adapter over `_round_robin_draw` —
    the `(score, card, signals)` triples the ranking produces are dropped to
    `(card, signals)` pairs here, and the spread guarantee lives in one place.
    """
    drawn = _round_robin_draw(
        [(card, signals) for _score, card, signals in scored],
        signals_of=lambda item: item[1],
        count=count,
        rng=rng,
    )
    return list(drawn)


def draw_session_cards(
    sample: CardSample,
    *,
    bucket: str,
    count: int,
    rng: random.Random,
) -> list[SampleCard]:
    """`count` cards from one bucket of an existing sample, spread across strata.

    What a benchmark session draws its card set with. The alternative — taking
    the first `count` of `sample.by_bucket(bucket)` — is what the old `--limit`
    flag effectively did, and it fails twice over: the sample file is ordered
    easy to hard, so a bare head of the whole list could only ever return easy
    cards, and within a bucket it would hand every session the same cards in
    the same order, re-creating inside a session the archetype skew this
    module's docstring exists to prevent.

    Reuses the sample's own recorded `signals`, so a session is stratified on
    exactly the keys the 300 were stratified on. Deterministic given `rng`;
    the caller records the seed on the session.

    Raises:
        ValueError: the bucket holds fewer than `count` cards, or `count` < 1.
            A session that silently ran fewer cards than asked for would make
            two sessions look comparable when they are not.
    """
    if count < 1:
        raise ValueError("count must be at least 1.")

    available = sample.by_bucket(bucket)
    if not available:
        raise ValueError(
            f"The sample holds no {bucket!r} cards. Buckets present: "
            + ", ".join(f"{b}={n}" for b, n in sample.bucket_counts().items())
        )
    if len(available) < count:
        raise ValueError(
            f"Asked for {count} {bucket!r} card(s) but the sample holds only "
            f"{len(available)}. Re-select a full sample (`sample --out`) or "
            "ask for fewer."
        )

    drawn = _round_robin_draw(
        available,
        signals_of=lambda card: card.signals,
        count=count,
        rng=rng,
    )
    # Stable order within the session, so the comparison table reads
    # alphabetically rather than in draw order. The draw decided *which* cards;
    # it has no opinion about the order they are reviewed in.
    return sorted(drawn, key=lambda card: (card.name, card.oracle_id))


def select_sample(
    session: Session | None = None,
    *,
    seed: int = 20261001,
    cards_per_bucket: int = CARDS_PER_BUCKET,
) -> CardSample:
    """Build a 300-card sample from the Commander-legal corpus.

    Deterministic given `seed` and a fixed corpus. Returns a sample with
    `reviewed=False`; see this module's docstring for why that matters.
    """
    if session is None:
        with new_session() as owned:
            return select_sample(
                owned, seed=seed, cards_per_bucket=cards_per_bucket
            )

    rng = random.Random(seed)
    excluded = anchor_card_names()

    total_cards = 0
    scored: list[tuple[float, Card, dict[str, Any]]] = []
    skipped_anchors = 0

    for card in session.scalars(select(Card).order_by(Card.oracle_id)):
        total_cards += 1
        if card.legalities.get("commander") != "legal":
            continue
        if card.name in excluded:
            skipped_anchors += 1
            continue
        signals = difficulty_signals(card)
        scored.append((difficulty_score(signals), card, signals))

    eligible = len(scored)
    needed = cards_per_bucket * len(BUCKETS)
    if eligible < needed:
        raise ValueError(
            f"Only {eligible:,} eligible Commander-legal cards in the corpus, "
            f"need {needed:,}. Has the Scryfall importer run against this "
            "database? (python -m knowledge_pipeline.scryfall_importer)"
        )

    # Sort by score, then oracle_id so ties break identically run to run.
    scored.sort(key=lambda item: (item[0], item[2]["text_chars"], item[1].oracle_id))

    tercile = eligible // 3
    slices: dict[str, list[tuple[float, Card, dict[str, Any]]]] = {
        "easy": scored[:tercile],
        "medium": scored[tercile : tercile * 2],
        "hard": scored[tercile * 2 :],
    }

    sample_cards: list[SampleCard] = []
    for bucket in BUCKETS:
        drawn = _stratified_draw(
            slices[bucket], count=cards_per_bucket, rng=rng
        )
        if len(drawn) < cards_per_bucket:
            raise ValueError(
                f"Could only draw {len(drawn)} of {cards_per_bucket} cards for the "
                f"{bucket!r} bucket after stratification."
            )
        for card, signals in drawn:
            recorded = dict(signals)
            recorded["difficulty_score"] = round(difficulty_score(signals), 3)
            sample_cards.append(
                SampleCard(
                    oracle_id=card.oracle_id,
                    name=card.name,
                    bucket=bucket,  # type: ignore[arg-type]
                    signals=recorded,
                )
            )

    score_bounds = {
        bucket: {
            "min": round(slices[bucket][0][0], 3),
            "max": round(slices[bucket][-1][0], 3),
        }
        for bucket in BUCKETS
        if slices[bucket]
    }

    return CardSample(
        cards=sample_cards,
        reviewed=False,
        generated_at=_utc_iso(),
        seed=seed,
        corpus={
            "cards_in_corpus": total_cards,
            "commander_legal_eligible": eligible,
            "anchor_cards_excluded": skipped_anchors,
            "tercile_size": tercile,
            "difficulty_score_bounds": score_bounds,
        },
        notes=[
            "Buckets are UNREVIEWED heuristics: difficulty was ranked by modal/"
            "conditional marker count, rules-text length, ability-line count and "
            "keyword count, then cut into terciles. See sample.py's docstring.",
            "The hard-bucket gap between tiers is the number this benchmark exists "
            "to produce, so treat it as provisional until `reviewed` is true.",
            "Anchor rungs are excluded: all 255 appear verbatim in every system "
            "prompt.",
        ],
    )


def describe(sample: CardSample) -> str:
    """A short human summary, for the CLI and for a run's log header."""
    lines = [
        f"Sample: {len(sample.cards):,} cards"
        f"  (generated {sample.generated_at or 'unknown'}, seed {sample.seed})",
        f"Buckets: "
        + ", ".join(f"{b}={n}" for b, n in sample.bucket_counts().items()),
    ]
    if not sample.reviewed:
        lines.append(
            "Buckets are UNREVIEWED heuristics -- bucket-split results are "
            "provisional."
        )
    for key, value in sorted(sample.corpus.items()):
        if key != "difficulty_score_bounds":
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def spread(sample: CardSample, bucket: str, key: str) -> dict[str, int]:
    """How one bucket is distributed over a stratification key.

    Exists so a reviewer can check the doc's warning was actually honored
    rather than take the stratification on faith.
    """
    counts: dict[str, int] = {}
    for card in sample.by_bucket(bucket):
        value = str(card.signals.get(key, "?"))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def cards_for_sample(
    sample: CardSample, session: Session, *, oracle_ids: Iterable[str] | None = None
) -> dict[str, Card]:
    """Fetch the corpus rows for a sample, keyed by `oracle_id`.

    One batched query, the same idiom as `validate_against_corpus`. The runner
    needs the full `Card` to render card facts, and 300 single-row lookups
    against a cloud Postgres is a pointless 300 round trips.
    """
    wanted = set(oracle_ids) if oracle_ids is not None else {
        c.oracle_id for c in sample.cards if c.oracle_id
    }
    rows = session.scalars(select(Card).where(Card.oracle_id.in_(wanted))).all()
    return {row.oracle_id: row for row in rows}
