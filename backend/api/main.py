from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import cards, collection, decks

app = FastAPI(title="Tome API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(collection.router, prefix="/api/collection", tags=["collection"])
app.include_router(cards.router, prefix="/api/cards", tags=["cards"])
app.include_router(decks.router, prefix="/api/decks", tags=["decks"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
