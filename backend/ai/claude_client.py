import logging

# Raw Anthropic SDK. This is a bare API handle, and a temporary one: the
# target shape is the `ModelProvider` interface in `docs/model-providers.md`,
# with Anthropic as one implementation among local and OpenAI-compatible ones.
from anthropic import Anthropic

from config import get_local_settings

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = get_local_settings().model_api_key
        if not api_key:
            raise RuntimeError(
                "MODEL_API_KEY is not set. Add it to backend/.env. It is a "
                "client-plane setting: model calls happen on the user's machine."
            )
        logger.info("Initializing Anthropic client")
        _client = Anthropic(api_key=api_key)
    return _client
