from fastapi import APIRouter

router = APIRouter()


@router.get("/search")
async def search_cards(q: str) -> list[dict]:
    # TODO: look up cards by name for the build-around card selection UI
    raise NotImplementedError
