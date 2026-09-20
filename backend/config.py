"""App-wide configuration, loaded once from the environment and `backend/.env`.

Tome runs as two planes (`docs/architecture.md`), and they do not share a
database. Settings are therefore split the same way, and there is deliberately
no combined accessor:

    from config import get_knowledge_settings   # cloud corpus: maintainers
    from config import get_local_settings       # the user's own machine

Note on history: commit a21fc64 removed pydantic-settings on the grounds that
nothing imported it and python-dotenv + os.environ was enough. That reasoning
held while `api/main.py` was the only entry point — it called `load_dotenv()`
above all other imports, so the module-level `os.environ` reads downstream saw
a populated environment.

The knowledge pipeline broke that assumption. `python -m knowledge_pipeline.*`
runs with no such bootstrap, so those module-level reads fell back to their
defaults and the importer would silently write to sqlite instead of the
configured Postgres. Config loading moved here, which is what pydantic-settings
is for; validation and typed access came along for free.

The plane split is the same lesson applied once more. A single `DATABASE_URL`
read through a single `get_settings()` meant the caller never had to say which
database it wanted, and the default quietly answered for it — that is how the
importer ended up able to write card data into a user's collection database.
Each plane now has its own required setting and its own accessor, so the
question has to be answered at every call site.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"

BACKEND_ROOT = Path(__file__).resolve().parent


class BaseAppSettings(BaseSettings):
    """Settings common to both planes.

    Deliberately almost empty. Anything plane-specific belongs on one of the
    subclasses — the point of the split is that a client process cannot read a
    knowledge-plane setting and vice versa, and every field hoisted up here
    erodes that.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # pydantic reserves the `model_` prefix for its own config and warns on
        # any field that starts with it. MODEL_API_KEY is the name already used
        # in .env and docker-compose, so clear the protected namespace instead
        # of renaming the setting.
        protected_namespaces=(),
    )

    log_level: str = "WARNING"

    # Not a setting — a tripwire. `DATABASE_URL` was the single pre-split
    # database setting, and `extra="ignore"` would otherwise let a stale one
    # sit in .env while the process silently used a default. Which database a
    # leftover value meant is unknowable, so refuse rather than guess.
    database_url: str | None = None

    @model_validator(mode="after")
    def _reject_the_pre_split_database_url(self) -> "BaseAppSettings":
        if self.database_url is not None:
            raise ValueError(
                "DATABASE_URL is no longer used: Tome has two databases and this "
                "setting cannot say which one you meant. Replace it with "
                "KNOWLEDGE_DATABASE_URL (the shared card corpus, maintainers only) "
                "and/or LOCAL_DATABASE_URL (your own collection and decks). "
                "See docs/self-hosting.md#configuration."
            )
        return self


class KnowledgeSettings(BaseAppSettings):
    """The hosted knowledge plane: the shared card corpus and the pipeline.

    Only maintainers — or someone running their own knowledge plane — need any
    of this. A normal client install reads the corpus over HTTPS through the
    Knowledge API and never constructs these settings.
    """

    # --- persistence -----------------------------------------------------
    # Required, with no default on purpose. A default here would be a default
    # answer to "which database?", and the wrong answer writes user-visible
    # card data from a laptop.
    knowledge_database_url: str

    # --- Scryfall importer -----------------------------------------------
    # Scryfall requires a User-Agent that identifies the application rather
    # than a default library string, and an explicit Accept header.
    # See https://scryfall.com/docs/api (Required Headers).
    scryfall_user_agent: str = f"Tome/{APP_VERSION} (+https://github.com/harrisspeed/Tome)"
    scryfall_api_base: str = "https://api.scryfall.com"
    # Which bulk file to pull. `oracle_cards` is one object per Oracle ID,
    # which matches our normalized Card model — see docs/knowledge-pipeline.md.
    scryfall_bulk_type: str = "oracle_cards"
    # Downloaded bulk archives land here. Already gitignored (`data/scryfall/`).
    scryfall_cache_dir: Path = BACKEND_ROOT / "data" / "scryfall"
    # Rows per INSERT ... ON CONFLICT batch during import.
    import_batch_size: int = 1000


class LocalSettings(BaseAppSettings):
    """The client plane: what runs on the user's own machine."""

    # --- persistence -----------------------------------------------------
    # The user's collection and decks. Never leaves the machine. SQLite is the
    # zero-setup default; any SQLAlchemy URL works.
    local_database_url: str = "sqlite:///./tome.db"

    # --- the knowledge plane, as a dependency ----------------------------
    # Where card data comes from. A connection string is deliberately not an
    # option here — the client talks to the corpus over HTTP only
    # (docs/knowledge-api.md). Defaults to a locally-run knowledge service
    # until the hosted one exists.
    knowledge_api_url: str = "http://localhost:8001"

    # --- model provider --------------------------------------------------
    model_api_key: str = ""


@lru_cache
def get_base_settings() -> BaseAppSettings:
    """Only the settings both planes share.

    For code that runs in either deployable and needs neither database —
    logging setup, essentially. Using a plane's settings there would make a
    client process require KNOWLEDGE_DATABASE_URL just to configure a log
    handler.
    """
    return BaseAppSettings()


@lru_cache
def get_knowledge_settings() -> KnowledgeSettings:
    """Knowledge-plane settings. Raises if `KNOWLEDGE_DATABASE_URL` is unset."""
    return KnowledgeSettings()


@lru_cache
def get_local_settings() -> LocalSettings:
    """Client-plane settings."""
    return LocalSettings()
