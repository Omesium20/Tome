"""Calls Claude to construct a deck and re-invokes on validation failure until valid.

See docs/architecture.md#deck-generation-pipeline (steps 4-5).
"""


def build_deck(
    build_around_card_names: list[str],
    power_level: str,
    collection_preference: str,
) -> dict:
    raise NotImplementedError
