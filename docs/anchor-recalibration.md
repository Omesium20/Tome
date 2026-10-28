# Recalibrating the anchor ladders

How to rebuild or revise the anchor ladders in `backend/knowledge_pipeline/metadata_generator/anchors.py`. Maintainer-only; nothing here runs in the pipeline or on a user's machine.

Read `data-model.md#anchor-cards` first — it holds the band rubric and the reasoning, and it is the source of truth. This file is the procedure.

## When you need this

- A `Role` or `Theme` member was added, split, renamed, or dropped, so some members have no ladder and some rungs now grade the wrong tag.
- The band rubric changed.
- A rung turns out to be a poor ruler — it grades the tag on a different property than the four cards around it.

## What a ladder is, and what makes this exacting

Each `Role` and `Theme` member gets **five** anchor cards, one per band of the 1–10 scale (`1-2`, `3-4`, `5-6`, `7-8`, `9-10`). A ladder renders verbatim into the rating prompt of **every card tagged with its `Role` or `Theme`** — selected per card by `anchors.ladders_for`, at most three per card, across a ~31,830-card corpus. (Until recently every call carried all 66, which is itself the subject of a `lessons-learned.md` entry.) The ladders are the only thing keeping `power_rating` meaning the same in card 30,000 as in card 1.

Three consequences:

- **A misremembered card name is not a typo.** It is a permanent, invisible error in every rating calibrated against it. Propose cards by name and verify them against the corpus; never ship a rung from recollection.
- **A ladder is complete or absent.** `AnchorLadder` refuses to construct with fewer than five rungs. A partial ladder is worse than none: the missing band is exactly the region the model must guess, and the four rungs around it make the guess *look* calibrated. If a fifth rung can't be found, leave the tag unanchored and let `missing_tags()` report it.
- **`SynergyTag` members never get ladders.** Only `Role` and `Theme`.

## Doing the work

Write `register(AnchorLadder(...))` calls directly into `anchors.py`, in the style of the calls already there. There is no intermediate format and no handoff file — the registry is the artifact.

**Only touch the ladders in scope.** A ladder whose tag survived a vocabulary revision unchanged is not up for re-review; a card that is still right for its role keeps its rung, its rating and its note. Widening the batch is how a 155-rung job becomes a 330-rung one.

### The three things each rung carries

- **`name`** — exact, as the corpus spells it. Verified in the gate below.
- **`power_rating`** — inside its band's bounds, checked at construction. Read the band rubric in `data-model.md#anchor-cards`, and its two traps: universality is not power (Sol Ring is 7-8, not 10), and cards are judged in a four-player pod.
- **`note`** — **the only reasoning the model ever sees.** It must say *why the card sits in its band*, not restate the oracle text. No double quotes.

### The axis comment

Each ladder carries a comment stating **what actually changes from rung to rung**, and naming the weakest joint. That line is load-bearing, not decoration: a proposed replacement rung can be a fine card and still be a bad rung, because it grades the tag on a different property than its neighbours. Check a swap against the axis, not just against the band rubric. Enforcing the whole-set rules once turned up three rungs whose axis was wrong — `Theme.SACRIFICE`'s top rung graded a payoff on a ladder measuring outlets.

### Two whole-set rules

Both are enforced by `tests/test_anchors.py`, and both decided rungs rather than merely flagging typos:

- **No card carries two different ratings.** Every ladder renders into one shared system block. A card shown as a 9 under one tag and an 8 under another teaches the model, inside a single prompt, that the scale depends on which ladder you read — the exact inconsistency anchors exist to prevent. A disagreement crossing a band boundary cannot be renumbered away; one of the two rungs has to become a different card.
- **No card anchors two ladders.** Weaker, and not a correctness bug — a card legitimately fills several roles. But a shared rung is one fewer independent reference point, and on the `Role`/`Theme` pairs that share a word (Lifegain, Equipment) the same card on both sides actively fails to teach the distinction the two vocabularies exist to draw.

### Picking a good ruler mark

The properties that make a card a reliable *reference point* are not the ones that make it a good card:

- The tag is visible in the card's own oracle text — that text is the only evidence the model gets.
- The rating sits unambiguously inside its band, not on a boundary where reasonable raters disagree.
- The text reads fast; a rung is read ~31,830 times.
- The rung differs clearly from its neighbours, along the ladder's stated axis.
- Obscurity is neither a bonus nor a penalty. Weighting it was a bias the first time (`lessons-learned.md`).

Draw from the "easy" bucket of the benchmark sample (`benchmarking-and-testing.md#ground-truth`) or pick to that bucket's standard: cards whose role, theme and power are obvious enough that labeling them isn't itself a judgment call. Never source a rung from the medium or hard bucket, whose definition is that reasonable raters disagree.

## The two gates

Run from `backend/` with the venv interpreter. Both must pass before the change is done.

```
# 1. Every rung is a real card the format can actually play.
.venv/Scripts/python.exe -m knowledge_pipeline.metadata_generator.verify_anchors

# 2. The set is coherent: five rungs each, rating in band, the two whole-set
#    rules, every Role/Theme member anchored.
.venv/Scripts/python.exe -m pytest tests/test_anchors.py -q
```

Gate 1 reads `KNOWLEDGE_DATABASE_URL` and checks the `game_stage` profiles alongside the ladder rungs, reporting any reference card whose name is absent from the corpus, matches more than one card, or is not Commander-legal. The legality check has a track record: two first picks were Commander-banned when the original ladders were built, which no amount of reading would have caught. Confirm the corpus is reachable before trusting a clean result.

Gate 2 needs no database. It is where the whole-set rules live, and `test_every_role_and_theme_ships_with_a_ladder` is the scoreboard — it fails while any member is unanchored. Do not xfail it, delete it, or weaken it.

## The other calibration set: `game_stage` profiles

`anchors.py` ships **two** sets, and this runbook is mostly about the first. The `power_rating` ladders are 66 of them, one per `Role`/`Theme`, five rungs each. `GAME_STAGE_ANCHORS` is a single global set of 14 profiles, because timing is measured on one clock for the whole format rather than per category (`data-model.md#game_stage-anchors-one-set-not-one-per-tag`).

Editing it follows the same two gates and three extra rules, all enforced by `tests/test_anchors.py`:

- **Each axis must reach both outer thirds.** A set clustered in the middle teaches clustering.
- **The curve shapes must stay balanced** — the test fails if more than half the profiles rise. This is not pedantry: a draft was nine rising out of thirteen, which would have demonstrated the exact monotonic-ramp bias the set exists to correct. Cost-exception cards are back-loaded by nature, so a set chosen to teach the cost rule drifts that way on its own.
- **Reuse across the two sets is allowed**, and four profiles are also `power_rating` rungs. The ladders' no-reuse rule stops one card carrying two different *power ratings*; power and timing are different fields on different scales, so there is nothing to contradict.

One cross-cutting trap, and it applies to a new ladder rung just as much: **widening the calibration sets does not re-filter the benchmark sample.** `sample.anchor_card_names()` keeps reference cards out of the 300-card draw, but that file is frozen and drawn once. Adding the profiles put `Argentum Armor` in both at once, which would have scored a model on a card whose answer was printed in its own system prompt. `test_the_shipped_sample_contains_no_calibration_card` now catches it; when it fails, swap the anchor rather than redrawing the sample, since redrawing breaks comparability with every recorded session.

## Prompt size is a budget the vocabulary spends

Five cards per new member is not only five cards to choose. The 2026-10 revision grew the anchor block 60%, from 27,652 to **44,469 characters (~12,562 tokens)**, and the rung revisions that followed took it to **44,915**. That put it past `benchmark/runner.py`'s `DEFAULT_NUM_CTX = 12288` — the benchmark's default context no longer holds the ladders. Check the rendered size after adding members:

```
.venv/Scripts/python.exe -c "from knowledge_pipeline.metadata_generator.anchors import render_anchors; print(len(render_anchors()))"
```

If it grew, re-check `DEFAULT_NUM_CTX` against it. `runner.py`'s first-call truncation guard refuses a run rather than recording uncalibrated ratings, which is the whole argument for that guard existing.

## When the ladders are in, update these

- **Counts.** `anchors.py`'s module docstring, `ollama_backend.py`'s prompt-size note, `CLAUDE.md`, `docs/architecture.md`, `docs/data-model.md`, `docs/knowledge-pipeline.md` all state ladder and rung totals. They go stale together and silently.
- **`tests/test_benchmark_sample.py::test_anchor_names_cover_every_shipped_rung`** — the expected rung count.
- **`benchmark/sample.json`** — the sample excludes anchor cards, so a new rung that landed in the 300 is a collision. Re-check it.
- **`docs/data-model.md#anchor-cards`** — the provenance paragraph, if how the rungs were chosen changed.
- **`docs/lessons-learned.md`** — only if something was tried one way and changed.
