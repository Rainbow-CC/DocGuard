from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile, status

from docguard.domain.models import CreateTaskRequest, TaskCreatedResponse, UploadDocumentResponse
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService
from docguard.services.uploads import UploadStorage, UploadTooLargeError, UploadValidationError


store = InMemoryTaskStore()
service = AuditTaskService(store, ProfileRegistry())
app = FastAPI(title="DocGuard", version="0.1.0")
app.state.upload_storage = UploadStorage.from_environment()


def get_upload_storage(request: Request) -> UploadStorage:
    return request.app.state.upload_storage


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


@app.post(
    "/api/v1/agents/{agent_id}/uploads",
    response_model=UploadDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    agent_id: str,
    file: Annotated[UploadFile, File(description="DOCX document to make available to the agent")],
    upload_storage: Annotated[UploadStorage, Depends(get_upload_storage)],
) -> UploadDocumentResponse:
    try:
        stored = await upload_storage.store_docx(agent_id, file)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Unable to persist upload") from exc
    finally:
        await file.close()

    return UploadDocumentResponse(
        upload_id=stored.upload_id,
        agent_id=stored.agent_id,
        filename=stored.filename,
        size_bytes=stored.size_bytes,
        content_sha256=stored.sha256,
        agent_path=stored.agent_path,
        source_uri=stored.source_uri,
    )


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str):
    try:
        return store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
