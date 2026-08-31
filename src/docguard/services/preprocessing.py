"""Application-owned DOCX preparation executed in the worker's Linux runtime."""

from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Protocol

from docguard.domain.models import AuditAttempt, AuditTask
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
        self.command = command or os.getenv("DOCGUARD_PREPROCESS_COMMAND", "wsl.exe")
        self.distribution = distribution or os.getenv("DOCGUARD_WSL_DISTRIBUTION", "Ubuntu")
        self.write_root = Path(write_root or os.getenv("DOCGUARD_RESULT_WRITE_ROOT", r"\\wsl.localhost\Ubuntu\home\ubuntu\docguard-results"))
        self.vision_adapter = vision_adapter or QwenVisionAdapter()
        self.vision_cache = vision_cache or VisionResponseCache.from_environment()

    @classmethod
    def from_environment(cls) -> "WslDocxPreprocessor":
        return cls(
            skill_root=os.getenv("DOCGUARD_SKILL_AGENT_ROOT", "/mnt/c/Code/fromGitHub/DocGuard/doc-audit-integrate-skill"),
            result_root=os.getenv("DOCGUARD_RESULT_AGENT_ROOT", "/home/ubuntu/docguard-results"),
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
            command.extend(
                [
                    "--distribution",
                    self.distribution,
                    "--",
                    "bash",
                    str(runner),
                    document,
                    str(attempt_dir),
                ]
            )
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


class BashDocxPreprocessor:
    """Runs skill Python scripts directly on macOS/Linux without WSL."""

    def __init__(
        self,
        skill_root: Path | str,
        result_root: Path | str,
        write_root: Path | str | None = None,
        vision_adapter: VisionAdapter | None = None,
        vision_cache: VisionResponseCache | None = None,
    ) -> None:
        self.skill_root = Path(skill_root)
        self.result_root = Path(result_root)
        self.write_root = Path(write_root or result_root)
        self.vision_adapter = vision_adapter or QwenVisionAdapter()
        self.vision_cache = vision_cache or VisionResponseCache.from_environment()

    @classmethod
    def from_environment(cls) -> "BashDocxPreprocessor":
        skill_root = os.getenv("DOCGUARD_SKILL_AGENT_ROOT", "")
        if not skill_root:
            project_root = Path(__file__).resolve().parents[3]
            skill_root = project_root / "doc-audit-integrate-skill"
        result_root = os.getenv("DOCGUARD_RESULT_AGENT_ROOT", os.path.expanduser("~/docguard-results"))
        return cls(skill_root=skill_root, result_root=result_root)

    def prepare(self, task: AuditTask, attempt: AuditAttempt) -> None:
        if task.review_type is None:
            raise PreprocessingError("Task has no frozen review type definition")
        document = task.document.source_uri.removeprefix("file://")
        attempt_dir = self.result_root / task.task_id / attempt.attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        work_dir = attempt_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        scripts = self.skill_root / "scripts"

        logger.info(
            "preprocessing.paths task_id=%s attempt_id=%s input_docx=%r skill_root=%r attempt_dir=%r",
            task.task_id,
            attempt.attempt_id,
            document,
            str(self.skill_root),
            str(attempt_dir),
        )

        # Step 1: extract_docx_structure (always creates a directory; JSON inside it)
        structure_dir = work_dir / "structure"
        self._run_python(
            scripts / "extract_docx_structure.py",
            str(document),
            "--output", str(structure_dir),
        )
        structure_file = structure_dir / "document-structure.json"

        # Step 2: build_audit_packet
        context_path = work_dir / "audit-context.md"
        evidence_path = work_dir / "audit-evidence.json"
        self._run_python(
            scripts / "build_audit_packet.py",
            str(structure_file),
            "--context-output", str(context_path),
            "--evidence-output", str(evidence_path),
        )

        # Step 3: build_vision_prompt
        template = self.skill_root / "references" / "vision-prompt-template.md"
        schema = self.skill_root / "references" / "architecture-facts.schema.json"
        prompt_path = work_dir / "vision-prompt.txt"
        if template.exists() and schema.exists():
            self._run_python(
                scripts / "build_vision_prompt.py",
                "--template", str(template),
                "--schema", str(schema),
                "--output", str(prompt_path),
            )

        if task.review_type.visual_policy.get("enabled"):
            self._understand_images(task, attempt)
        logger.info("preprocessing.completed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)

    def _run_python(self, script: Path, *args: str) -> None:
        logger.debug("preprocessing.script script=%s args=%r", script.name, args)
        try:
            completed = subprocess.run(
                ["python3", str(script), *args],
                text=True, capture_output=True, timeout=900,
            )
        except OSError as exc:
            raise PreprocessingError(f"Unable to start {script.name}: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip() or "no output"
            raise PreprocessingError(
                f"{script.name} failed (exit {completed.returncode}): {detail[-2000:]}"
            )

    def _understand_images(self, task: AuditTask, attempt: AuditAttempt) -> None:
        work = self.write_root / task.task_id / attempt.attempt_id / "work"
        prompt_path = work / "vision-prompt.txt"
        if not prompt_path.is_file():
            logger.warning(
                "vision.skipped_no_prompt task_id=%s attempt_id=%s",
                task.task_id,
                attempt.attempt_id,
            )
            return
        prompt = prompt_path.read_text(encoding="utf-8")
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
