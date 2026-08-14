"""Application-owned DOCX preparation executed in the worker's Linux runtime."""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Protocol

from docguard.domain.models import AuditAttempt, AuditTask
from docguard.settings import Settings
from docguard.services.vision import QwenVisionAdapter, VisionAdapter, VisionResponseCache

logger = logging.getLogger("docguard.preprocessing")
MAX_VISION_IMAGES = 50
MAX_VISION_WORKERS = 10


class PreprocessingError(RuntimeError):
    pass


class AuditPreprocessor(Protocol):
    def prepare(self, task: AuditTask, attempt: AuditAttempt) -> None: ...


class NoopPreprocessor:
    """Test seam for artifact-only unit tests."""

    def prepare(self, task: AuditTask, attempt: AuditAttempt) -> None:
        return None


class WslDocxPreprocessor:
    """Runs the versioned skill scripts and vision CLI from the application.

    The web process can run on Windows while the tools and their intermediate
    files remain in WSL. In Linux deployments set ``DOCGUARD_PREPROCESS_COMMAND``
    to ``bash`` (or leave it unset) and use Linux roots for both sides.
    """

    def __init__(
        self,
        skill_root: PurePosixPath | str,
        result_root: PurePosixPath | str,
        command: str | None = None,
        distribution: str | None = None,
        write_root: Path | str | None = None,
        vision_adapter: VisionAdapter | None = None,
        vision_cache: VisionResponseCache | None = None,
    ) -> None:
        self.skill_root = PurePosixPath(skill_root)
        self.result_root = PurePosixPath(result_root)
        settings = Settings.from_environment()
        self.command = command or settings.preprocess_command
        self.distribution = distribution or settings.wsl_distribution or ""
        self.write_root = Path(write_root or settings.result_write_root)
        self.vision_adapter = vision_adapter or QwenVisionAdapter()
        self.vision_cache = vision_cache or VisionResponseCache.from_environment()

    @classmethod
    def from_environment(cls) -> "WslDocxPreprocessor":
        settings = Settings.from_environment()
        return cls(
            skill_root=settings.skill_agent_root,
            result_root=settings.result_agent_root,
            command=settings.preprocess_command,
            distribution=settings.wsl_distribution,
            write_root=settings.result_write_root,
        )

    def prepare(self, task: AuditTask, attempt: AuditAttempt) -> None:
        if task.review_type is None:
            raise PreprocessingError("Task has no frozen review type definition")
        document = task.document.source_uri.removeprefix("file://")
        attempt_dir = self.result_root / task.task_id / attempt.attempt_id
        runner = self.skill_root / "scripts" / "preprocess_attempt.sh"
        command = [self.command]
        if Path(self.command).name.lower() == "wsl.exe":
            # Do not rely on Windows-to-WSL environment propagation.
            if self.distribution:
                command.extend(["--distribution", self.distribution])
            command.extend(["--", "bash", str(runner), document, str(attempt_dir)])
        else:
            command.extend([str(runner), document, str(attempt_dir)])
        logger.info(
            "preprocessing.paths task_id=%s attempt_id=%s input_docx=%r skill_root=%r attempt_dir=%r",
            task.task_id,
            attempt.attempt_id,
            document,
            str(self.skill_root),
            str(attempt_dir),
        )
        logger.debug("preprocessing.command task_id=%s attempt_id=%s command=%r", task.task_id, attempt.attempt_id, command)
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PreprocessingError(f"Unable to start DOCX preprocessor: {exc}") from exc
        if completed.returncode:
            logger.error(
                "preprocessing.command_failed task_id=%s attempt_id=%s returncode=%s stdout=%r stderr=%r",
                task.task_id,
                attempt.attempt_id,
                completed.returncode,
                completed.stdout,
                completed.stderr,
            )
            detail = (completed.stderr or completed.stdout).strip() or "no process output"
            raise PreprocessingError(
                f"DOCX preprocessing failed (exit {completed.returncode}): {detail[-2000:]}"
            )
        if task.review_type.visual_policy.get("enabled"):
            self._understand_images(task, attempt)
        logger.info("preprocessing.completed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)

    def _understand_images(self, task: AuditTask, attempt: AuditAttempt) -> None:
        work = self.write_root / task.task_id / attempt.attempt_id / "work"
        prompt = (work / "vision-prompt.txt").read_text(encoding="utf-8")
        rendered = work / "extracted" / "rendered"
        raw_dir = work / "vision-responses"
        images = sorted(rendered.glob("*.png"))
        if len(images) > MAX_VISION_IMAGES:
            raise PreprocessingError(
                f"Visual review has {len(images)} images; maximum is {MAX_VISION_IMAGES}. No model calls were made."
            )
        raw_dir.mkdir(exist_ok=True)

        def understand(image: Path) -> None:
            raw = raw_dir / f"{image.stem}.raw.txt"
            try:
                response, cached = self.vision_cache.get_or_create(image.read_bytes(), prompt, self.vision_adapter)
                raw.write_text(response.raw_response, encoding="utf-8")
                logger.info("vision.completed task_id=%s image=%s cache_hit=%s", task.task_id, image.stem, cached)
            except Exception as exc:
                raw.write_text("图片理解失败\n", encoding="utf-8")
                logger.warning("vision.failed task_id=%s image=%s error=%s", task.task_id, image.stem, exc)

        logger.info(
            "vision.batch_started task_id=%s images=%s max_workers=%s",
            task.task_id,
            len(images),
            MAX_VISION_WORKERS,
        )
        with ThreadPoolExecutor(max_workers=MAX_VISION_WORKERS, thread_name_prefix="docguard-vision") as executor:
            futures = [executor.submit(understand, image) for image in images]
            for future in as_completed(futures):
                future.result()
