from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.schemas import CardSummary
from database.session import get_session

router = APIRouter()


@router.get("/search", response_model=list[CardSummary])
async def search_cards(q: str, session: Session = Depends(get_session)) -> list[CardSummary]:
    # TODO: look up cards by name for the build-around card selection UI
    raise NotImplementedError
