from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.schemas import DeckSummary
from database.session import get_session

router = APIRouter()


@router.get("/get_decks", response_model=list[DeckSummary])
async def get_decks(session: Session = Depends(get_session)) -> list[DeckSummary]:
    # TODO: retrieve decks from the database
    raise NotImplementedError
