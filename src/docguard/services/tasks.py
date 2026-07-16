from __future__ import annotations

from docguard.adapters.agents import gateway_for
from docguard.domain.models import AuditTask, CreateTaskRequest, TaskStatus
from docguard.graph.audit_graph import build_audit_graph
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore


class AuditTaskService:
    def __init__(self, store: InMemoryTaskStore, profiles: ProfileRegistry) -> None:
        self.store = store
        self.profiles = profiles

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
        try:
            graph = build_audit_graph(gateway_for(task.agent_backend))
            result = graph.invoke({"task": task}, {"configurable": {"thread_id": task.task_id}})
            task.findings = result["findings"]
            task.report_markdown = result["report_markdown"]
            return self.store.update(task, status=TaskStatus.COMPLETED)
        except Exception as exc:
            return self.store.update(task, status=TaskStatus.FAILED, error=str(exc))
