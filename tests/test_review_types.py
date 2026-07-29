from hashlib import sha256

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
            agent_backend=AgentBackend.STUB,
        )
    )

    assert [item.review_type_id for item in registry.list()] == ["overview-design", "technical-architecture"]
    assert task.review_type is not None
    assert task.review_type.skill_ref == "docx-overview-design-audit"
    assert task.review_type.rule_pack_ref == "overview-design/review-rules.md"
