from fastapi import APIRouter
from pydantic import BaseModel

from deck_pipeline import generator, retrieval, validator

router = APIRouter()


class DeckGenerationRequest(BaseModel):
    build_around_card_names: list[str]
    power_level: str
    collection_preference: str


@router.post("/generate")
async def generate_deck(request: DeckGenerationRequest) -> dict:
    # TODO: retrieval.get_candidates -> generator.build_deck -> validator.validate
    raise NotImplementedError
