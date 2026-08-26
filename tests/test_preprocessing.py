from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
from subprocess import CompletedProcess
from threading import Lock
from time import sleep

import pytest

from docguard.domain.models import AuditAttempt, CreateTaskRequest, InputDocument
from docguard.services.preprocessing import PreprocessingError, WslDocxPreprocessor
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService
from docguard.services.vision import VisionResponse, VisionResponseCache


def _task(review_type_registry):
    service = AuditTaskService(InMemoryTaskStore(), review_type_registry)
    return service.create(
        CreateTaskRequest(
            review_type_id="technical-architecture",
            document=InputDocument(
                filename="sample.docx",
                content_sha256=sha256(b"sample").hexdigest(),
                source_uri="file:///home/ubuntu/docguard-inbox/reviewer/sample/source.docx",
            )
        )
    )


def test_wsl_preprocessor_uses_linux_paths_and_runs_vision(monkeypatch, review_type_registry) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("docguard.services.preprocessing.subprocess.run", fake_run)
    task = _task(review_type_registry)
    task.review_type.visual_policy = {"enabled": False}
    attempt = AuditAttempt(input_manifest_uri="pending", result_uri="pending", input_sha256=task.document.content_sha256)
    preprocessor = WslDocxPreprocessor(
        "/mnt/c/repo/doc-audit-integrate-skill",
        PurePosixPath("/home/ubuntu/docguard-results"),
    )

    preprocessor.prepare(task, attempt)

    assert captured["command"][:5] == ["wsl.exe", "--distribution", "Ubuntu", "--", "bash"]
    assert captured["command"][5].endswith("/scripts/preprocess_attempt.sh")
    assert captured["command"][6] == "/home/ubuntu/docguard-inbox/reviewer/sample/source.docx"
    assert captured["command"][7] == f"/home/ubuntu/docguard-results/{task.task_id}/{attempt.attempt_id}"


def test_wsl_preprocessor_surfaces_linux_failure(monkeypatch, review_type_registry) -> None:
    monkeypatch.setattr(
        "docguard.services.preprocessing.subprocess.run",
        lambda command, **kwargs: CompletedProcess(command, 2, "", "missing soffice"),
    )
    task = _task(review_type_registry)
    task.review_type.visual_policy = {"enabled": False}
    attempt = AuditAttempt(input_manifest_uri="pending", result_uri="pending", input_sha256=task.document.content_sha256)
    preprocessor = WslDocxPreprocessor("/skill", "/results")

    with pytest.raises(PreprocessingError, match="missing soffice"):
        preprocessor.prepare(task, attempt)


class CountingVisionAdapter:
    adapter_id = "test"
    model = "test-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = Lock()

    def describe(self, image: bytes, prompt: str, *, media_type: str = "image/png") -> VisionResponse:
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        sleep(0.02)
        with self.lock:
            self.active -= 1
        return VisionResponse(self.adapter_id, self.model, '{"content":"response"}')


def _vision_work(tmp_path, task, attempt, images: int):
    work = tmp_path / task.task_id / attempt.attempt_id / "work"
    rendered = work / "extracted" / "rendered"
    rendered.mkdir(parents=True)
    (work / "vision-prompt.txt").write_text("prompt", encoding="utf-8")
    for index in range(images):
        (rendered / f"image-{index}.png").write_bytes(f"image-{index}".encode())


def test_vision_batch_limits_concurrency_to_ten(
    tmp_path, provision_database, review_type_registry
) -> None:
    task, attempt = _task(review_type_registry), AuditAttempt(
        input_manifest_uri="pending", result_uri="pending", input_sha256="a" * 64
    )
    adapter = CountingVisionAdapter()
    _vision_work(tmp_path, task, attempt, 11)
    cache_database_path = tmp_path / "cache.sqlite3"
    provision_database(cache_database_path)
    preprocessor = WslDocxPreprocessor(
        "/skill",
        "/results",
        write_root=tmp_path,
        vision_adapter=adapter,
        vision_cache=VisionResponseCache(cache_database_path),
    )

    preprocessor._understand_images(task, attempt)

    assert adapter.calls == 11
    assert 1 < adapter.max_active <= 10


def test_vision_batch_rejects_more_than_fifty_images_without_calls(
    tmp_path, provision_database, review_type_registry
) -> None:
    task, attempt = _task(review_type_registry), AuditAttempt(
        input_manifest_uri="pending", result_uri="pending", input_sha256="a" * 64
    )
    adapter = CountingVisionAdapter()
    _vision_work(tmp_path, task, attempt, 51)
    cache_database_path = tmp_path / "cache.sqlite3"
    provision_database(cache_database_path)
    preprocessor = WslDocxPreprocessor(
        "/skill",
        "/results",
        write_root=tmp_path,
        vision_adapter=adapter,
        vision_cache=VisionResponseCache(cache_database_path),
    )

    with pytest.raises(PreprocessingError, match="maximum is 50"):
        preprocessor._understand_images(task, attempt)
    assert adapter.calls == 0
