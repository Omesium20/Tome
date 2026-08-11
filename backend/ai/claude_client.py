import logging
import os

from anthropic import Anthropic

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        logger.info("Initializing Anthropic client")
        _client = Anthropic(api_key=os.environ["MODEL_API_KEY"])
    return _client
