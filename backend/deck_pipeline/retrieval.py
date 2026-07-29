"""Builds a retrieval query from the selected build-around cards and queries ChromaDB
for ~100-200 candidate cards. See docs/architecture.md#deck-generation-pipeline (steps 2-3).
"""


def get_candidates(build_around_card_names: list[str]) -> list[dict]:
    raise NotImplementedError
