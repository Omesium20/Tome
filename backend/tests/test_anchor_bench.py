"""Tests for the anchor recalibration tooling (`knowledge_pipeline/anchor_bench`).

The tool's whole job is to protect a human decision, so these tests concentrate
on the two ways it could quietly stop doing that: letting a bad pool through
validation, and letting provenance leak into the ranking. Both have happened —
the ranking bug is recorded in `ranking.py` and `docs/lessons-learned.md`.

Nothing here touches the knowledge database. `validate_against_corpus` is the
one function that needs it, and it takes a session so a fake can stand in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_pipeline.anchor_bench import example, export, page, pool, ranking
from knowledge_pipeline.anchor_bench.pool import BANDS, CandidatePool


def make_pool(**overrides) -> CandidatePool:
    """The example pool, which is real and valid, as a starting point."""
    raw = example.example_pool()
    raw.update(overrides)
    return CandidatePool.from_json(raw)


def selection_for(loaded: CandidatePool, *, picks: dict[str, str] | None = None) -> dict:
    """Select the first candidate of every rung of every ladder, by name."""
    chosen = picks or {}
    return {
        "reviewed": [l.enum_member for l in loaded.ladders],
        "picks": {
            l.enum_member: {
                r.band: {
                    "name": chosen.get(f"{l.enum_member}|{r.band}", r.candidates[0].name),
                    "power_rating": next(
                        c.power_rating for c in r.candidates
                        if c.name == chosen.get(f"{l.enum_member}|{r.band}",
                                                r.candidates[0].name)
                    ),
                }
                for r in l.rungs
            }
            for l in loaded.ladders
        },
    }


# ------------------------------------------------------------------ the pool ---


def test_the_example_pool_is_structurally_valid():
    """`example` is the contract as something runnable, so it has to pass."""
    assert pool.validate_pool(make_pool()) == []


def test_a_rating_outside_its_band_is_rejected():
    raw = example.example_pool()
    raw["ladders"][0]["rungs"][0]["candidates"][0]["power_rating"] = 7
    problems = pool.validate_pool(CandidatePool.from_json(raw))
    assert any("outside 1-2" in str(p) for p in problems)


def test_a_missing_band_is_rejected():
    raw = example.example_pool()
    raw["ladders"][0]["rungs"] = raw["ladders"][0]["rungs"][:4]
    problems = pool.validate_pool(CandidatePool.from_json(raw))
    assert any("no rung" in str(p) for p in problems)


def test_a_tag_outside_the_closed_vocabulary_is_rejected():
    """The vocabulary in `schema.py` is closed, so a ladder for `Theme.FLYING`
    (dropped) or a typo would build five reference cards nothing ever reads."""
    raw = example.example_pool()
    raw["ladders"][0]["enum_member"] = "Theme.FLYING"
    problems = pool.validate_pool(CandidatePool.from_json(raw))
    assert any("not a Role or Theme member" in str(p) for p in problems)


def test_one_card_rated_two_ways_is_rejected():
    """The rule that decided rungs rather than catching typos: every ladder
    renders into one cached prompt, so two ratings contradict inside it."""
    raw = example.example_pool()
    second = json.loads(json.dumps(raw["ladders"][0]))
    second["enum_member"] = "Role.MANA_FIXING"
    second["tag"] = "Mana Fixing"
    second["rungs"][0]["candidates"][0]["power_rating"] = 1
    raw["ladders"].append(second)
    problems = pool.validate_pool(CandidatePool.from_json(raw))
    assert any("one prompt cannot teach both" in str(p) for p in problems)


def test_a_note_is_required_on_every_candidate():
    raw = example.example_pool()
    raw["ladders"][0]["rungs"][0]["candidates"][0]["note"] = "  "
    problems = pool.validate_pool(CandidatePool.from_json(raw))
    assert any("has no note" in str(p) for p in problems)


def test_a_pool_round_trips_through_json(tmp_path: Path):
    before = make_pool()
    pool.write_pool(before, tmp_path / "p.json")
    after = pool.load_pool(tmp_path / "p.json")
    assert after.to_json() == before.to_json()


def test_missing_ladders_lists_every_unproposed_tag():
    """The example proposes one ladder, so every other tag is unanchored —
    which is reportable, not an error."""
    unanchored = pool.missing_ladders(make_pool())
    assert "Role.RAMP" not in unanchored
    assert "Theme.TOKENS" in unanchored


# ------------------------------------------------------------- corpus checks ---


class FakeCard:
    def __init__(self, oracle_id, name, legal="legal", oracle_text="", mana_cost=None,
                 type_line=""):
        self.oracle_id = oracle_id
        self.name = name
        self.legalities = {"commander": legal}
        self.oracle_text = oracle_text
        self.mana_cost = mana_cost
        self.type_line = type_line


class FakeSession:
    """Stands in for a knowledge-database session: returns the cards it holds."""

    def __init__(self, cards):
        self._cards = list(cards)

    def scalars(self, _statement):
        return self

    def all(self):
        return self._cards


def test_a_candidate_missing_from_the_corpus_is_reported():
    loaded = make_pool()
    problems = pool.validate_against_corpus(loaded, FakeSession([]))
    assert problems and all("is not in the corpus" in str(p) for p in problems)


def test_a_banned_candidate_is_reported():
    """An anchor the format cannot play is a reference point for nothing. Two
    first picks failed exactly this check when the shipped ladders were built."""
    loaded = make_pool()
    cards = [FakeCard(c.oracle_id, c.name, legal="banned", oracle_text=c.oracle_text)
             for _l, _r, c in loaded.all_candidates()]
    problems = pool.validate_against_corpus(loaded, FakeSession(cards))
    assert problems and all("not Commander-legal" in str(p) for p in problems)


def test_a_renamed_candidate_is_reported():
    loaded = make_pool()
    first = next(c for _l, _r, c in loaded.all_candidates())
    cards = [FakeCard(c.oracle_id, "Something Else" if c is first else c.name,
                      oracle_text=c.oracle_text)
             for _l, _r, c in loaded.all_candidates()]
    problems = pool.validate_against_corpus(loaded, FakeSession(cards))
    assert any("the corpus calls" in str(p) for p in problems)


def test_paraphrased_oracle_text_is_reported():
    """A rung is quoted verbatim into every generation prompt, so a paraphrase
    is a permanent invisible error rather than a cosmetic one."""
    loaded = make_pool()
    cards = [FakeCard(c.oracle_id, c.name, oracle_text="something the card does not say")
             for _l, _r, c in loaded.all_candidates()]
    problems = pool.validate_against_corpus(loaded, FakeSession(cards))
    assert any("does not match the corpus" in str(p) for p in problems)


def test_fill_card_facts_takes_the_corpus_version():
    loaded = make_pool()
    cards = [FakeCard(c.oracle_id, c.name, oracle_text="corpus text", mana_cost="{9}",
                      type_line="Artifact")
             for _l, _r, c in loaded.all_candidates()]
    changed = pool.fill_card_facts(loaded, FakeSession(cards))
    assert changed == 7
    assert all(c.oracle_text == "corpus text" for _l, _r, c in loaded.all_candidates())


# ---------------------------------------------------------------- the ranking ---


def test_ranking_scores_and_ranks_every_candidate():
    loaded = make_pool()
    stats = ranking.rank_pool(loaded)
    assert stats["scored"] == 7
    for _l, rung, candidate in loaded.all_candidates():
        assert candidate.score is not None
        assert 1 <= candidate.rank <= len(rung.candidates)
        assert candidate.verdict.endswith(".")


def test_exactly_one_candidate_per_rung_is_recommended():
    loaded = make_pool()
    ranking.rank_pool(loaded)
    for ladder in loaded.ladders:
        for rung in ladder.rungs:
            assert sum(1 for c in rung.candidates if c.recommended) == 1
            assert rung.candidates[rung.recommended].recommended


def test_provenance_never_changes_a_score():
    """The bias that had to be removed: an obscurity bonus only the newer
    candidates could earn was a one-point handicap on every older one. Source
    and profile must not move a score at all."""
    plain = make_pool()
    ranking.rank_pool(plain)
    baseline = {c.name: c.score for _l, _r, c in plain.all_candidates()}

    raw = example.example_pool()
    for ladder in raw["ladders"]:
        for rung in ladder["rungs"]:
            for i, candidate in enumerate(rung["candidates"]):
                candidate["source"] = "primary" if i == 0 else "new"
                candidate["profile"] = "famous" if i == 0 else "obscure"
    tagged = CandidatePool.from_json(raw)
    ranking.rank_pool(tagged)

    assert {c.name: c.score for _l, _r, c in tagged.all_candidates()} == baseline


def test_pool_order_never_changes_a_ranking():
    """Ties break on text length then name, so shuffling a rung cannot promote a
    candidate — otherwise 'whichever was listed first' is a hidden signal."""
    forward = make_pool()
    ranking.rank_pool(forward)
    expected = {c.name: c.rank for _l, _r, c in forward.all_candidates()}

    raw = example.example_pool()
    for ladder in raw["ladders"]:
        for rung in ladder["rungs"]:
            rung["candidates"].reverse()
    reversed_pool = CandidatePool.from_json(raw)
    ranking.rank_pool(reversed_pool)

    assert {c.name: c.rank for _l, _r, c in reversed_pool.all_candidates()} == expected


def test_text_evidence_tolerates_a_plural():
    """The false negative that scored Cultivate as having no evidence of being
    Ramp: the predicate says "basic land card", the card says "cards"."""
    terms = ranking.predicate_terms("oracle_text LIKE '%search your library for a basic land card%'")
    assert ranking.has_evidence(
        "Search your library for up to two basic land cards, put them onto the battlefield.", terms
    )


def test_a_cross_ladder_rating_conflict_is_penalised():
    raw = example.example_pool()
    second = json.loads(json.dumps(raw["ladders"][0]))
    second["enum_member"] = "Role.MANA_FIXING"
    second["rungs"][0]["candidates"] = [second["rungs"][0]["candidates"][0]]
    second["rungs"][0]["candidates"][0]["power_rating"] = 1
    raw["ladders"].append(second)
    conflicted = CandidatePool.from_json(raw)
    stats = ranking.rank_pool(conflicted)
    assert stats["conflicted_cards"] == ["Untamed Wilds"]

    clean = make_pool()
    ranking.rank_pool(clean)
    before = next(c.score for _l, _r, c in clean.all_candidates() if c.name == "Untamed Wilds")
    after = next(c.score for _l, _r, c in conflicted.all_candidates()
                 if c.name == "Untamed Wilds")
    assert after == before - 3.0
    # The reason has to reach the reviewer, with the tag name intact: the verdict
    # is trimmed to its first few reasons, and `str.capitalize` used to lowercase
    # everything after the first letter.
    verdict = next(c.verdict for _l, _r, c in conflicted.all_candidates()
                   if c.name == "Untamed Wilds")
    assert verdict.startswith("Rated differently in Role.MANA_FIXING @ 1")


@pytest.mark.parametrize("cost,expected", [
    (None, None), ("{0}", 0), ("{1}{G}", 2), ("{2}{W}{W}", 4), ("{X}{B}", 1), ("{3}", 3),
])
def test_mana_value_from_a_cost_string(cost, expected):
    assert ranking.mana_value(cost) == expected


# ----------------------------------------------------------------- the export ---


def test_export_renders_a_registry_call_that_matches_anchors_py_style():
    loaded = make_pool()
    source = export.render_registry(loaded, selection_for(loaded), header=False)
    assert "register(AnchorLadder(" in source
    assert "    tag=Role.RAMP," in source
    assert "AnchorCard(PowerBand.B1_2," in source
    # Never a direct assignment: Role and Theme are StrEnums, so `ANCHORS[tag]`
    # would silently collide on the names both vocabularies define.
    assert "ANCHORS[" not in source


def test_exported_source_is_valid_python():
    loaded = make_pool()
    source = export.render_registry(loaded, selection_for(loaded), header=False)
    compile(source, "<generated>", "exec", dont_inherit=True, flags=0)


def test_exported_source_builds_the_ladder_it_describes():
    """The real check: run the generated code against the actual registry API."""
    from knowledge_pipeline.metadata_generator import anchors as anchors_module

    loaded = make_pool()
    source = export.render_registry(loaded, selection_for(loaded), header=False)
    built: dict[str, object] = {}
    namespace = {
        "AnchorCard": anchors_module.AnchorCard,
        "AnchorLadder": anchors_module.AnchorLadder,
        "PowerBand": anchors_module.PowerBand,
        "Role": __import__("knowledge_pipeline.metadata_generator.schema",
                           fromlist=["Role"]).Role,
        "register": lambda ladder: built.setdefault(ladder.tag.name, ladder),
    }
    exec(source, namespace)  # noqa: S102 - the point of the test is that this runs
    ladder = built["RAMP"]
    assert [r.band.value for r in ladder.rungs] == list(BANDS)
    assert ladder.rungs[0].name == "Untamed Wilds"


def test_export_refuses_a_rung_whose_card_left_the_pool():
    """A selection names cards rather than indices so this fails loudly instead
    of silently anchoring whatever now sits at that position."""
    loaded = make_pool()
    selection = selection_for(loaded)
    selection["picks"]["Role.RAMP"]["1-2"]["name"] = "A Card That Was Dropped"
    with pytest.raises(export.SelectionError, match="not a candidate in this rung"):
        export.render_registry(loaded, selection, header=False)


def test_export_refuses_a_reviewed_ladder_with_a_missing_rung():
    loaded = make_pool()
    selection = selection_for(loaded)
    del selection["picks"]["Role.RAMP"]["9-10"]
    with pytest.raises(export.SelectionError, match="no pick for 9-10"):
        export.render_registry(loaded, selection, header=False)


def test_export_renders_only_reviewed_ladders():
    loaded = make_pool()
    selection = selection_for(loaded)
    selection["reviewed"] = []
    assert "register(" not in export.render_registry(loaded, selection)


def test_selection_problems_catches_a_card_chosen_in_two_ladders():
    """Harmless in a pool, a defect once both sides are selected."""
    raw = example.example_pool()
    second = json.loads(json.dumps(raw["ladders"][0]))
    second["enum_member"] = "Role.MANA_FIXING"
    raw["ladders"].append(second)
    loaded = CandidatePool.from_json(raw)
    problems = export.selection_problems(loaded, selection_for(loaded))
    assert any("anchors both" in p for p in problems)


def test_a_note_with_a_double_quote_is_refused_rather_than_mangled():
    raw = example.example_pool()
    raw["ladders"][0]["rungs"][0]["candidates"][0]["note"] = 'It says "draw a card".'
    loaded = CandidatePool.from_json(raw)
    with pytest.raises(export.SelectionError, match="double quote"):
        export.render_registry(loaded, selection_for(loaded), header=False)


# ------------------------------------------------------------------- the page ---


def test_the_page_is_one_self_contained_document():
    loaded = make_pool()
    ranking.rank_pool(loaded)
    html = page.render_page(loaded)
    assert html.startswith("<!doctype html>")
    assert "window.ANCHOR_DATA = {" in html
    # No sibling file and no network: a local file has no server to fetch from,
    # and the artifact CSP blocks it anyway.
    assert 'src="anchor-data.js"' not in html
    assert "Untamed Wilds" in html


def test_the_fragment_form_omits_the_skeleton_for_artifact_publishing():
    html = page.render_page(make_pool(), fragment=True)
    assert "<!doctype" not in html
    assert "<body>" not in html


def test_the_page_never_assumes_a_particular_pool_size():
    """A hard-coded "0 / 52" and an "All 52 ladders" survived into the first
    build of this page. Any such count has to come from the data at runtime, or
    the chrome quietly lies about a pool of a different size. Checked against the
    template rather than a rendered page, since real card data is full of digits."""
    template = page.TEMPLATE.read_text(encoding="utf-8")
    markup = template.split("</style>", 1)[1]
    for stale in ("/ 52", "All 52", "52 tags", "52 ladders", "53 tags"):
        assert stale not in markup


def test_a_template_without_the_placeholder_fails_loudly(monkeypatch, tmp_path: Path):
    empty = tmp_path / "bench.html"
    empty.write_text("<p>no placeholder here</p>", encoding="utf-8")
    monkeypatch.setattr(page, "TEMPLATE", empty)
    with pytest.raises(ValueError, match="nowhere to receive"):
        page.render_page(make_pool())
