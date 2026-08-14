from __future__ import annotations

import logging
import subprocess
from pathlib import Path, PurePosixPath

from docguard.domain.models import AgentBackend, AuditTask
from docguard.settings import Settings
from docguard.services.artifacts import ArtifactStore


logger = logging.getLogger("docguard.action_chains")


class ActionChainUnavailableError(ValueError):
    """The task cannot provide a downloadable OpenClaw trajectory."""


class OpenClawActionChainExporter:
    """Export and cache a development-only, full OpenClaw session trajectory."""

    def __init__(self, artifacts: ArtifactStore, *, enabled: bool | None = None) -> None:
        self.artifacts = artifacts
        settings = Settings.from_environment()
        self.enabled = enabled if enabled is not None else settings.action_chain_export_enabled
        self.wsl_distribution = settings.wsl_distribution or ""
        self.tools_root = Path(__file__).resolve().parents[3] / "tools"

    def download_path(self, task: AuditTask) -> Path:
        """Return cached Markdown, or export the latest task session and create it."""
        if not self.enabled:
            raise ActionChainUnavailableError("行动链导出功能未启用")
        if task.agent_backend is not AgentBackend.OPENCLAW:
            raise ActionChainUnavailableError("只有 OpenClaw 任务可导出行动链")
        if not task.attempts:
            raise ActionChainUnavailableError("任务尚未创建 OpenClaw 尝试")

        attempt = task.attempts[-1]
        output_dir = self.artifacts.write_root / task.task_id / attempt.attempt_id / "action-chain" / task.task_id
        output_path = output_dir / "output.md"
        if output_path.is_file():
            return output_path

        linux_output_root = (
            PurePosixPath(attempt.result_uri.removeprefix("file://")).parent / "action-chain"
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        export_script = self._linux_path(self.tools_root / "export-latest-openclaw-session.sh")
        extract_script = self._linux_path(self.tools_root / "extract_tool_calls.py")
        self._run_wsl(
            [
                "env",
                f"DOCGUARD_TRAJECTORY_EXPORT_ROOT={linux_output_root}",
                "bash",
                export_script,
                task.task_id,
            ],
            timeout=120,
        )
        self._run_wsl(["python3", extract_script, str(linux_output_root / task.task_id)], timeout=60)
        if not output_path.is_file():
            raise ActionChainUnavailableError("OpenClaw 导出完成，但未生成行动链 Markdown")
        logger.info("action_chain.exported task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
        return output_path

    def _linux_path(self, path: Path) -> str:
        # ``wsl.exe`` consumes backslashes in a Windows path passed as an argv item.
        # Forward slashes preserve the drive-qualified path for ``wslpath``.
        result = self._run_wsl(["wslpath", "-a", path.as_posix()], timeout=10)
        return result.stdout.strip()

    def _run_wsl(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        invocation = ["wsl.exe"]
        if self.wsl_distribution:
            invocation.extend(["--distribution", self.wsl_distribution])
        try:
            return subprocess.run(
                [*invocation, "--", *command],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip() or (exc.stdout or "").strip() or str(exc)
            raise ActionChainUnavailableError(f"无法导出 OpenClaw 行动链：{detail}") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActionChainUnavailableError(f"无法导出 OpenClaw 行动链：{exc}") from exc
