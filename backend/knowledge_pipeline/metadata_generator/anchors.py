"""Anchor cards: one hand-picked calibration example per `Role`/`Theme` value.

See `docs/data-model.md#anchor-cards`. Anchors are what keep `power_rating`
and the role/theme boundary consistent across ~31,830 independent, stateless
generation calls — and across model tiers, once tiering exists: every call,
on every tier, sees the same fixed reference points in its system prompt.

**This registry is intentionally empty until the benchmark fills it in.**
Anchors are drawn from the "easy" bucket of the 300-card benchmark sample
(`docs/benchmarking-and-testing.md#the-300-card-sample`) — hand-labeled,
reviewed examples, not a guess made ahead of that review. A guessed anchor
would be indistinguishable from a reviewed one in the generated corpus, which
is exactly the failure mode this file exists to prevent. `metadata_generator`
runs with an empty registry (logging a warning) so the pipeline is testable
before the benchmark lands; treat a full-corpus run against an empty registry
as a mistake, not a supported mode.

Populate by adding entries, one per enum value that has a clear anchor:

    ANCHORS[Role.RAMP] = AnchorCard(
        name="Cultivate",
        tag=Role.RAMP,
        power_rating=6,
        note="Efficient, colorless-relevant ramp with no upside beyond fixing.",
    )
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import Role, Theme


@dataclass(frozen=True)
class AnchorCard:
    """A fixed few-shot calibration example embedded in the generation prompt."""

    name: str
    tag: Role | Theme
    power_rating: float
    note: str


ANCHORS: dict[Role | Theme, AnchorCard] = {}


def render_anchors() -> str:
    """Render populated anchors as a few-shot block for the system prompt.

    Returns an empty string when `ANCHORS` is empty — the caller decides
    whether that's acceptable (fine for a smoke test; not for a full-corpus
    run, per the module docstring above).
    """
    if not ANCHORS:
        return ""

    lines = [
        f"- {anchor.tag.value}: {anchor.name} (power_rating={anchor.power_rating}) — {anchor.note}"
        for anchor in ANCHORS.values()
    ]
    return (
        "Calibration anchors — fixed reference points. Use these to keep "
        "power_rating and the boundary between adjacent roles/themes "
        "consistent with every other card ever scored against this prompt:\n"
        + "\n".join(lines)
    )
