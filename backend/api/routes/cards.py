from fastapi import APIRouter

from api.schemas import CardSummary

router = APIRouter()


@router.get("/search", response_model=list[CardSummary])
async def search_cards(q: str) -> list[CardSummary]:
    # TODO: look up cards by name for the build-around card selection UI, by
    # calling the Knowledge API (KNOWLEDGE_API_URL) -- not by querying a local
    # table. Cards live in the knowledge plane; the local database holds only
    # this user's collection and decks (docs/architecture.md). Deliberately
    # takes no local session, so this can't be implemented the wrong way by
    # reaching for one that's already injected.
    raise NotImplementedError
