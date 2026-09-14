import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# config reads backend/.env itself, so no load_dotenv preamble is needed here
# and the knowledge-pipeline entry points get the same values this one does.
from config import APP_VERSION
from logging_config import configure_logging
from api.routes import cards, collection, deck_builder, deck_collection

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(root_path="/api/v1", title="Tome API", version=APP_VERSION)
logger.info("Tome API starting up")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()

app.include_router(collection.router, prefix="/collection", tags=["collection"])
app.include_router(cards.router, prefix="/cards", tags=["cards"])
app.include_router(deck_builder.router, prefix="/decks", tags=["decks"])
app.include_router(deck_collection.router, prefix="/decks", tags=["decks"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": APP_VERSION}
