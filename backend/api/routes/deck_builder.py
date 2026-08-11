from fastapi import APIRouter
from api.schemas import DeckGenerationRequest

from deck_pipeline import generator, retrieval, validator

router = APIRouter()


@router.post("/generate")
async def generate_deck(request: DeckGenerationRequest) -> dict:
    # TODO: retrieval.get_candidates -> generator.build_deck -> validator.validate
    raise NotImplementedError
