"""Application logging setup.

Uvicorn configures only its own loggers; module loggers (``packages.*``) have no
handler and ``logger.info`` is swallowed. Configure the root logger once so the
contact-request audit trail is visible in ``docker compose logs api``.
"""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
