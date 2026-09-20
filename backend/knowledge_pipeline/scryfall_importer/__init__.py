"""Pulls bulk card data from the Scryfall API and persists it as Card rows.

Step 1 of the Knowledge Pipeline — see docs/architecture.md#knowledge-pipeline
and docs/knowledge-pipeline.md for the field mapping.

**Every card is imported, always.** There is no format selection: the corpus
is hosted centrally, so it holds the whole card pool and format becomes a
client-side filter over `Card.legalities`. See ``card_filter.py``.

Run it with::

    python -m knowledge_pipeline.scryfall_importer
"""

from .card_filter import NON_CARD_LAYOUTS, is_card
from .mapping import CardRow, to_card_row
from .pipeline import ImportReport, import_cards

__all__ = [
    "CardRow",
    "ImportReport",
    "NON_CARD_LAYOUTS",
    "import_cards",
    "is_card",
    "to_card_row",
]
