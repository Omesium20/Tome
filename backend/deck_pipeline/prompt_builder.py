"""Assembles the Claude prompt from selected cards, candidates, user preferences,
and Commander rules. See docs/architecture.md#deck-generation-pipeline (step 4).
"""


def build_prompt(
    build_around_cards: list[dict],
    candidates: list[dict],
    power_level: str,
    collection_preference: str,
) -> str:
    raise NotImplementedError
