"""SQLite connection helpers that never create a database implicitly."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_existing_database(database_path: Path | str, *, timeout: float) -> sqlite3.Connection:
    """Open an already-provisioned SQLite database in read/write mode."""

    path = Path(database_path).expanduser().resolve()
    try:
        return sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=timeout)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"DocGuard database is unavailable at {path}; provision it before starting the application."
        ) from exc
