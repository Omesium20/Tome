"""`generate_metadata`'s per-card loop, now that it makes two calls.

The loop had no test before the split, and the split is exactly the kind of
change that needs one: the failure it guards against is not an exception but
a *sequencing* mistake — pass two called with pass one's prompt, a card
written with only half its fields, or a pass-one failure that still produces
a row. None of those raise; they just write wrong metadata to the shared
corpus.

The backend here is a fake rather than a mock so the assertions are about
what the pipeline *did* (two calls, in order, with the right schema and the
right prompts) rather than about how it was wired.
"""

from datetime import datetime

import pytest
from pydantic import BaseModel

from config import KnowledgeSettings
from database.knowledge.models import Card, CardMetadata
from knowledge_pipeline.metadata_generator.model_backend import MetadataGenerationError
from knowledge_pipeline.metadata_generator.pipeline import generate_metadata
from knowledge_pipeline.metadata_generator.schema import (
    CardMetadataBlueprint,
    GameStageProfile,
    MetadataPassOne,
    MetadataPassTwo,
    Role,
    SynergyTag,
    Theme,
)

PASS_ONE = MetadataPassOne(
    mechanics="Costs {1}. It is an artifact. Tap it to add {C}{C}.",
    synergy_tags=[SynergyTag.ARTIFACT_MATTERS],
    primary_role=Role.MANA_ROCK,
    roles=[Role.MANA_ROCK],
    themes=[Theme.ARTIFACTS],
    strengths=["Nets mana the turn after it lands"],
    weaknesses=["Does nothing on an empty board"],
    game_stage=GameStageProfile(early=10, mid=7, late=4),
)
PASS_TWO = MetadataPassTwo(
    power_rating=9.5, summary="A one-mana rock that pays for itself immediately."
)


class FakeBackend:
    """Records every call, and can be told to fail one pass.

    Returns whatever shape it is asked for, so a pipeline that asked for the
    wrong one fails on the recorded `output_model` rather than silently
    getting a usable object back.
    """

    name = "fake"

    def __init__(self, *, fail_on: type[BaseModel] | None = None) -> None:
        self.calls: list[tuple[type[BaseModel], str, str]] = []
        self._fail_on = fail_on

    def generate(self, *, system: str, prompt: str, output_model):
        self.calls.append((output_model, system, prompt))

        if output_model is self._fail_on:
            raise MetadataGenerationError(f"fake failure on {output_model.__name__}")
        if output_model is MetadataPassOne:
            return PASS_ONE
        if output_model is MetadataPassTwo:
            return PASS_TWO
        raise AssertionError(f"unexpected output_model {output_model!r}")

    @property
    def requested_models(self) -> list[type[BaseModel]]:
        return [model for model, _system, _prompt in self.calls]


@pytest.fixture
def settings() -> KnowledgeSettings:
    return KnowledgeSettings(metadata_batch_size=50)


def make_card(
    session,
    *,
    oracle_id: str = "oracle-1",
    name: str = "Test Rock",
    oracle_text: str = "{T}: Add {C}{C}.",
) -> Card:
    card = Card(
        oracle_id=oracle_id,
        scryfall_id=f"scryfall-{oracle_id}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        oracle_text=oracle_text,
        colors=[],
        color_identity=[],
        type_line="Artifact",
        keywords=[],
        layout="normal",
        legalities={"commander": "legal"},
        updated_at=datetime(2026, 1, 1),
    )
    session.add(card)
    session.commit()
    return card


def run(session, backend, settings, **kwargs):
    return generate_metadata(
        session=session, backend=backend, settings=settings, **kwargs
    )


def test_each_card_takes_two_calls_in_schema_order(knowledge_session, settings):
    make_card(knowledge_session)
    backend = FakeBackend()

    report = run(knowledge_session, backend, settings)

    assert backend.requested_models == [MetadataPassOne, MetadataPassTwo]
    assert report.cards_generated == 1
    assert report.cards_failed == 0


def test_the_two_passes_get_different_system_prompts(knowledge_session, settings):
    """Pass one's block carries the taxonomy; pass two's carries the rubric
    for reading a ladder. Handing either prompt to the other pass is the
    mistake that still returns valid JSON."""
    make_card(knowledge_session)
    backend = FakeBackend()

    run(knowledge_session, backend, settings)
    (_, one_system, one_prompt), (_, two_system, two_prompt) = backend.calls

    assert one_system != two_system
    assert "Valid `roles` values:" in one_system
    assert "Valid `roles` values:" not in two_system
    assert "Calibration ladders" not in one_system
    assert "Calibration ladders" not in two_system
    # Pass two sees the card again, plus what pass one decided about it.
    assert one_prompt in two_prompt
    assert PASS_ONE.mechanics in two_prompt


def test_pass_two_is_shown_the_ladder_for_the_role_pass_one_assigned(
    knowledge_session, settings
):
    """The join the split exists to make: the selection is driven by pass
    one's actual output, not by anything the pipeline guessed."""
    make_card(knowledge_session)
    backend = FakeBackend()

    run(knowledge_session, backend, settings)
    _, _, two_prompt = backend.calls[1]

    assert "Calibration ladders" in two_prompt
    assert Role.MANA_ROCK.value in two_prompt
    # A tag the card does not have must not bring its ladder along.
    assert Role.BOARD_WIPE.value not in two_prompt


def test_the_merged_row_reaches_the_database(knowledge_session, settings):
    make_card(knowledge_session)

    report = run(knowledge_session, FakeBackend(), settings)

    assert report.cards_written == 1
    row = knowledge_session.get(CardMetadata, "oracle-1")
    # Both halves, in one row.
    assert row.mechanics == PASS_ONE.mechanics
    assert row.roles == [Role.MANA_ROCK.value]
    assert row.power_rating == PASS_TWO.power_rating
    assert row.summary == PASS_TWO.summary


def test_a_pass_one_failure_skips_pass_two_entirely(knowledge_session, settings):
    """No point rating a card nothing was learned about, and no row for it."""
    make_card(knowledge_session)
    backend = FakeBackend(fail_on=MetadataPassOne)

    report = run(knowledge_session, backend, settings)

    assert backend.requested_models == [MetadataPassOne]
    assert report.cards_failed == 1
    assert report.cards_failed_pass_one == 1
    assert report.cards_failed_pass_two == 0
    assert report.cards_generated == 0
    assert knowledge_session.get(CardMetadata, "oracle-1") is None


def test_a_pass_two_failure_writes_nothing_rather_than_half_a_card(
    knowledge_session, settings
):
    """`sink.upsert_batches` refreshes every column, so a pass-one-only row
    would be a card with real tags and no rating — worse than no row, because
    the generator's own staleness check would consider it done."""
    make_card(knowledge_session)
    backend = FakeBackend(fail_on=MetadataPassTwo)

    report = run(knowledge_session, backend, settings)

    assert backend.requested_models == [MetadataPassOne, MetadataPassTwo]
    assert report.cards_failed == 1
    assert report.cards_failed_pass_two == 1
    assert report.cards_generated == 0
    assert knowledge_session.get(CardMetadata, "oracle-1") is None


def test_one_failed_card_does_not_abort_the_run(knowledge_session, settings):
    """The rule that predates the split: one bad card must never cost the
    other ~33,000."""
    make_card(knowledge_session, oracle_id="oracle-1", name="First")
    make_card(knowledge_session, oracle_id="oracle-2", name="Second")

    class FailsFirstCard(FakeBackend):
        def generate(self, *, system, prompt, output_model):
            if "First" in prompt and output_model is MetadataPassOne:
                self.calls.append((output_model, system, prompt))
                raise MetadataGenerationError("boom")
            return super().generate(
                system=system, prompt=prompt, output_model=output_model
            )

    report = run(knowledge_session, FailsFirstCard(), settings)

    assert report.cards_seen == 2
    assert report.cards_failed == 1
    assert report.cards_written == 1
    assert knowledge_session.get(CardMetadata, "oracle-2") is not None


def test_a_dry_run_still_calls_twice_but_writes_nothing(knowledge_session, settings):
    make_card(knowledge_session)
    backend = FakeBackend()

    report = run(knowledge_session, backend, settings, dry_run=True)

    assert backend.requested_models == [MetadataPassOne, MetadataPassTwo]
    assert report.cards_generated == 1
    assert report.cards_written == 0
    assert knowledge_session.get(CardMetadata, "oracle-1") is None


def test_the_summary_names_which_pass_lost_the_card(knowledge_session, settings):
    make_card(knowledge_session)

    report = run(knowledge_session, FakeBackend(fail_on=MetadataPassTwo), settings)

    assert "1 in pass two" in report.summary()


def test_a_card_whose_metadata_is_current_is_skipped(knowledge_session, settings):
    """Unchanged by the split, and worth pinning here: the staleness check is
    what stops a rerun from spending two calls per already-done card."""
    make_card(knowledge_session)
    knowledge_session.add(
        CardMetadata(
            **CardMetadataBlueprint.from_passes(PASS_ONE, PASS_TWO).to_card_metadata_row(
                "oracle-1", updated_at=datetime(2026, 6, 1)
            )
        )
    )
    knowledge_session.commit()
    backend = FakeBackend()

    report = run(knowledge_session, backend, settings)

    assert backend.calls == []
    assert report.cards_seen == 0


def test_a_derived_theme_reaches_the_ladder_selection_and_the_stored_row(
    knowledge_session, settings
):
    """The model is never asked about Treasure (`schema.DERIVED_THEMES`), but
    a card that creates one should still be rated against its ladder and
    still carry the tag in the written row — `pipeline.py` merges it in
    between the two calls (`MetadataPassOne.with_derived_themes`)."""
    make_card(
        knowledge_session,
        oracle_text="When this creature enters, create a Treasure token.",
    )
    backend = FakeBackend()

    report = run(knowledge_session, backend, settings)

    _, _, two_prompt = backend.calls[1]
    assert "Treasure" in two_prompt

    assert report.cards_written == 1
    row = knowledge_session.get(CardMetadata, "oracle-1")
    assert "Treasure" in row.themes
