from hashlib import sha256

from fastapi.testclient import TestClient

from docguard.api import app as api
from docguard.domain.models import AgentBackend, AuditTask, InputDocument
from docguard.services.store import InMemoryTaskStore


def _task(report_markdown: str | None, technical_review_type) -> AuditTask:
    return AuditTask(
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
        ),
        profile=technical_review_type.profile,
        agent_backend=AgentBackend.OPENCLAW,
        report_markdown=report_markdown,
    )


def test_report_download_returns_pdf_attachment(monkeypatch, technical_review_type) -> None:
    store = InMemoryTaskStore()
    monkeypatch.setattr(api, "store", store)
    task = store.create(_task("# 技术文档审核报告\n", technical_review_type))

    response = TestClient(api.app).get(f"/api/v1/tasks/{task.task_id}/report.pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="docguard-report-{task.task_id}.pdf"; '
        f"filename*=UTF-8''docguard-report-sample-{task.task_id}.pdf"
    )


def test_report_download_rejects_task_without_report(monkeypatch, technical_review_type) -> None:
    store = InMemoryTaskStore()
    monkeypatch.setattr(api, "store", store)
    task = store.create(_task(None, technical_review_type))

    response = TestClient(api.app).get(f"/api/v1/tasks/{task.task_id}/report.pdf")

    assert response.status_code == 409
    assert response.json() == {"detail": "Report is not available yet"}
