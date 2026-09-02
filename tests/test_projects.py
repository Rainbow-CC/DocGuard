import sqlite3
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from docguard.api.app import app
from docguard.domain.models import CreateTaskRequest, InputDocument, ProjectCreateRequest
from docguard.services.projects import InMemoryProjectStore, ProjectConflictError, SQLiteProjectStore
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def test_project_store_exposes_default_and_creates_projects(tmp_path, provision_database) -> None:
    database_path = tmp_path / "docguard.sqlite3"
    provision_database(database_path)
    projects = SQLiteProjectStore(database_path)

    default = projects.get("default")
    created = projects.create(
        ProjectCreateRequest(
            project_id="payments-platform",
            name="支付平台",
            description="支付系统技术文档审核。",
            owner="架构组",
        )
    )

    assert default.name == "default"
    assert created.project_id == "payments-platform"
    assert [project.project_id for project in projects.list()] == ["default", "payments-platform"]
    with pytest.raises(ProjectConflictError, match="payments-platform"):
        projects.create(ProjectCreateRequest(project_id="payments-platform", name="重复项目"))


def test_new_audit_task_is_associated_with_selected_project(review_type_registry) -> None:
    projects = InMemoryProjectStore()
    projects.create(ProjectCreateRequest(project_id="payments-platform", name="支付平台"))
    service = AuditTaskService(InMemoryTaskStore(), review_type_registry, projects=projects)

    task = service.create(
        CreateTaskRequest(
            project_id="payments-platform",
            review_type_id="technical-architecture",
            document=InputDocument(
                filename="payment.docx",
                content_sha256=sha256(b"payment").hexdigest(),
                source_uri="file:///payment.docx",
            ),
        )
    )

    assert task.project_id == "payments-platform"
    with pytest.raises(KeyError, match="Unknown project"):
        service.create(
            CreateTaskRequest(
                project_id="missing-project",
                review_type_id="technical-architecture",
                document=InputDocument(
                    filename="missing.docx",
                    content_sha256=sha256(b"missing").hexdigest(),
                    source_uri="file:///missing.docx",
                ),
            )
        )


def test_operations_upgrade_backfills_legacy_tasks_to_default_project(tmp_path, provision_database) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE audit_tasks (
                task_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO audit_tasks (task_id, created_at, updated_at, payload)
            VALUES ('legacy-task', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '{}')
            """
        )

    provision_database(database_path)
    provision_database(database_path)

    with sqlite3.connect(database_path) as connection:
        project_id = connection.execute(
            "SELECT project_id FROM audit_tasks WHERE task_id = 'legacy-task'"
        ).fetchone()[0]
        default_project = connection.execute(
            "SELECT project_id FROM projects WHERE project_id = 'default'"
        ).fetchone()[0]
    assert project_id == "default"
    assert default_project == "default"


def test_project_api_lists_default_and_registers_a_project() -> None:
    client = TestClient(app)
    project_id = f"api-{uuid4().hex[:12]}"

    listed = client.get("/api/v1/projects")
    created = client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "API 项目", "owner": "测试"},
    )
    duplicate = client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "API 项目"},
    )

    assert listed.status_code == 200
    assert any(project["project_id"] == "default" for project in listed.json())
    assert created.status_code == 201
    assert created.json()["project_id"] == project_id
    assert duplicate.status_code == 409
