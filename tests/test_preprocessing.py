from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
from subprocess import CompletedProcess

import pytest

from docguard.domain.models import AuditAttempt, CreateTaskRequest, InputDocument
from docguard.services.preprocessing import PreprocessingError, WslDocxPreprocessor
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def _task():
    service = AuditTaskService(InMemoryTaskStore(), ProfileRegistry())
    return service.create(
        CreateTaskRequest(
            document=InputDocument(
                filename="sample.docx",
                content_sha256=sha256(b"sample").hexdigest(),
                source_uri="file:///home/ubuntu/docguard-inbox/reviewer/sample/source.docx",
            )
        )
    )


def test_wsl_preprocessor_uses_linux_paths_and_runs_vision(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("docguard.services.preprocessing.subprocess.run", fake_run)
    task = _task()
    task.review_type.visual_policy = {"enabled": False}
    attempt = AuditAttempt(input_manifest_uri="pending", result_uri="pending", input_sha256=task.document.content_sha256)
    preprocessor = WslDocxPreprocessor(
        "/mnt/c/repo/doc-audit-integrate-skill",
        PurePosixPath("/home/ubuntu/docguard-results"),
    )

    preprocessor.prepare(task, attempt)

    assert captured["command"][:5] == ["wsl.exe", "--distribution", "Ubuntu", "--", "bash"]
    assert captured["environment"]["INPUT_DOCX"].startswith("/home/ubuntu/docguard-inbox/")
    assert captured["environment"]["ATTEMPT_DIR"].endswith(f"/{task.task_id}/{attempt.attempt_id}")
    assert "build_vision_prompt.py" in captured["command"][-1]


def test_wsl_preprocessor_surfaces_linux_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "docguard.services.preprocessing.subprocess.run",
        lambda command, **kwargs: CompletedProcess(command, 2, "", "missing soffice"),
    )
    task = _task()
    task.review_type.visual_policy = {"enabled": False}
    attempt = AuditAttempt(input_manifest_uri="pending", result_uri="pending", input_sha256=task.document.content_sha256)
    preprocessor = WslDocxPreprocessor("/skill", "/results")

    with pytest.raises(PreprocessingError, match="missing soffice"):
        preprocessor.prepare(task, attempt)

