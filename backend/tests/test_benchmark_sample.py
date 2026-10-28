"""The 300-card sample: its difficulty heuristic, its file contract, its checks.

The heuristic here decides which tercile a card lands in, and nothing else —
it feeds no metric. What these tests pin down is that it *orders* cards the way
the doc's bucket definitions describe (vanilla below plain below modal), that
the validation catches the mistakes which would corrupt an aggregate rather
than merely look untidy, and that two selection rules hold: anchor rungs never
appear in the sample, and buckets come out spread across colors, mana values
and card types instead of straight off the ranking.

Nothing here needs a model, a server, or a configured database.
"""

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from database.knowledge.models import Card
from knowledge_pipeline.benchmark import sample as sample_mod
from knowledge_pipeline.benchmark.sample import (
    CardSample,
    SampleCard,
    difficulty_score,
    difficulty_signals,
)


def make_card(
    oracle_id: str,
    name: str = "Test Card",
    *,
    oracle_text: str | None = "Do a thing.",
    type_line: str = "Creature — Test",
    color_identity: list[str] | None = None,
    mana_value: float = 2.0,
    keywords: list[str] | None = None,
    commander: str = "legal",
) -> Card:
    return Card(
        oracle_id=oracle_id,
        scryfall_id=f"print-{oracle_id}",
        name=name,
        mana_cost="{1}{G}",
        mana_value=mana_value,
        oracle_text=oracle_text,
        colors=["G"],
        color_identity=["G"] if color_identity is None else color_identity,
        type_line=type_line,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        keywords=keywords or [],
        image_url=None,
        layout="normal",
        legalities={"commander": commander},
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    )


def make_sample_card(
    oracle_id: str = "oid-1",
    name: str = "Test Card",
    bucket: str = "easy",
    **signals,
) -> SampleCard:
    return SampleCard(
        oracle_id=oracle_id, name=name, bucket=bucket, signals=dict(signals)
    )


# ---------------------------------------------------------------------------
# the difficulty heuristic
# ---------------------------------------------------------------------------


class TestDifficultySignals:
    def test_vanilla_card_scores_zero(self):
        """Whatever else is true of a card with no rules text, it isn't ambiguous."""
        signals = difficulty_signals(make_card("v", oracle_text=None))
        assert signals["is_vanilla"] is True
        assert difficulty_score(signals) == 0.0

    def test_empty_string_text_counts_as_vanilla(self):
        signals = difficulty_signals(make_card("v", oracle_text="   "))
        assert signals["is_vanilla"] is True

    def test_reminder_text_is_not_counted_as_length(self):
        """Parenthetical reminders restate a keyword; they add no decision.

        Without this, every card with a long reminder (Ward, Cascade, Food)
        would drift upward in difficulty for text the model already knows.
        """
        plain = difficulty_signals(make_card("a", oracle_text="Flying"))
        reminded = difficulty_signals(
            make_card(
                "b",
                oracle_text="Flying (This creature can't be blocked except by "
                "creatures with flying or reach.)",
            )
        )
        assert reminded["text_chars"] == plain["text_chars"]

    def test_modal_language_raises_the_score(self):
        plain = make_card("a", oracle_text="Destroy target creature.")
        modal = make_card(
            "b",
            oracle_text=(
                "Choose one -- Destroy target creature; or draw two cards "
                "unless its controller pays {3}."
            ),
        )
        assert difficulty_score(difficulty_signals(modal)) > difficulty_score(
            difficulty_signals(plain)
        )

    def test_ordering_matches_the_bucket_definitions(self):
        """vanilla < simple < modal, which is what easy/medium/hard mean."""
        vanilla = make_card("a", oracle_text=None)
        simple = make_card("b", oracle_text="{T}: Add {C}{C}.")
        modal = make_card(
            "c",
            oracle_text=(
                "Choose one --\n"
                "- Draw two cards.\n"
                "- Destroy target creature unless its controller pays {3}.\n"
                "Whenever this would deal damage, you may exile it instead "
                "for each creature you control."
            ),
        )
        scores = [
            difficulty_score(difficulty_signals(card))
            for card in (vanilla, simple, modal)
        ]
        assert scores == sorted(scores)
        assert scores[0] < scores[1] < scores[2]

    def test_plural_wording_is_matched(self):
        """Guards the bias `lessons-learned.md` records.

        A literal substring test for "basic land card" scored Cultivate as
        having no evidence of Ramp because the card says "basic land cards".
        The markers here are word-boundary regexes for the same reason.
        """
        singular = difficulty_signals(
            make_card("a", oracle_text="You may search for a basic land card.")
        )
        plural = difficulty_signals(
            make_card("b", oracle_text="You may search for two basic land cards.")
        )
        assert singular["modal_markers"] == plural["modal_markers"] >= 1

    def test_every_card_can_earn_every_signal(self):
        """The first anchor-selection bias, stated as a property.

        `lessons-learned.md`: "a signal that one group cannot physically earn
        is not a signal." Each signal is a count or length over fields every
        card has, so none of them is structurally unreachable.
        """
        signals = difficulty_signals(
            make_card(
                "a",
                oracle_text="Whenever you may choose one, for each creature, unless.",
                keywords=["Flying"],
            )
        )
        assert signals["modal_markers"] > 0
        assert signals["text_chars"] > 0
        assert signals["ability_lines"] > 0
        assert signals["keywords"] > 0

    @pytest.mark.parametrize(
        "type_line,expected",
        [
            ("Creature — Elf Druid", "Creature"),
            ("Artifact Creature — Golem", "Creature"),
            ("Legendary Planeswalker — Teferi", "Planeswalker"),
            ("Instant", "Instant"),
            ("Enchantment — Aura", "Enchantment"),
            ("Land", "Land"),
            ("Conspiracy", "Other"),
        ],
    )
    def test_base_type_is_stable(self, type_line, expected):
        """Priority order, not string position, so strata stay stable."""
        assert difficulty_signals(make_card("a", type_line=type_line))[
            "base_type"
        ] == expected

    @pytest.mark.parametrize(
        "identity,expected",
        [([], "C"), (["G"], "G"), (["W", "U"], "M2"), (["W", "U", "B", "R", "G"], "M5")],
    )
    def test_color_buckets(self, identity, expected):
        assert difficulty_signals(make_card("a", color_identity=identity))[
            "color_bucket"
        ] == expected

    @pytest.mark.parametrize(
        "mana_value,expected",
        [(0, "0-1"), (1, "0-1"), (2, "2-3"), (3, "2-3"), (5, "4-5"), (9, "6+")],
    )
    def test_mana_value_bands(self, mana_value, expected):
        assert difficulty_signals(make_card("a", mana_value=mana_value))[
            "mana_value_band"
        ] == expected


# ---------------------------------------------------------------------------
# the file contract
# ---------------------------------------------------------------------------


class TestSampleFile:
    def test_json_round_trip(self, tmp_path):
        original = CardSample(
            cards=[make_sample_card("a", "A", "easy", difficulty_score=1.0)],
            reviewed=False,
            generated_at="2026-10-02T00:00:00Z",
            seed=7,
            corpus={"cards_in_corpus": 10},
            notes=["a note"],
        )
        path = tmp_path / "sample.json"
        sample_mod.write_sample(original, path)
        loaded = sample_mod.load_sample(path)

        assert loaded.seed == 7
        assert loaded.reviewed is False
        assert loaded.corpus == {"cards_in_corpus": 10}
        assert loaded.notes == ["a note"]
        assert [c.oracle_id for c in loaded.cards] == ["a"]
        assert loaded.cards[0].signals == {"difficulty_score": 1.0}

    def test_bucket_counts_and_views(self):
        sample = CardSample(
            cards=[
                make_sample_card("a", "A", "easy"),
                make_sample_card("b", "B", "hard"),
                make_sample_card("c", "C", "hard"),
            ]
        )
        assert sample.bucket_counts() == {"easy": 1, "medium": 0, "hard": 2}
        assert [c.name for c in sample.by_bucket("hard")] == ["B", "C"]

    def test_describe_flags_unreviewed_buckets(self):
        sample = CardSample(cards=[make_sample_card()], reviewed=False)
        assert "UNREVIEWED" in sample_mod.describe(sample)

    def test_describe_omits_the_flag_once_reviewed(self):
        sample = CardSample(cards=[make_sample_card()], reviewed=True)
        assert "UNREVIEWED" not in sample_mod.describe(sample)


class TestValidateSample:
    def test_a_good_sample_has_no_problems(self):
        sample = CardSample(
            cards=[make_sample_card(f"o{i}", f"Card {i}", "easy") for i in range(3)]
        )
        assert sample_mod.validate_sample(sample, require_full_size=False) == []

    def test_duplicate_oracle_id_is_a_problem(self):
        """One card scored twice would weight it double in every aggregate."""
        sample = CardSample(
            cards=[
                make_sample_card("same", "First", "easy"),
                make_sample_card("same", "Second", "hard"),
            ]
        )
        problems = sample_mod.validate_sample(sample, require_full_size=False)
        assert any("duplicate oracle_id" in str(p) for p in problems)

    def test_unknown_bucket_is_a_problem(self):
        """`bucket` is CHECK-constrained in SQL; catch it before the insert."""
        sample = CardSample(cards=[make_sample_card("a", "A", "tricky")])
        problems = sample_mod.validate_sample(sample, require_full_size=False)
        assert any("is not one of" in str(p) for p in problems)

    def test_missing_oracle_id_is_a_problem(self):
        sample = CardSample(cards=[make_sample_card("", "A", "easy")])
        problems = sample_mod.validate_sample(sample, require_full_size=False)
        assert any("no oracle_id" in str(p) for p in problems)

    def test_anchor_card_in_the_sample_is_a_problem(self):
        """An anchor's answer is quoted in the prompt the model is scored on."""
        anchor_name = sorted(sample_mod.anchor_card_names())[0]
        sample = CardSample(cards=[make_sample_card("a", anchor_name, "easy")])
        problems = sample_mod.validate_sample(sample, require_full_size=False)
        assert any("anchor rung" in str(p) for p in problems)

    def test_wrong_bucket_size_is_a_problem_at_full_size(self):
        sample = CardSample(cards=[make_sample_card("a", "A", "easy")])
        problems = sample_mod.validate_sample(sample, require_full_size=True)
        messages = [str(p) for p in problems]
        assert any("expected 100" in m for m in messages)

    def test_anchor_names_cover_every_shipped_rung(self):
        """330 rungs across 66 ladders, per docs/data-model.md#anchor-cards.

        This count moves with the registry, and has twice: 255/51 originally,
        then 210/42 when the schema.py revision orphaned 9 ladders (Role.RAMP,
        COMBO_PIECE, WIN_CONDITION, EQUIPMENT, AURA; Theme.LANDFALL, SACRIFICE,
        CONTROL, AGGRO), now 330/66 — one ladder for every member of the
        revised vocabulary.

        It is deliberately an equality and not a floor. Every rung here is a
        card the 300-card benchmark sample must exclude, so a rung that
        appears without the sample being re-validated against it is a card
        scored on recall of its own system prompt.

        Since 2026-10 it also covers the 14 `game_stage` profiles, which are
        rendered into pass one's system block for the same reason and must be
        excluded for the same reason. Four of them are already `power_rating`
        rungs, so the union is 339 rather than 344.
        """
        assert len(sample_mod.anchor_card_names()) == 339

    def test_the_shipped_sample_contains_no_calibration_card(self):
        """The exclusion is only worth having if the shipped file obeys it.

        `anchor_card_names` grows whenever a ladder or profile is added, and
        the sample is a frozen file drawn once -- so widening the one does not
        re-filter the other. Adding the `game_stage` profiles put `Argentum
        Armor` in both at once, which would have scored a model on a card
        whose answer was printed in its own system prompt. Caught here rather
        than in a run, and the anchor was swapped.
        """
        shipped = sample_mod.load_sample(sample_mod.DEFAULT_SAMPLE_PATH)
        clash = {card.name for card in shipped.cards} & set(
            sample_mod.anchor_card_names()
        )
        assert not clash, (
            f"{sorted(clash)} are both benchmark sample cards and calibration "
            "reference cards; pick a different anchor or redraw the sample"
        )


class TestValidateAgainstCorpus:
    def test_resolved_commander_legal_cards_pass(self, knowledge_session: Session):
        knowledge_session.add(make_card("oid-1", "Real Card"))
        knowledge_session.commit()

        sample = CardSample(cards=[make_sample_card("oid-1", "Real Card", "easy")])
        assert sample_mod.validate_against_corpus(sample, knowledge_session) == []

    def test_missing_oracle_id_is_reported(self, knowledge_session: Session):
        sample = CardSample(cards=[make_sample_card("ghost", "Ghost", "easy")])
        problems = sample_mod.validate_against_corpus(sample, knowledge_session)
        assert any("not in the corpus" in str(p) for p in problems)

    def test_name_mismatch_is_reported(self, knowledge_session: Session):
        knowledge_session.add(make_card("oid-1", "Actual Name"))
        knowledge_session.commit()

        sample = CardSample(cards=[make_sample_card("oid-1", "Misremembered", "easy")])
        problems = sample_mod.validate_against_corpus(sample, knowledge_session)
        assert any("the corpus calls" in str(p) for p in problems)

    def test_non_commander_legal_card_is_reported(self, knowledge_session: Session):
        """The prompt tells the model to judge the card in Commander."""
        knowledge_session.add(make_card("oid-1", "Banned Thing", commander="banned"))
        knowledge_session.commit()

        sample = CardSample(cards=[make_sample_card("oid-1", "Banned Thing", "easy")])
        problems = sample_mod.validate_against_corpus(sample, knowledge_session)
        assert any("not Commander-legal" in str(p) for p in problems)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def _seed_corpus(session: Session, *, count: int = 60) -> None:
    """A corpus varied enough to exercise terciles and stratification."""
    colors = [[], ["W"], ["U"], ["B"], ["R"], ["G"], ["W", "U"], ["B", "R", "G"]]
    types = [
        "Creature — Elf",
        "Instant",
        "Sorcery",
        "Artifact",
        "Enchantment — Aura",
        "Land",
    ]
    for index in range(count):
        # Difficulty rises with index: no text, then short text, then modal text.
        if index % 3 == 0:
            text = None
        elif index % 3 == 1:
            text = "Add {G}."
        else:
            text = (
                "Choose one -- draw a card; or you may destroy target creature "
                "unless its controller pays {2}. Whenever this happens, for each "
                "creature you control, it instead gains a counter."
            )
        session.add(
            make_card(
                f"oid-{index:03d}",
                f"Card {index:03d}",
                oracle_text=text,
                type_line=types[index % len(types)],
                color_identity=colors[index % len(colors)],
                mana_value=float(index % 8),
            )
        )
    session.commit()


class TestSelectSample:
    def test_selects_the_requested_size_per_bucket(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)

        assert sample.bucket_counts() == {"easy": 5, "medium": 5, "hard": 5}
        assert len(sample.cards) == 15

    def test_is_deterministic_for_a_seed(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        first = sample_mod.select_sample(
            knowledge_session, seed=42, cards_per_bucket=5
        )
        second = sample_mod.select_sample(
            knowledge_session, seed=42, cards_per_bucket=5
        )
        assert first.oracle_ids() == second.oracle_ids()

    def test_different_seeds_can_differ(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        first = sample_mod.select_sample(knowledge_session, seed=1, cards_per_bucket=5)
        second = sample_mod.select_sample(knowledge_session, seed=999, cards_per_bucket=5)
        # Not a strict inequality assertion on content -- a tiny corpus can
        # legitimately collide. The point is that the seed reaches the draw.
        assert first.seed != second.seed

    def test_excludes_non_commander_legal_cards(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        knowledge_session.add(
            make_card("illegal", "Not Legal Here", commander="not_legal")
        )
        knowledge_session.commit()

        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)
        assert "illegal" not in sample.oracle_ids()

    def test_excludes_anchor_cards(self, knowledge_session: Session):
        """The decisive rule: an anchor's answer is in the prompt being scored."""
        _seed_corpus(knowledge_session, count=60)
        anchor_name = sorted(sample_mod.anchor_card_names())[0]
        knowledge_session.add(make_card("anchor-oid", anchor_name))
        knowledge_session.commit()

        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)
        assert "anchor-oid" not in sample.oracle_ids()
        assert anchor_name not in [c.name for c in sample.cards]
        assert sample.corpus["anchor_cards_excluded"] == 1

    def test_easy_bucket_is_less_difficult_than_hard(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)

        easy = [c.signals["difficulty_score"] for c in sample.by_bucket("easy")]
        hard = [c.signals["difficulty_score"] for c in sample.by_bucket("hard")]
        assert max(easy) <= min(hard)

    def test_buckets_are_spread_across_strata(self, knowledge_session: Session):
        """The doc's warning: not accidentally all one archetype."""
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)

        for bucket in ("easy", "medium", "hard"):
            colors = sample_mod.spread(sample, bucket, "color_bucket")
            assert len(colors) > 1, f"{bucket} drew only {colors}"

    def test_no_stratum_is_unreachable_by_draw_order(self, knowledge_session: Session):
        """Regression: the draw must not be biased by the strata's sort order.

        The first version of `_stratified_draw` walked strata in sorted order and
        stopped as soon as the quota filled. With more strata than the quota, the
        pass never reached the end of the alphabet -- the real 300-card sample
        came out with no white or blue cards at all and red appearing twice.

        This builds a corpus with many more strata than the quota so an
        order-biased draw cannot pass: the colors drawn must not all come from
        the front of the sorted order.
        """
        # 10 color buckets x 4 mana-value bands = 40+ strata for a quota of 5,
        # with W and U deliberately last in sort order.
        colors = [["B"], ["C_"], ["G"], ["R"], ["U"], ["W"]]
        for index in range(120):
            identity = colors[index % len(colors)]
            session_identity = [] if identity == ["C_"] else identity
            knowledge_session.add(
                make_card(
                    f"strat-{index:03d}",
                    f"Strat {index:03d}",
                    oracle_text=None if index % 3 == 0 else "Add {G}.",
                    color_identity=session_identity,
                    mana_value=float(index % 4),
                )
            )
        knowledge_session.commit()

        drawn_colors: set[str] = set()
        # Several seeds, because one unlucky seed legitimately drawing a narrow
        # set is not a bug -- a draw that *cannot* reach late strata is.
        for seed in (1, 2, 3, 7, 11):
            sample = sample_mod.select_sample(
                knowledge_session, seed=seed, cards_per_bucket=8
            )
            for bucket in ("easy", "medium", "hard"):
                drawn_colors |= set(sample_mod.spread(sample, bucket, "color_bucket"))

        # W and U sort last; under the old alphabetical draw they were never
        # reachable at all.
        assert "W" in drawn_colors, f"white unreachable; drew {sorted(drawn_colors)}"
        assert "U" in drawn_colors, f"blue unreachable; drew {sorted(drawn_colors)}"

    def test_sample_is_never_marked_reviewed(self, knowledge_session: Session):
        """Nothing in selection may claim a human looked at the buckets."""
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)
        assert sample.reviewed is False
        assert any("unreviewed" in note.lower() for note in sample.notes)

    def test_selection_output_passes_its_own_validation(
        self, knowledge_session: Session
    ):
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)
        assert sample_mod.validate_sample(sample, require_full_size=False) == []
        assert sample_mod.validate_against_corpus(sample, knowledge_session) == []

    def test_too_small_a_corpus_says_so(self, knowledge_session: Session):
        """Named failure rather than a short sample nobody notices."""
        _seed_corpus(knowledge_session, count=6)
        with pytest.raises(ValueError, match="eligible Commander-legal cards"):
            sample_mod.select_sample(knowledge_session, cards_per_bucket=100)

    def test_records_corpus_provenance(self, knowledge_session: Session):
        _seed_corpus(knowledge_session, count=60)
        sample = sample_mod.select_sample(knowledge_session, cards_per_bucket=5)

        assert sample.corpus["cards_in_corpus"] == 60
        assert sample.corpus["commander_legal_eligible"] == 60
        assert sample.generated_at
        assert sample.seed is not None


class TestCardsForSample:
    def test_fetches_rows_keyed_by_oracle_id(self, knowledge_session: Session):
        knowledge_session.add(make_card("oid-1", "One"))
        knowledge_session.add(make_card("oid-2", "Two"))
        knowledge_session.commit()

        sample = CardSample(
            cards=[
                make_sample_card("oid-1", "One", "easy"),
                make_sample_card("oid-2", "Two", "hard"),
            ]
        )
        found = sample_mod.cards_for_sample(sample, knowledge_session)
        assert sorted(found) == ["oid-1", "oid-2"]
        assert found["oid-1"].name == "One"

    def test_omits_cards_not_in_the_corpus(self, knowledge_session: Session):
        sample = CardSample(cards=[make_sample_card("ghost", "Ghost", "easy")])
        assert sample_mod.cards_for_sample(sample, knowledge_session) == {}


# ---------------------------------------------------------------------------
# drawing a session's cards out of an existing sample
#
# The step `--limit` used to stand in for, and the two ways that failed: the
# sample file is ordered easy-to-hard, so a head of it could only ever return
# easy cards; and within a bucket a head would hand every session the same
# cards, re-creating the archetype skew stratification exists to prevent.
# ---------------------------------------------------------------------------


def _spread_sample(per_bucket: int = 12) -> CardSample:
    """A sample with strata to spread over: five colors, three types, 4 bands."""
    colors = ("W", "U", "B", "R", "G")
    types = ("Creature", "Instant", "Land")
    cards = []
    index = 0
    for bucket in ("easy", "medium", "hard"):
        for position in range(per_bucket):
            cards.append(
                SampleCard(
                    oracle_id=f"oid-{bucket}-{position:02d}",
                    name=f"{bucket.title()} Card {position:02d}",
                    bucket=bucket,
                    signals={
                        "color_bucket": colors[index % len(colors)],
                        "mana_value_band": str(index % 4),
                        "base_type": types[index % len(types)],
                    },
                )
            )
            index += 1
    return CardSample(cards=cards, reviewed=False, seed=1)


def test_draw_session_cards_takes_only_the_named_bucket():
    drawn = sample_mod.draw_session_cards(
        _spread_sample(), bucket="hard", count=5, rng=random.Random(1)
    )

    assert len(drawn) == 5
    assert {card.bucket for card in drawn} == {"hard"}


def test_draw_session_cards_is_not_the_head_of_the_bucket():
    """`head -N` is what the old flag effectively did."""
    sample = _spread_sample()
    drawn = sample_mod.draw_session_cards(
        sample, bucket="easy", count=5, rng=random.Random(1)
    )

    head = [card.oracle_id for card in sample.by_bucket("easy")[:5]]
    assert [card.oracle_id for card in drawn] != head


def test_draw_session_cards_spreads_across_strata():
    """A session drawn from one bucket must not be five mono-green creatures,
    for the same reason the 300 are not."""
    drawn = sample_mod.draw_session_cards(
        _spread_sample(), bucket="medium", count=6, rng=random.Random(3)
    )

    colors = {card.signals["color_bucket"] for card in drawn}
    types = {card.signals["base_type"] for card in drawn}
    assert len(colors) >= 3
    assert len(types) >= 2


def test_draw_session_cards_is_reproducible_and_seed_sensitive():
    sample = _spread_sample()
    once = sample_mod.draw_session_cards(
        sample, bucket="hard", count=5, rng=random.Random(11)
    )
    again = sample_mod.draw_session_cards(
        sample, bucket="hard", count=5, rng=random.Random(11)
    )
    other = sample_mod.draw_session_cards(
        sample, bucket="hard", count=5, rng=random.Random(12)
    )

    assert [c.oracle_id for c in once] == [c.oracle_id for c in again]
    assert {c.oracle_id for c in once} != {c.oracle_id for c in other}


def test_draw_session_cards_returns_them_in_a_stable_reading_order():
    """The draw decides *which* cards; the comparison table reads them by name."""
    drawn = sample_mod.draw_session_cards(
        _spread_sample(), bucket="easy", count=6, rng=random.Random(5)
    )

    assert [card.name for card in drawn] == sorted(card.name for card in drawn)


def test_draw_session_cards_refuses_to_quietly_return_fewer():
    """A session that ran fewer cards than asked for would look comparable to
    one that did not."""
    with pytest.raises(ValueError, match="holds only"):
        sample_mod.draw_session_cards(
            _spread_sample(per_bucket=3), bucket="easy", count=10, rng=random.Random(1)
        )


def test_draw_session_cards_refuses_an_empty_bucket():
    sample = CardSample(
        cards=[SampleCard(oracle_id="a", name="A", bucket="easy")], seed=1
    )

    with pytest.raises(ValueError, match="no 'hard' cards"):
        sample_mod.draw_session_cards(
            sample, bucket="hard", count=1, rng=random.Random(1)
        )


def test_draw_session_cards_rejects_a_nonsense_count():
    with pytest.raises(ValueError, match="at least 1"):
        sample_mod.draw_session_cards(
            _spread_sample(), bucket="easy", count=0, rng=random.Random(1)
        )


def test_draw_session_cards_tolerates_a_sample_without_signals():
    """Older sample files, and the trimmed fixtures tests build by hand, carry
    no `signals`; they land in one stratum rather than crashing the draw."""
    sample = CardSample(
        cards=[
            SampleCard(oracle_id=f"o-{i}", name=f"Card {i}", bucket="easy")
            for i in range(4)
        ],
        seed=1,
    )

    drawn = sample_mod.draw_session_cards(
        sample, bucket="easy", count=3, rng=random.Random(1)
    )
    assert len(drawn) == 3
