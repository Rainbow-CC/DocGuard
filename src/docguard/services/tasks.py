from __future__ import annotations

from datetime import UTC, datetime

from docguard.adapters.agents import GatewayExecutionError, OpenClawAgentGateway, gateway_for
from docguard.domain.models import (
    AgentBackend,
    AttemptStatus,
    AuditAttempt,
    AuditTask,
    CreateTaskRequest,
    TaskStatus,
)
from docguard.graph.audit_graph import build_audit_graph, build_evidence
from docguard.services.artifacts import ArtifactStore, ArtifactValidationError
from docguard.services.profiles import ProfileRegistry
from docguard.services.reporting import render_markdown
from docguard.services.store import InMemoryTaskStore

_UNSET = object()


class AuditTaskService:
    def __init__(
        self,
        store: InMemoryTaskStore,
        profiles: ProfileRegistry,
        artifacts: ArtifactStore | None = None,
        openclaw_gateway: OpenClawAgentGateway | None = None,
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.artifacts = artifacts or ArtifactStore.from_environment()
        self.openclaw_gateway = openclaw_gateway or OpenClawAgentGateway()

    def create(self, request: CreateTaskRequest) -> AuditTask:
        task = AuditTask(
            document=request.document,
            profile=self.profiles.get(request.profile_id),
            agent_backend=request.agent_backend,
        )
        return self.store.create(task)

    def run(self, task_id: str) -> AuditTask:
        task = self.store.get(task_id)
        self.store.update(task, status=TaskStatus.RUNNING)
        task.checkpoint_thread_id = task.task_id
        if task.agent_backend is AgentBackend.OPENCLAW:
            return self._run_openclaw(task)
        try:
            graph = build_audit_graph(gateway_for(task.agent_backend))
            result = graph.invoke({"task": task}, {"configurable": {"thread_id": task.task_id}})
            task.findings = result["findings"]
            task.report_markdown = result["report_markdown"]
            return self.store.update(task, status=TaskStatus.COMPLETED)
        except Exception as exc:
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))

    def collect(self, task_id: str, attempt_id: str | None = None) -> AuditTask:
        """Reconcile a durable agent result after normal completion or an SSE failure."""
        task = self.store.get(task_id)
        if task.status is TaskStatus.COMPLETED:
            return task
        attempt = self._attempt(task, attempt_id)
        evidence = build_evidence(task)
        try:
            result = self.artifacts.read_result(task, attempt, evidence)
        except ArtifactValidationError as exc:
            self._set_attempt_status(attempt, AttemptStatus.FAILED, str(exc))
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))
        if result is None:
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING)
            return self.store.update(task, status=TaskStatus.COLLECTING)

        # The application, not the agent, performs deterministic de-duplication and rendering.
        merged = {}
        for finding in result.findings:
            merged.setdefault(finding.root_cause_key, finding)
        task.findings = list(merged.values())
        task.report_markdown = render_markdown(task.profile, task.findings)
        self._set_attempt_status(attempt, AttemptStatus.COMPLETED, None)
        return self.store.update(task, status=TaskStatus.COMPLETED)

    def collect_pending(self) -> list[AuditTask]:
        """Hook for a recurring worker; safe to call repeatedly."""
        reconciled: list[AuditTask] = []
        for task in self.store.list():
            if task.status is TaskStatus.COLLECTING and task.attempts:
                reconciled.append(self.collect(task.task_id))
        return reconciled

    def _run_openclaw(self, task: AuditTask) -> AuditTask:
        evidence = build_evidence(task)
        attempt = self.artifacts.prepare(task, evidence)
        task.attempts.append(attempt)
        self._set_attempt_status(attempt, AttemptStatus.RUNNING)
        self.store.update(task, status=TaskStatus.RUNNING)
        try:
            attempt.gateway_response_id = self.openclaw_gateway.execute_attempt(task, attempt)
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING)
        except GatewayExecutionError as exc:
            # A transport failure is deliberately non-terminal: the agent may have
            # finished writing its artifact after the SSE connection disappeared.
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING, str(exc))
        return self.collect(task.task_id, attempt.attempt_id)

    @staticmethod
    def _set_attempt_status(
        attempt: AuditAttempt, status: AttemptStatus, error: str | None | object = _UNSET
    ) -> None:
        attempt.status = status
        if error is not _UNSET:
            attempt.error = error
        attempt.updated_at = datetime.now(UTC)

    @staticmethod
    def _attempt(task: AuditTask, attempt_id: str | None) -> AuditAttempt:
        if not task.attempts:
            raise ValueError(f"Task {task.task_id} has no OpenClaw attempt")
        if attempt_id is None:
            return task.attempts[-1]
        for attempt in task.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise ValueError(f"Unknown attempt {attempt_id}")
