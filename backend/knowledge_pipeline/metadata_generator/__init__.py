"""Uses a model to generate CardMetadata (roles, themes, synergy tags, ...) for each Card.

Step 2 of the Knowledge Pipeline — see docs/architecture.md#knowledge-pipeline,
docs/data-model.md#cardmetadata, and docs/knowledge-pipeline.md#metadata-generation.

Run it with::

    python -m knowledge_pipeline.metadata_generator
"""

from .pipeline import MetadataReport, generate_metadata
from .schema import CardMetadataBlueprint, GameStageProfile, Role, SynergyTag, Theme

__all__ = [
    "CardMetadataBlueprint",
    "GameStageProfile",
    "MetadataReport",
    "Role",
    "SynergyTag",
    "Theme",
    "generate_metadata",
]
