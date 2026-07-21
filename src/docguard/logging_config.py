"""Application logging setup shared by the API and background task workflow."""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def configure_logging() -> None:
    """Emit DocGuard logs to the process console and a rotating UTF-8 log file."""
    logger = logging.getLogger("docguard")
    level_name = os.getenv("DOCGUARD_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    if logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_path = Path(os.getenv("DOCGUARD_LOG_FILE", "logs/docguard.log"))
    try:
        retention_days = int(os.getenv("DOCGUARD_LOG_RETENTION_DAYS", "14"))
    except ValueError:
        retention_days = 14
        logger.warning("Invalid DOCGUARD_LOG_RETENTION_DAYS; using 14 days")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=max(retention_days, 0),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Unable to enable file logging at %s: %s", log_path, exc)
    else:
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
