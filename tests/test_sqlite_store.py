from hashlib import sha256

from docguard.domain.models import AgentBackend, AuditTask, InputDocument, TaskStatus
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import SQLiteTaskStore


def test_sqlite_store_survives_reinitialization(tmp_path) -> None:
    database_path = tmp_path / "docguard.sqlite3"
    task = AuditTask(
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///docguard-inbox/sample.docx",
        ),
        profile=ProfileRegistry().get("technical-audit"),
        agent_backend=AgentBackend.STUB,
    )

    first_store = SQLiteTaskStore(database_path)
    first_store.create(task)
    first_store.update(task, status=TaskStatus.COMPLETED)

    restored = SQLiteTaskStore(database_path).get(task.task_id)

    assert restored.status is TaskStatus.COMPLETED
    assert restored.document.filename == "sample.docx"
    assert restored.task_id == task.task_id
