from __future__ import annotations

import json
import threading
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from fastapi.testclient import TestClient

from docguard.adapters import agents
from docguard.adapters.agents import DshAgentGateway, GatewayExecutionError
from docguard.api import app as api
from docguard.domain.models import (
    AgentBackend,
    AgentRun,
    AuditAgentDefinition,
    AuditAttempt,
    AuditProfile,
    AuditTask,
    CreateTaskRequest,
    InputDocument,
    ReviewTypeDefinition,
    TaskStatus,
)
from docguard.services.artifacts import ArtifactStore
from docguard.services.preprocessing import NoopPreprocessor
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def _register_dsh_review_type(review_type_registry) -> str:
    definition = review_type_registry.get("technical-architecture")
    definition.review_type_id = "dsh-multi-agent"
    definition.version = "1.0.0"
    definition.display_name = "DSH multi-agent audit"
    definition.agents = [
        AuditAgentDefinition(
            agent_id="dsh-content-reviewer",
            version="1.0.0",
            dimension="content",
            agent_backend=AgentBackend.DSH,
            agent_model_ref="dsh/audit-runtime",
            skill_ref="docx-tech-architecture-audit",
            rule_pack_ref="technical-architecture/review-rules.md",
            rule_pack_version="1.0.0",
        ),
        AuditAgentDefinition(
            agent_id="dsh-architecture-reviewer",
            version="1.0.0",
            dimension="architecture",
            agent_backend=AgentBackend.DSH,
            agent_model_ref="dsh/audit-runtime",
            skill_ref="docx-tech-architecture-audit",
            rule_pack_ref="technical-architecture/review-rules.md",
            rule_pack_version="1.0.0",
        ),
    ]
    review_type_registry.register(definition)
    return definition.review_type_id


def _result(task, attempt, run) -> dict[str, object]:
    return {
        "schema_version": "docguard-agent-result-v1",
        "task_id": task.task_id,
        "attempt_id": attempt.attempt_id,
        "input_sha256": task.document.content_sha256,
        "profile_id": task.profile.profile_id,
        "profile_version": task.profile.version,
        "prompt_versions": task.profile.prompt_versions,
        "review_type_id": task.review_type.review_type_id,
        "review_type_version": task.review_type.version,
        "core_contract_version": task.review_type.core_contract_version,
        "dimension": run.agent.dimension,
        "scope": run.agent.scope,
        "producer_agent_id": run.agent.agent_id,
        "producer_agent_version": run.agent.version,
        "producer_model_ref": run.agent.agent_model_ref,
        "findings": [
            {
                "finding_id": f"fd-{run.agent.artifact_stem}",
                "schema_version": "finding-v1",
                "rule_id": "DG-001",
                "category": "一致性",
                "review_dimension": "一致性与可读性",
                "judgment": "文本不一致",
                "severity": "一般",
                "confidence": 0.9,
                "title": "术语前后不一致",
                "text_evidence": ["第1章：术语不一致"],
                "image_evidence": ["不适用（纯文本审核）"],
                "problem_description": "同一对象使用两个名称。",
                "impact": "影响评审理解。",
                "revision_suggestion": "统一术语。",
                "revision_location": "第 1 章",
                "completion_criteria": "全文仅保留一个术语。",
                "evidence_ids": ["txt_001"],
                "root_cause_key": f"terminology:{run.agent.artifact_stem}",
                "agent_backend": (run.execution_backend or task.agent_backend).value,
            }
        ],
    }


def _write_result(root: Path, task, attempt, run) -> None:
    target = root / task.task_id / attempt.attempt_id / "findings" / f"{run.agent.artifact_stem}.findings.json"
    target.write_text(json.dumps(_result(task, attempt, run), ensure_ascii=False), encoding="utf-8")


class ConcurrentDshGateway:
    def __init__(self, root: Path, expected_agents: int) -> None:
        self.root = root
        self.expected_agents = expected_agents
        self._lock = threading.Lock()
        self._all_started = threading.Event()
        self._active = 0
        self.max_active = 0

    def execute_attempt(self, task, attempt, run) -> None:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            if self._active == self.expected_agents:
                self._all_started.set()
        try:
            if not self._all_started.wait(timeout=1):
                raise GatewayExecutionError("DSH specialists were not dispatched concurrently")
            _write_result(self.root, task, attempt, run)
        finally:
            with self._lock:
                self._active -= 1

    def continue_attempt(self, task, attempt, run) -> None:
        raise AssertionError("continuation is not expected in this test")


class RecoveringDshGateway:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.continued_agent_ids: list[str] = []

    def execute_attempt(self, task, attempt, run) -> None:
        raise GatewayExecutionError("DSH transport failure: connection reset")

    def continue_attempt(self, task, attempt, run) -> None:
        self.continued_agent_ids.append(run.agent.agent_id)
        _write_result(self.root, task, attempt, run)


def _create_dsh_task(service: AuditTaskService, review_type_id: str):
    return service.create(
        CreateTaskRequest(
            review_type_id=review_type_id,
            document=InputDocument(
                filename="sample.docx",
                content_sha256=sha256(b"sample").hexdigest(),
                source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
            ),
            agent_backend=AgentBackend.DSH,
        )
    )


def test_dsh_runs_specialists_concurrently_and_accepts_dsh_artifacts(
    tmp_path: Path, review_type_registry
) -> None:
    review_type_id = _register_dsh_review_type(review_type_registry)
    gateway = ConcurrentDshGateway(tmp_path, expected_agents=2)
    service = AuditTaskService(
        InMemoryTaskStore(),
        review_type_registry,
        artifacts=ArtifactStore(tmp_path, PurePosixPath("/docguard-results")),
        dsh_gateway=gateway,
        preprocessor=NoopPreprocessor(),
    )
    task = _create_dsh_task(service, review_type_id)

    completed = service.run(task.task_id)

    assert completed.status is TaskStatus.COMPLETED
    assert gateway.max_active == 2
    attempt = completed.attempts[0]
    assert {run.execution_backend for run in attempt.agent_runs} == {AgentBackend.DSH}
    assert {run.gateway_session_id for run in attempt.agent_runs} == {
        f"docguard:task:{completed.task_id}:attempt:{attempt.attempt_id}:agent:{run.agent.agent_id}"
        for run in attempt.agent_runs
    }
    assert len({run.workspace_path for run in attempt.agent_runs}) == 2
    assert all(Path(run.workspace_path).is_dir() for run in attempt.agent_runs)

    manifest_path = tmp_path / completed.task_id / attempt.attempt_id / "input-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {item["execution_backend"] for item in manifest["agent_runs"]} == {"dsh"}


def test_dsh_collecting_attempt_can_continue_every_incomplete_specialist(
    tmp_path: Path, review_type_registry
) -> None:
    review_type_id = _register_dsh_review_type(review_type_registry)
    gateway = RecoveringDshGateway(tmp_path)
    service = AuditTaskService(
        InMemoryTaskStore(),
        review_type_registry,
        artifacts=ArtifactStore(tmp_path, PurePosixPath("/docguard-results")),
        dsh_gateway=gateway,
        preprocessor=NoopPreprocessor(),
    )
    task = _create_dsh_task(service, review_type_id)

    collecting = service.run(task.task_id)
    assert collecting.status is TaskStatus.COLLECTING
    completed = service.continue_collecting(task.task_id)

    assert completed.status is TaskStatus.COMPLETED
    assert set(gateway.continued_agent_ids) == {
        "dsh-content-reviewer",
        "dsh-architecture-reviewer",
    }
    assert completed.attempts[0].error is None


def test_continue_endpoint_accepts_a_collecting_dsh_task(
    tmp_path: Path, review_type_registry, monkeypatch
) -> None:
    review_type_id = _register_dsh_review_type(review_type_registry)
    store = InMemoryTaskStore()
    service = AuditTaskService(
        store,
        review_type_registry,
        artifacts=ArtifactStore(tmp_path, PurePosixPath("/docguard-results")),
        dsh_gateway=RecoveringDshGateway(tmp_path),
        preprocessor=NoopPreprocessor(),
    )
    task = _create_dsh_task(service, review_type_id)
    assert service.run(task.task_id).status is TaskStatus.COLLECTING
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "service", service)

    response = TestClient(api.app).post(f"/api/v1/tasks/{task.task_id}/continue")

    assert response.status_code == 200
    assert store.get(task.task_id).status is TaskStatus.COMPLETED


def test_dsh_gateway_uses_the_run_workspace_and_does_not_return_completion_text(
    tmp_path: Path, monkeypatch
) -> None:
    profile = AuditProfile(
        profile_id="technical-audit",
        version="1.0.0",
        required_nodes=[],
        report_template="default",
        evidence_policy="accepted_revision_only",
        prompt_versions={},
    )
    agent = AuditAgentDefinition(
        agent_id="dsh-content-reviewer",
        version="1.0.0",
        dimension="content",
        agent_backend=AgentBackend.DSH,
        agent_model_ref="dsh/audit-runtime",
        skill_ref="docx-tech-architecture-audit",
        rule_pack_ref="technical-architecture/review-rules.md",
        rule_pack_version="1.0.0",
    )
    review_type = ReviewTypeDefinition(
        review_type_id="dsh-audit",
        version="1.0.0",
        display_name="DSH audit",
        description="Audit",
        skill_ref=agent.skill_ref,
        core_contract_version=1,
        rule_pack_ref=agent.rule_pack_ref,
        rule_pack_version=agent.rule_pack_version,
        profile=profile,
        agents=[agent],
    )
    task = AuditTask(
        task_id="task-example",
        document=InputDocument(
            filename="sample.docx",
            content_sha256="a" * 64,
            source_uri="file:///docguard-inbox/sample.docx",
        ),
        profile=profile,
        review_type=review_type,
        agent_backend=AgentBackend.DSH,
    )
    attempt = AuditAttempt(
        attempt_id="attempt-example",
        input_manifest_uri="file:///docguard-results/task-example/attempt-example/input-manifest.json",
        result_uri="file:///docguard-results/task-example/attempt-example/findings",
        input_sha256=task.document.content_sha256,
    )
    run = AgentRun(
        agent=agent,
        result_uri="file:///docguard-results/task-example/attempt-example/findings/content.findings.json",
        execution_backend=AgentBackend.DSH,
        gateway_session_id="session-example",
        workspace_path=str(tmp_path),
    )
    captured: dict[str, object] = {}

    class RecordingHarness:
        def __init__(self, **kwargs) -> None:
            captured["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def run(self, prompt: str, *, session_id: str):
            captured["prompt"] = prompt
            captured["session_id"] = session_id
            return SimpleNamespace(final_response="completed, but not a response id")

    monkeypatch.setattr(agents, "DeepSeekHarness", RecordingHarness)

    result = DshAgentGateway(dsh_home=str(tmp_path)).execute_attempt(task, attempt, run)

    assert result is None
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["session_id"] == "session-example"
    assert "DOCGUARD_AGENT_BACKEND=dsh" in captured["prompt"]
    assert "DOCGUARD_EVIDENCE_DIR=/docguard-results/task-example/attempt-example/evidence" in captured["prompt"]
    assert "DOCGUARD_WORK_DIR=/docguard-results/task-example/attempt-example/work" in captured["prompt"]
