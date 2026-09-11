import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from docguard.domain.models import CreateTaskRequest, InputDocument
from docguard.services.profiles import ReviewTypeRegistry
from docguard.services.store import SQLiteTaskStore
from docguard.services.vision import VisionResponseCache


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INIT_SCRIPT = PROJECT_ROOT / "init" / "apply_sql.py"


def _provision(database_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INIT_SCRIPT), "--database-path", str(database_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_operations_init_script_provisions_schema_and_seed_data(tmp_path) -> None:
    database_path = tmp_path / "docguard.sqlite3"

    first = _provision(database_path)
    second = _provision(database_path)

    assert "Provisioned" in first.stdout
    assert "Provisioned" in second.stdout
    registry = ReviewTypeRegistry(database_path)
    assert [definition.review_type_id for definition in registry.list()] == ["technical-architecture"]
    assert registry.get("technical-architecture").agents[0].agent_id == "content-reviewer"

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        review_type_count = connection.execute("SELECT COUNT(*) FROM review_type_definitions").fetchone()[0]
        agent_count = connection.execute("SELECT COUNT(*) FROM agent_definitions").fetchone()[0]
        agent_definition = json.loads(
            connection.execute("SELECT definition FROM agent_definitions").fetchone()[0]
        )
        routes = connection.execute(
            "SELECT route_id, is_default FROM agent_routes ORDER BY route_id"
        ).fetchall()
    assert {
        "review_type_definitions",
        "agent_definitions",
        "agent_routes",
        "audit_tasks",
        "vision_response_cache",
    } <= tables
    assert review_type_count == 1
    assert agent_count == 1
    assert "agent_backend" not in agent_definition
    assert "agent_model_ref" not in agent_definition
    assert routes == [("technical-audit/content-reviewer", 1)]


def test_application_does_not_create_a_missing_database(tmp_path) -> None:
    database_path = tmp_path / "not-provisioned.sqlite3"

    with pytest.raises(RuntimeError, match="database is unavailable"):
        ReviewTypeRegistry(database_path)

    assert not database_path.exists()


def test_task_store_and_vision_cache_do_not_create_a_missing_database(tmp_path) -> None:
    database_path = tmp_path / "not-provisioned.sqlite3"

    with pytest.raises(RuntimeError, match="database is unavailable"):
        SQLiteTaskStore(database_path).list()

    class VisionAdapter:
        adapter_id = "test"
        model = "test-v1"

        def describe(self, image: bytes, prompt: str, *, media_type: str = "image/png"):
            raise AssertionError("The database should be read before the adapter is called")

    with pytest.raises(RuntimeError, match="database is unavailable"):
        VisionResponseCache(database_path).get_or_create(b"image", "prompt", VisionAdapter())

    assert not database_path.exists()


def test_task_request_requires_an_explicit_review_type() -> None:
    with pytest.raises(ValidationError, match="review_type_id"):
        CreateTaskRequest(
            document=InputDocument(
                filename="sample.docx",
                content_sha256="a" * 64,
                source_uri="file:///sample.docx",
            )
        )
