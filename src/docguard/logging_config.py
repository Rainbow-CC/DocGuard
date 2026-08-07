"""Application logging setup shared by the API and background task workflow."""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class ExcludeTaskListAccessLogFilter(logging.Filter):
    """Suppress the dashboard's high-frequency task-list polling access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep all access logs except GET requests for the task collection endpoint."""
        if record.name != "uvicorn.access" or len(record.args) < 3:
            return True

        method = record.args[1]
        path = record.args[2]
        return not (
            method == "GET"
            and isinstance(path, str)
            and path.partition("?")[0] == "/api/v1/tasks"
        )


def _configure_access_log_filters() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(log_filter, ExcludeTaskListAccessLogFilter) for log_filter in access_logger.filters):
        access_logger.addFilter(ExcludeTaskListAccessLogFilter())


def configure_logging() -> None:
    """Emit DocGuard logs to the process console and a rotating UTF-8 log file."""
    _configure_access_log_filters()
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
