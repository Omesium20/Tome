"""Tooling for rebuilding the anchor ladders in `metadata_generator/anchors.py`.

`anchors.py` holds 51 five-rung ladders — the fixed reference points every
`power_rating` in the corpus is measured against (`docs/data-model.md#anchor-cards`).
Those rungs are a human decision, and this package is what makes that decision
repeatable: given a pool of candidate cards, it validates them against the
corpus, ranks them on the properties that make a *ruler mark* reliable, and
builds a self-contained review page where a person picks each rung and copies
out the generated registry.

**Why this exists even though the ladders are already chosen.** The ladders set
the meaning of the whole 1-10 scale, so revising one is not a casual edit — and
the first time they were built, every step lived in throwaway scripts. If the
rubric changes, the vocabulary gains a member, or a rung turns out to be a bad
ruler, the work should start from a tool rather than from scratch. Nothing here
runs in production or touches a user's machine; it is maintainer tooling, used
once in a while and never by the pipeline.

**What it deliberately does not do: pick cards.** Proposing candidates is a
judgment call about Magic, made by a person or by a model prompted as one, and
the output lands here as a JSON pool (`pool.py` states the contract, and
`python -m knowledge_pipeline.anchor_bench example` writes a valid one). The
ranking that follows is triage, not a decision: on the pool that produced the
shipped ladders it left the top two candidates exactly tied in 101 of 260
rungs. A tool that chose rungs by score would quietly replace the review this
whole mechanism depends on.

The loop, start to finish::

    # 1. a pool of candidates arrives as JSON -- see `pool.py` for the contract
    python -m knowledge_pipeline.anchor_bench example -o pool.json

    # 2. does every candidate actually exist, at the rating it claims?
    python -m knowledge_pipeline.anchor_bench validate pool.json

    # 3. rank within each rung -- adds score/rank/recommended, decides nothing
    python -m knowledge_pipeline.anchor_bench rank pool.json -o ranked.json

    # 4. the human pass: one page, no server, opens offline
    python -m knowledge_pipeline.anchor_bench page ranked.json -o bench.html

Step 4's page carries an Export tab that emits `register(AnchorLadder(...))`
source for the ladders marked reviewed, to paste into `anchors.py`. Only
reviewed ladders appear: an unreviewed tag is left out rather than guessed,
which is the same distinction `anchors.py` enforces by shipping a tag either
fully anchored or openly unanchored.
"""

from .pool import (
    BANDS,
    Candidate,
    CandidatePool,
    Ladder,
    PoolProblem,
    Rung,
    load_pool,
    validate_against_corpus,
    validate_pool,
)
from .ranking import SIGNALS, rank_pool

__all__ = [
    "BANDS",
    "SIGNALS",
    "Candidate",
    "CandidatePool",
    "Ladder",
    "PoolProblem",
    "Rung",
    "load_pool",
    "rank_pool",
    "validate_against_corpus",
    "validate_pool",
]
