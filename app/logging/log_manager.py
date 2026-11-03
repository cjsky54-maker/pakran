"""Rotating file logging for the whole application.

NOTE: this package is ``app.logging``; stdlib ``logging`` is imported via
absolute imports and is unaffected.
"""
from __future__ import annotations

import logging
import logging.handlers
import os

LOGGER_NAME = "DriverThermalQC"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3

_configured = False


def setup_logging(logs_dir: str, console: bool = False) -> logging.Logger:
    """Configure the application logger. Safe to call more than once."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured and logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    os.makedirs(logs_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(logs_dir, "app.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Namespaced child logger (e.g. get_logger('camera'))."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
