from fastapi import BackgroundTasks, FastAPI, HTTPException, status

from docguard.domain.models import CreateTaskRequest, TaskCreatedResponse
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


store = InMemoryTaskStore()
service = AuditTaskService(store, ProfileRegistry())
app = FastAPI(title="DocGuard", version="0.1.0")


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/tasks", response_model=TaskCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_task(request: CreateTaskRequest, background_tasks: BackgroundTasks) -> TaskCreatedResponse:
    try:
        task = service.create(request)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(service.run, task.task_id)
    return TaskCreatedResponse(
        task_id=task.task_id,
        status=task.status,
        status_url=f"/api/v1/tasks/{task.task_id}",
    )


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str):
    try:
        return store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
