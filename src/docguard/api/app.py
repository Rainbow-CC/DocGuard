import logging
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from docguard.domain.models import CreateTaskRequest, TaskCreatedResponse, UploadDocumentResponse
from docguard.logging_config import configure_logging
from docguard.services.profiles import ProfileRegistry
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService
from docguard.services.uploads import UploadStorage, UploadTooLargeError, UploadValidationError


configure_logging()
logger = logging.getLogger("docguard.api")

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
        logger.warning("task.create.rejected profile_id=%s error=%s", request.profile_id, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(service.run, task.task_id)
    logger.info("task.queued task_id=%s backend=%s", task.task_id, task.agent_backend.value)
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
        logger.warning("upload.rejected_too_large agent_id=%s error=%s", agent_id, exc)
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except UploadValidationError as exc:
        logger.warning("upload.rejected agent_id=%s error=%s", agent_id, exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("upload.persistence_failed agent_id=%s", agent_id)
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


@app.get("/api/v1/tasks/{task_id}/report.md")
def download_task_report(task_id: str) -> Response:
    """Download the deterministic Markdown report for a completed task."""
    try:
        task = store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    if task.report_markdown is None:
        raise HTTPException(status_code=409, detail="Report is not available yet")

    filename = f"docguard-report-{task.task_id}.md"
    logger.info("task.report.downloaded task_id=%s filename=%s", task.task_id, filename)
    return Response(
        content=task.report_markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/v1/tasks/{task_id}/collect")
def collect_task(task_id: str):
    """Internal worker hook to reconcile an artifact after an SSE interruption."""
    logger.info("task.collect.requested task_id=%s", task_id)
    try:
        return service.collect(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
