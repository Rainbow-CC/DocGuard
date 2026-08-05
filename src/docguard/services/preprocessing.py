"""Application-owned DOCX preparation executed in the worker's Linux runtime."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Protocol

from docguard.domain.models import AuditAttempt, AuditTask
from docguard.services.vision import QwenVisionAdapter, VisionAdapter, VisionResponseCache

logger = logging.getLogger("docguard.preprocessing")


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
        # A quoted, fixed script avoids Windows/WSL path guessing. Dynamic values
        # are passed through environment variables, never interpolated into Bash.
        script = r'''set -euo pipefail
test -s "$INPUT_DOCX"
test -d "$SKILL_ROOT"
WORK="$ATTEMPT_DIR/work"
mkdir -p "$WORK/vision-responses" "$WORK/vision-facts" "$ATTEMPT_DIR/evidence"
python3 "$SKILL_ROOT/scripts/extract_docx_structure.py" "$INPUT_DOCX" --output "$WORK/extracted" --render-png --revision-mode accept
python3 "$SKILL_ROOT/scripts/build_audit_packet.py" "$WORK/extracted/document-structure.json" --context-output "$WORK/audit-context.md" --evidence-output "$WORK/audit-evidence.json"
python3 "$SKILL_ROOT/scripts/build_vision_prompt.py" --template "$SKILL_ROOT/review-packs/technical-architecture/vision-prompt.md" --schema "$SKILL_ROOT/review-packs/technical-architecture/vision-facts.schema.json" --output "$WORK/vision-prompt.txt"
cp "$WORK/audit-evidence.json" "$ATTEMPT_DIR/evidence/audit-evidence.json"
cp -a "$WORK/extracted/rendered" "$ATTEMPT_DIR/evidence/rendered"
'''
        environment = os.environ | {
            "INPUT_DOCX": document,
            "SKILL_ROOT": str(self.skill_root),
            "ATTEMPT_DIR": str(attempt_dir),
        }
        command = [self.command]
        if Path(self.command).name.lower() == "wsl.exe":
            command.extend(["--distribution", self.distribution, "--", "bash", "-lc", script])
        else:
            command.extend(["-lc", script])
        try:
            completed = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PreprocessingError(f"Unable to start DOCX preprocessor: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise PreprocessingError(f"DOCX preprocessing failed: {detail[-2000:]}")
        if task.review_type.visual_policy.get("enabled"):
            self._understand_images(task, attempt)
        logger.info("preprocessing.completed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)

    def _understand_images(self, task: AuditTask, attempt: AuditAttempt) -> None:
        work = self.write_root / task.task_id / attempt.attempt_id / "work"
        prompt = (work / "vision-prompt.txt").read_text(encoding="utf-8")
        rendered = work / "extracted" / "rendered"
        raw_dir, facts_dir = work / "vision-responses", work / "vision-facts"
        raw_dir.mkdir(exist_ok=True)
        facts_dir.mkdir(exist_ok=True)
        schema = self.skill_root / "review-packs" / "technical-architecture" / "vision-facts.schema.json"
        validator = self.skill_root / "scripts" / "validate_vision_response.py"
        for image in rendered.glob("*.png"):
            raw = raw_dir / f"{image.stem}.raw.txt"
            try:
                response, cached = self.vision_cache.get_or_create(image.read_bytes(), prompt, self.vision_adapter)
                raw.write_text(response.raw_response, encoding="utf-8")
                completed = subprocess.run(["python", str(validator), "--raw", str(raw), "--schema", str(schema), "--output", str(facts_dir / f"{image.stem}.json"), "--error-output", str(facts_dir / f"{image.stem}.error.txt")], text=True, capture_output=True, timeout=30)
                logger.info("vision.completed task_id=%s image=%s cache_hit=%s valid=%s", task.task_id, image.stem, cached, completed.returncode == 0)
            except Exception as exc:
                raw.write_text("图片理解失败\n", encoding="utf-8")
                logger.warning("vision.failed task_id=%s image=%s error=%s", task.task_id, image.stem, exc)
