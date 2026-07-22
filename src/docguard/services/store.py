from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from docguard.domain.models import AuditTask, TaskStatus


class TaskStore(Protocol):
    """Persistence boundary shared by the development and durable stores."""

    def create(self, task: AuditTask) -> AuditTask: ...

    def get(self, task_id: str) -> AuditTask: ...

    def list(self) -> list[AuditTask]: ...

    def update(
        self, task: AuditTask, status: TaskStatus | None = None, error: str | None = None
    ) -> AuditTask: ...


class InMemoryTaskStore:
    """Small test double for workflows that do not need durable state."""

    def __init__(self) -> None:
        self._tasks: dict[str, AuditTask] = {}

    def create(self, task: AuditTask) -> AuditTask:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> AuditTask:
        return self._tasks[task_id]

    def list(self) -> list[AuditTask]:
        return list(self._tasks.values())

    def update(self, task: AuditTask, status: TaskStatus | None = None, error: str | None = None) -> AuditTask:
        if status is not None:
            task.status = status
        task.error = error
        task.updated_at = datetime.now().astimezone()
        self._tasks[task.task_id] = task
        return task


class SQLiteTaskStore:
    """Durable local task store backed by the Python standard-library SQLite driver."""

    DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[3] / "data" / "docguard.sqlite3"

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_environment(cls) -> SQLiteTaskStore:
        return cls(os.getenv("DOCGUARD_DATABASE_PATH", str(cls.DEFAULT_DATABASE_PATH)))

    def create(self, task: AuditTask) -> AuditTask:
        payload = task.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_tasks (task_id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (task.task_id, task.created_at.isoformat(), task.updated_at.isoformat(), payload),
            )
        return task

    def get(self, task_id: str) -> AuditTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM audit_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return AuditTask.model_validate_json(row["payload"])

    def list(self) -> list[AuditTask]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM audit_tasks").fetchall()
        return [AuditTask.model_validate_json(row["payload"]) for row in rows]

    def update(
        self, task: AuditTask, status: TaskStatus | None = None, error: str | None = None
    ) -> AuditTask:
        if status is not None:
            task.status = status
        task.error = error
        task.updated_at = datetime.now().astimezone()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE audit_tasks
                SET updated_at = ?, payload = ?
                WHERE task_id = ?
                """,
                (task.updated_at.isoformat(), task.model_dump_json(), task.task_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(task.task_id)
        return task

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_tasks (
                    task_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tasks_created_at ON audit_tasks (created_at DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection
