from __future__ import annotations

from datetime import UTC, datetime

from docguard.domain.models import AuditTask, TaskStatus


class InMemoryTaskStore:
    """Development implementation; retain this interface when moving to PostgreSQL."""

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
        task.updated_at = datetime.now(UTC)
        self._tasks[task.task_id] = task
        return task
