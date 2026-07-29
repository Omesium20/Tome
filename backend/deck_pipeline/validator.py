"""Validates a Claude-generated decklist against Commander rules in code:
100-card count, commander legality, color identity, singleton.
See docs/architecture.md#deck-generation-pipeline (step 5).
"""


class DeckValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_deck(deck: dict) -> None:
    """Raises DeckValidationError with all violations found, if any."""
    raise NotImplementedError
