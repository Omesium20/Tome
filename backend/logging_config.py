"""App-wide logging setup.

Call configure_logging() once, at process startup, before any module-level
`logging.getLogger(__name__)` calls emit anything. Everywhere else in the
backend, just do:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("...")

Handlers/formatting/level are controlled centrally here rather than per
module, and messages propagate up to the root logger configured below.
"""

import logging
import os
import sys

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Third-party libraries default to chatty INFO/DEBUG logging; keep them
    # quiet unless the app itself is explicitly running at DEBUG.
    if level > logging.DEBUG:
        for noisy_logger in ("httpx", "chromadb", "sentence_transformers"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _CONFIGURED = True
