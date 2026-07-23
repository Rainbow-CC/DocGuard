import json
from hashlib import sha256
from pathlib import Path, PurePosixPath

from docguard.domain.models import AgentBackend, CreateTaskRequest, InputDocument, TaskStatus
from docguard.adapters.agents import GatewayExecutionError
from docguard.services.artifacts import ArtifactStore
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def _result(task, attempt) -> dict[str, object]:
    return {
        "schema_version": "docguard-agent-result-v1",
        "task_id": task.task_id,
        "attempt_id": attempt.attempt_id,
        "input_sha256": task.document.content_sha256,
        "profile_id": task.profile.profile_id,
        "profile_version": task.profile.version,
        "prompt_versions": task.profile.prompt_versions,
        "findings": [
            {
                "finding_id": "fd_example",
                "schema_version": "finding-v1",
                "rule_id": "DG-001",
                "category": "一致性",
                "review_dimension": "一致性与可读性",
                "judgment": "文本不一致",
                "severity": "一般",
                "confidence": 0.9,
                "title": "术语前后不一致",
                "text_evidence": ["第1章（概述），block:001：术语不一致"],
                "image_evidence": ["不适用（纯文本审核）"],
                "problem_description": "同一对象使用两个名称。",
                "impact": "影响评审理解。",
                "revision_suggestion": "统一术语。",
                "revision_location": "第 1 章",
                "completion_criteria": "全文仅保留一个术语。",
                "evidence_ids": ["txt_001"],
                "root_cause_key": "terminology:example",
                "agent_backend": "openclaw",
            }
        ],
    }


class CompletingGateway:
    def __init__(self, root: Path) -> None:
        self.root = root

    def execute_attempt(self, task, attempt) -> str:
        target = self.root / task.task_id / attempt.attempt_id / "findings.json"
        target.write_text(json.dumps(_result(task, attempt), ensure_ascii=False), encoding="utf-8")
        return "resp_example"


class DisconnectingGateway:
    def execute_attempt(self, task, attempt) -> str:
        raise GatewayExecutionError("OpenClaw transport failure: connection reset")

    def continue_attempt(self, task, attempt) -> str:
        assert attempt.gateway_response_id is None
        raise GatewayExecutionError("OpenClaw transport failure: connection reset")


class ContinuingGateway:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.continued = False

    def execute_attempt(self, task, attempt) -> str:
        raise GatewayExecutionError("OpenClaw transport failure: connection reset")

    def continue_attempt(self, task, attempt) -> str:
        self.continued = True
        target = self.root / task.task_id / attempt.attempt_id / "findings.json"
        target.write_text(json.dumps(_result(task, attempt), ensure_ascii=False), encoding="utf-8")
        return "resp_continued"


def test_openclaw_result_artifact_completes_task(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    service = AuditTaskService(
        InMemoryTaskStore(),
        ProfileRegistry(),
        artifacts=artifacts,
        openclaw_gateway=CompletingGateway(tmp_path),
    )
    task = service.create(
        CreateTaskRequest(
            document=InputDocument(
                filename="sample.docx",
                content_sha256=sha256(b"sample").hexdigest(),
                source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
            ),
            agent_backend=AgentBackend.OPENCLAW,
        )
    )

    completed = service.run(task.task_id)

    assert completed.status is TaskStatus.COMPLETED
    assert completed.attempts[0].gateway_response_id == "resp_example"
    manifest_path = tmp_path / completed.task_id / completed.attempts[0].attempt_id / "input-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "evidence" not in manifest
    assert "allowed_evidence_ids" not in manifest
    assert completed.findings[0].severity == "一般"
    assert "术语前后不一致" in (completed.report_markdown or "")


def test_sse_disconnect_keeps_task_collecting_for_artifact_reconciliation(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    service = AuditTaskService(
        InMemoryTaskStore(),
        ProfileRegistry(),
        artifacts=artifacts,
        openclaw_gateway=DisconnectingGateway(),
    )
    task = service.create(
        CreateTaskRequest(
            document=InputDocument(
                filename="sample.docx",
                content_sha256=sha256(b"sample").hexdigest(),
                source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
            ),
            agent_backend=AgentBackend.OPENCLAW,
        )
    )

    collecting = service.run(task.task_id)

    assert collecting.status is TaskStatus.COLLECTING
    assert collecting.attempts[0].error == "OpenClaw transport failure: connection reset"


def test_collecting_task_can_continue_in_its_existing_attempt(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    gateway = ContinuingGateway(tmp_path)
    service = AuditTaskService(InMemoryTaskStore(), ProfileRegistry(), artifacts=artifacts, openclaw_gateway=gateway)
    task = service.create(CreateTaskRequest(document=InputDocument(filename="sample.docx", content_sha256=sha256(b"sample").hexdigest(), source_uri="file:///docguard-inbox/reviewer/sample/source.docx"), agent_backend=AgentBackend.OPENCLAW))

    collecting = service.run(task.task_id)
    assert collecting.status is TaskStatus.COLLECTING
    completed = service.continue_collecting(task.task_id)

    assert gateway.continued is True
    assert completed.status is TaskStatus.COMPLETED
    assert len(completed.attempts) == 1
    assert completed.attempts[0].gateway_response_id == "resp_continued"
