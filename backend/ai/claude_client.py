import logging

# Raw Anthropic SDK for now — CLAUDE.md's stack table designates LangChain for
# retrieval/prompt construction/output parsing once the Deck Generation
# Pipeline (still stubs) is built. This client is just the bare API handle
# those LangChain components will wrap.
from anthropic import Anthropic

from config import get_settings

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = get_settings().model_api_key
        if not api_key:
            raise RuntimeError(
                "MODEL_API_KEY is not set. Copy backend/.env.example to backend/.env "
                "and add your Anthropic API key."
            )
        logger.info("Initializing Anthropic client")
        _client = Anthropic(api_key=api_key)
    return _client
