from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import LOG_DIR

LOG_FILE = LOG_DIR / "app.log"


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("epl_value_betting")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_500_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
