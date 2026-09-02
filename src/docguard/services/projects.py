"""Project persistence and lookup used to scope every document audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Protocol

from docguard.domain.models import (
    DEFAULT_PROJECT_ID,
    Project,
    ProjectCreateRequest,
    ProjectStatus,
)
from docguard.services.sqlite import connect_existing_database


class ProjectConflictError(ValueError):
    """Raised when a project identifier is already registered."""


class ProjectStore(Protocol):
    """Persistence boundary for the project association on audit tasks."""

    def create(self, request: ProjectCreateRequest) -> Project: ...

    def get(self, project_id: str) -> Project: ...

    def list(self, *, include_archived: bool = False) -> list[Project]: ...


def default_project() -> Project:
    """Return the reserved project used to preserve existing audit history."""

    return Project(
        project_id=DEFAULT_PROJECT_ID,
        name=DEFAULT_PROJECT_ID,
        description="历史审核记录及未指定项目的默认归属。",
    )


class InMemoryProjectStore:
    """Small test double that always provides the required default project."""

    def __init__(self, projects: Iterable[Project] = ()) -> None:
        self._projects: dict[str, Project] = {DEFAULT_PROJECT_ID: default_project()}
        for project in projects:
            self._projects[project.project_id] = project.model_copy(deep=True)

    def create(self, request: ProjectCreateRequest) -> Project:
        if request.project_id in self._projects:
            raise ProjectConflictError(f"Project {request.project_id} already exists")
        project = Project(**request.model_dump())
        self._projects[project.project_id] = project
        return project.model_copy(deep=True)

    def get(self, project_id: str) -> Project:
        try:
            return self._projects[project_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(f"Unknown project: {project_id}") from exc

    def list(self, *, include_archived: bool = False) -> list[Project]:
        projects = self._projects.values()
        if not include_archived:
            projects = (project for project in projects if project.status is ProjectStatus.ACTIVE)
        return [project.model_copy(deep=True) for project in projects]


class SQLiteProjectStore:
    """Durable project catalog backed by the provisioned SQLite database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def create(self, request: ProjectCreateRequest) -> Project:
        project = Project(**request.model_dump())
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects
                        (project_id, name, description, owner, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        project.name,
                        project.description,
                        project.owner,
                        project.status.value,
                        project.created_at.isoformat(),
                        project.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectConflictError(f"Project {project.project_id} already exists") from exc
        return project

    def get(self, project_id: str) -> Project:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, name, description, owner, status, created_at, updated_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown project: {project_id}")
        return Project.model_validate(dict(row))

    def list(self, *, include_archived: bool = False) -> list[Project]:
        query = """
            SELECT project_id, name, description, owner, status, created_at, updated_at
            FROM projects
        """
        parameters: tuple[str, ...] = ()
        if not include_archived:
            query += " WHERE status = ?"
            parameters = (ProjectStatus.ACTIVE.value,)
        query += " ORDER BY name COLLATE NOCASE, project_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Project.model_validate(dict(row)) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = connect_existing_database(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
