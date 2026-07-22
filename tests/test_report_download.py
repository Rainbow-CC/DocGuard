from hashlib import sha256

from fastapi.testclient import TestClient

from docguard.api.app import app, store
from docguard.domain.models import AgentBackend, AuditTask, InputDocument
from docguard.services.profiles import ProfileRegistry


def _task(report_markdown: str | None) -> AuditTask:
    return AuditTask(
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
        ),
        profile=ProfileRegistry().get("technical-audit"),
        agent_backend=AgentBackend.OPENCLAW,
        report_markdown=report_markdown,
    )


def test_report_download_returns_markdown_attachment() -> None:
    task = store.create(_task("# 技术文档审核报告\n"))

    response = TestClient(app).get(f"/api/v1/tasks/{task.task_id}/report.md")

    assert response.status_code == 200
    assert response.text == "# 技术文档审核报告\n"
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="docguard-report-{task.task_id}.md"; '
        f"filename*=UTF-8''docguard-report-sample-{task.task_id}.md"
    )


def test_report_download_rejects_task_without_report() -> None:
    task = store.create(_task(None))

    response = TestClient(app).get(f"/api/v1/tasks/{task.task_id}/report.md")

    assert response.status_code == 409
    assert response.json() == {"detail": "Report is not available yet"}
