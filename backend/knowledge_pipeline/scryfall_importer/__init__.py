"""Pulls bulk card data from the Scryfall API and persists it as Card rows.

Step 1 of the Knowledge Pipeline — see docs/architecture.md#knowledge-pipeline
and docs/knowledge-pipeline.md for the field mapping and format model.

Commander-only for now — see ``formats.py`` for why, and for the disabled
profiles kept as scaffolding.

Run it with::

    python -m knowledge_pipeline.scryfall_importer
"""

from .formats import (
    PROFILES,
    FormatError,
    FormatNotEnabledError,
    FormatProfile,
    UnknownFormatError,
    enabled_profiles,
    resolve,
)
from .mapping import CardRow, to_card_row
from .pipeline import ImportReport, import_cards

__all__ = [
    "CardRow",
    "FormatError",
    "FormatNotEnabledError",
    "FormatProfile",
    "ImportReport",
    "PROFILES",
    "UnknownFormatError",
    "enabled_profiles",
    "import_cards",
    "resolve",
    "to_card_row",
]
