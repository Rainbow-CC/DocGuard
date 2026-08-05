from __future__ import annotations

import logging
from datetime import datetime

from docguard.adapters.agents import (
    GatewayExecutionError,
    OpenClawAgentGateway,
    OpenClawAttemptGateway,
    graph_gateway_for,
)
from docguard.domain.models import (
    AgentBackend,
    AttemptStatus,
    AuditAttempt,
    AuditTask,
    CreateTaskRequest,
    TaskStatus,
)
from docguard.graph.audit_graph import build_audit_graph
from docguard.services.artifacts import ArtifactStore, ArtifactValidationError
from docguard.services.profiles import ReviewTypeRegistry
from docguard.services.preprocessing import AuditPreprocessor, PreprocessingError, WslDocxPreprocessor
from docguard.services.reporting import render_markdown
from docguard.services.store import TaskStore

_UNSET = object()
logger = logging.getLogger("docguard.tasks")


class AuditTaskService:
    def __init__(
        self,
        store: TaskStore,
        review_types: ReviewTypeRegistry,
        artifacts: ArtifactStore | None = None,
        openclaw_gateway: OpenClawAttemptGateway | None = None,
        preprocessor: AuditPreprocessor | None = None,
    ) -> None:
        self.store = store
        self.review_types = review_types
        self.artifacts = artifacts or ArtifactStore.from_environment()
        self.openclaw_gateway = openclaw_gateway or OpenClawAgentGateway()
        self.preprocessor = preprocessor or WslDocxPreprocessor.from_environment()

    def create(self, request: CreateTaskRequest) -> AuditTask:
        review_type = self.review_types.get(request.review_type_id)
        if not hasattr(review_type, "agent_backend"):
            raise KeyError(f"Registry returned no review type for {request.review_type_id}")
        backend = request.agent_backend or review_type.agent_backend
        task = AuditTask(
            document=request.document,
            profile=review_type.profile,
            review_type=review_type,
            agent_backend=backend,
        )
        created = self.store.create(task)
        logger.info(
            "task.created task_id=%s backend=%s review_type=%s@%s filename=%s",
            created.task_id,
            created.agent_backend.value,
            created.review_type.review_type_id,
            created.review_type.version,
            created.document.filename,
        )
        return created

    def run(self, task_id: str) -> AuditTask:
        task = self.store.get(task_id)
        logger.info("task.run.started task_id=%s backend=%s", task_id, task.agent_backend.value)
        self.store.update(task, status=TaskStatus.RUNNING)
        task.checkpoint_thread_id = task.task_id
        if task.agent_backend is AgentBackend.OPENCLAW:
            return self._run_openclaw(task)
        try:
            graph = build_audit_graph(graph_gateway_for(task.agent_backend))
            result = graph.invoke({"task": task}, {"configurable": {"thread_id": task.task_id}})
            task.findings = result["findings"]
            task.report_markdown = result["report_markdown"]
            completed = self.store.update(task, status=TaskStatus.COMPLETED)
            logger.info(
                "task.run.completed task_id=%s findings=%s", task_id, len(completed.findings)
            )
            return completed
        except Exception as exc:
            logger.exception("task.run.failed task_id=%s", task_id)
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))

    def collect(self, task_id: str, attempt_id: str | None = None) -> AuditTask:
        """Reconcile a durable agent result after normal completion or an SSE failure."""
        task = self.store.get(task_id)
        logger.info("task.collect.started task_id=%s attempt_id=%s", task_id, attempt_id)
        if task.status is TaskStatus.COMPLETED:
            logger.info("task.collect.skipped_completed task_id=%s", task_id)
            return task
        attempt = self._attempt(task, attempt_id)
        try:
            result = self.artifacts.read_result(task, attempt)
        except ArtifactValidationError as exc:
            logger.exception(
                "task.collect.invalid_artifact task_id=%s attempt_id=%s",
                task_id,
                attempt.attempt_id,
            )
            self._set_attempt_status(attempt, AttemptStatus.FAILED, str(exc))
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))
        if result is None:
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING)
            logger.info(
                "task.collect.waiting_for_artifact task_id=%s attempt_id=%s",
                task_id,
                attempt.attempt_id,
            )
            return self.store.update(task, status=TaskStatus.COLLECTING)

        # Findings remain atomic; root_cause_key is explanatory metadata only.
        # task.findings = list(merged.values())
        task.findings = result.findings
        task.report_markdown = render_markdown(
            task.profile, task.findings, self.artifacts.read_evidence(task, attempt)
        )
        self._set_attempt_status(attempt, AttemptStatus.COMPLETED, None)
        completed = self.store.update(task, status=TaskStatus.COMPLETED)
        logger.info(
            "task.collect.completed task_id=%s attempt_id=%s findings=%s",
            task_id,
            attempt.attempt_id,
            len(completed.findings),
        )
        return completed

    def collect_pending(self) -> list[AuditTask]:
        """Hook for a recurring worker; safe to call repeatedly."""
        reconciled: list[AuditTask] = []
        for task in self.store.list():
            if task.status is TaskStatus.COLLECTING and task.attempts:
                reconciled.append(self.collect(task.task_id))
        return reconciled

    def continue_collecting(self, task_id: str) -> AuditTask:
        """Prompt a disconnected OpenClaw task to continue in its existing session."""
        task = self.store.get(task_id)
        if task.status is not TaskStatus.COLLECTING:
            raise ValueError(f"Task {task_id} is not collecting")
        if task.agent_backend is not AgentBackend.OPENCLAW:
            raise ValueError(f"Task {task_id} does not use the OpenClaw backend")

        attempt = self._attempt(task, None)
        self._set_attempt_status(attempt, AttemptStatus.RUNNING, None)
        self.store.update(task, status=TaskStatus.RUNNING, error=None)
        logger.info("task.openclaw.continue_started task_id=%s attempt_id=%s", task_id, attempt.attempt_id)
        try:
            response_id = self.openclaw_gateway.continue_attempt(task, attempt)
            if response_id:
                attempt.gateway_response_id = response_id
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING)
        except GatewayExecutionError as exc:
            # Keep the task actionable: another continuation may still reach the agent.
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING, str(exc))
            logger.exception(
                "task.openclaw.continue_error task_id=%s attempt_id=%s error=%s",
                task_id,
                attempt.attempt_id,
                exc,
            )
        self.store.update(task, status=TaskStatus.COLLECTING, error=attempt.error)
        return self.collect(task_id, attempt.attempt_id)

    def _run_openclaw(self, task: AuditTask) -> AuditTask:
        logger.info("task.openclaw.prepare_started task_id=%s", task.task_id)
        attempt = self.artifacts.prepare(task)
        task.attempts.append(attempt)
        self._set_attempt_status(attempt, AttemptStatus.RUNNING)
        self.store.update(task, status=TaskStatus.RUNNING)
        try:
            self.preprocessor.prepare(task, attempt)
        except PreprocessingError as exc:
            self._set_attempt_status(attempt, AttemptStatus.FAILED, str(exc))
            logger.exception("task.preprocessing.failed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))
        try:
            attempt.gateway_response_id = self.openclaw_gateway.execute_attempt(task, attempt)
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING)
            logger.info(
                "task.openclaw.gateway_finished task_id=%s attempt_id=%s response_id=%s",
                task.task_id,
                attempt.attempt_id,
                attempt.gateway_response_id,
            )
        except GatewayExecutionError as exc:
            # A transport failure is deliberately non-terminal: the agent may have
            # finished writing its artifact after the SSE connection disappeared.
            self._set_attempt_status(attempt, AttemptStatus.COLLECTING, str(exc))
            logger.exception(
                "task.openclaw.gateway_error task_id=%s attempt_id=%s error=%s",
                task.task_id,
                attempt.attempt_id,
                exc,
            )
        # Persist SSE metadata before reconciliation reloads the task from a durable store.
        self.store.update(task, status=TaskStatus.COLLECTING, error=attempt.error)
        return self.collect(task.task_id, attempt.attempt_id)

    @staticmethod
    def _set_attempt_status(
        attempt: AuditAttempt, status: AttemptStatus, error: str | None | object = _UNSET
    ) -> None:
        attempt.status = status
        if error is not _UNSET:
            attempt.error = error
        attempt.updated_at = datetime.now().astimezone()

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
