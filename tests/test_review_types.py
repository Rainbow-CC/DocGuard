from hashlib import sha256
import sqlite3

from docguard.domain.models import AgentBackend, CreateTaskRequest, InputDocument
from docguard.services.profiles import DEFAULT_TECHNICAL_REVIEW_TYPE, ReviewTypeRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def test_review_type_registry_versions_definitions_and_task_freezes_snapshot(tmp_path) -> None:
    registry = ReviewTypeRegistry(tmp_path / "docguard.sqlite3")
    definition = DEFAULT_TECHNICAL_REVIEW_TYPE.model_copy(deep=True)
    definition.review_type_id = "overview-design"
    definition.version = "2.0.0"
    definition.display_name = "概要设计审核"
    definition.skill_ref = "docx-overview-design-audit"
    definition.rule_pack_ref = "overview-design/review-rules.md"
    definition.visual_policy = {"enabled": False}
    definition.agents[0].version = "1.0.1"
    definition.agents[0].agent_backend = AgentBackend.STUB
    registry.register(definition)

    service = AuditTaskService(InMemoryTaskStore(), registry)
    task = service.create(
        CreateTaskRequest(
            review_type_id="overview-design",
            document=InputDocument(
                filename="overview.docx",
                content_sha256=sha256(b"overview").hexdigest(),
                source_uri="file:///overview.docx",
            ),
        )
    )

    assert [item.review_type_id for item in registry.list()] == ["overview-design", "technical-architecture"]
    assert task.review_type is not None
    assert task.review_type.skill_ref == "docx-overview-design-audit"
    assert task.review_type.rule_pack_ref == "overview-design/review-rules.md"
    assert task.agent_backend is AgentBackend.STUB


def test_agents_are_registered_once_and_can_be_reused_by_review_types(tmp_path) -> None:
    database_path = tmp_path / "docguard.sqlite3"
    registry = ReviewTypeRegistry(database_path)
    shared_agent = DEFAULT_TECHNICAL_REVIEW_TYPE.agents[0]

    second = DEFAULT_TECHNICAL_REVIEW_TYPE.model_copy(deep=True)
    second.review_type_id = "overview-design"
    second.version = "1.0.0"
    second.display_name = "概要设计审核"
    second.agents = [shared_agent]
    registry.register(second)

    with sqlite3.connect(database_path) as connection:
        agent_count = connection.execute(
            "SELECT COUNT(*) FROM agent_definitions WHERE agent_id = ? AND version = ?",
            (shared_agent.agent_id, shared_agent.version),
        ).fetchone()[0]
        assignments = connection.execute(
            "SELECT COUNT(*) FROM review_type_agent_definitions"
        ).fetchone()[0]

    assert agent_count == 1
    assert assignments == 2
    assert registry.get("overview-design").agents == [shared_agent]


def test_registry_migrates_legacy_embedded_agent_definitions(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    legacy_definition = DEFAULT_TECHNICAL_REVIEW_TYPE.model_copy(deep=True)
    legacy_definition.review_type_id = "legacy-review"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE review_type_definitions (
                review_type_id TEXT NOT NULL,
                version TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                definition TEXT NOT NULL,
                PRIMARY KEY (review_type_id, version)
            )
            """
        )
        connection.execute(
            "INSERT INTO review_type_definitions VALUES (?, ?, 1, ?)",
            ("legacy-review", "1.0.0", legacy_definition.model_dump_json()),
        )

    registry = ReviewTypeRegistry(database_path)

    assert registry.get("legacy-review").agents == DEFAULT_TECHNICAL_REVIEW_TYPE.agents
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_definitions").fetchone()[0] == 1
