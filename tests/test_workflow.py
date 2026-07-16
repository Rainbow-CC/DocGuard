from hashlib import sha256

from docguard.domain.models import CreateTaskRequest, InputDocument, TaskStatus
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def test_stub_task_completes_with_programmatic_report() -> None:
    digest = sha256(b"sample").hexdigest()
    service = AuditTaskService(InMemoryTaskStore(), ProfileRegistry())
    task = service.create(
        CreateTaskRequest(
            document=InputDocument(filename="sample.docx", content_sha256=digest, source_uri="file:///sample.docx")
        )
    )

    completed = service.run(task.task_id)

    assert completed.status is TaskStatus.COMPLETED
    assert completed.findings == []
    assert "技术文档审核报告" in (completed.report_markdown or "")
