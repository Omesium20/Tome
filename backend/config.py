"""App-wide configuration, loaded once from the environment and `backend/.env`.

Every setting the backend reads lives here. Import the singleton rather than
touching `os.environ` directly:

    from config import get_settings
    settings = get_settings()
    engine = create_engine(settings.database_url)

Note on history: commit a21fc64 removed pydantic-settings on the grounds that
nothing imported it and python-dotenv + os.environ was enough. That reasoning
held while `api/main.py` was the only entry point — it called `load_dotenv()`
above all other imports, so the module-level `os.environ` reads downstream saw
a populated environment.

The knowledge pipeline breaks that assumption. `python -m knowledge_pipeline.*`
runs with no such bootstrap, so those module-level reads fell back to their
defaults and the importer would silently write to sqlite instead of the
configured Postgres. Config loading now belongs to a module both entry points
import, which is what pydantic-settings is for; validation and typed access
come along for free.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"

BACKEND_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
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

    # --- persistence -----------------------------------------------------
    database_url: str = "sqlite:///./tome.db"

    # --- AI / retrieval --------------------------------------------------
    model_api_key: str = ""
    chroma_persist_dir: Path = Path("./chroma_data")

    # --- logging ---------------------------------------------------------
    log_level: str = "WARNING"

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


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings singleton.

    Cached so that repeated calls are free and every caller observes the same
    values even if the environment is mutated after startup.
    """
    return Settings()
