from hashlib import sha256

from docguard.domain.models import AgentBackend, CreateTaskRequest, InputDocument, TaskStatus
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def test_stub_task_completes_with_programmatic_report(review_type_registry) -> None:
    digest = sha256(b"sample").hexdigest()
    service = AuditTaskService(InMemoryTaskStore(), review_type_registry)
    task = service.create(
        CreateTaskRequest(
            review_type_id="technical-architecture",
            document=InputDocument(filename="sample.docx", content_sha256=digest, source_uri="file:///sample.docx"),
            agent_backend=AgentBackend.STUB,
        )
    )

    completed = service.run(task.task_id)

    assert completed.status is TaskStatus.COMPLETED
    assert completed.findings == []
    assert "技术文档审核报告" in (completed.report_markdown or "")
