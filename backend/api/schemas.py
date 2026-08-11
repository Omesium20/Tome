from datetime import datetime

from pydantic import BaseModel


class DeckGenerationRequest(BaseModel):
    build_around_card_names: list[str]
    power_level: str
    collection_preference: str


class CollectionImportResponse(BaseModel):
    quantity_imported: int


class CardSummary(BaseModel):
    id: str
    name: str
    mana_cost: str
    type_line: str
    image_url: str


class DeckSummary(BaseModel):
    id: str
    user_id: str
    commander_id: str
    created_at: datetime
