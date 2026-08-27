from __future__ import annotations

import logging
import subprocess
from pathlib import Path, PurePosixPath

from docguard.domain.models import AgentBackend, AgentRun, AuditAttempt, AuditTask
from docguard.settings import Settings
from docguard.services.artifacts import ArtifactStore


logger = logging.getLogger("docguard.action_chains")


class ActionChainUnavailableError(ValueError):
    """The task cannot provide a downloadable OpenClaw trajectory."""


class OpenClawActionChainExporter:
    """Export and cache development-only OpenClaw session trajectories."""

    def __init__(self, artifacts: ArtifactStore, *, enabled: bool | None = None) -> None:
        self.artifacts = artifacts
        settings = Settings.from_environment()
        self.enabled = enabled if enabled is not None else settings.action_chain_export_enabled
        self.wsl_distribution = settings.wsl_distribution or ""
        self.tools_root = Path(__file__).resolve().parents[3] / "tools"

    def download_path(self, task: AuditTask) -> Path:
        """Return a task-level Markdown export for every specialist session.

        New OpenClaw tasks have one session per ``AgentRun``.  Each specialist
        therefore receives an isolated trajectory export before the individual
        Markdown files are assembled into the downloadable task-level document.
        """
        if not self.enabled:
            raise ActionChainUnavailableError("行动链导出功能未启用")
        if task.agent_backend is not AgentBackend.OPENCLAW:
            raise ActionChainUnavailableError("只有 OpenClaw 任务可导出行动链")
        if not task.attempts:
            raise ActionChainUnavailableError("任务尚未创建 OpenClaw 尝试")

        attempt = task.attempts[-1]
        output_dir = self.artifacts.write_root / task.task_id / attempt.attempt_id / "action-chain" / task.task_id
        output_path = output_dir / "output.md"
        if self._is_cached(output_dir, output_path, attempt):
            return output_path

        output_dir.mkdir(parents=True, exist_ok=True)
        export_script = self._linux_path(self.tools_root / "export-latest-openclaw-session.sh")
        extract_script = self._linux_path(self.tools_root / "extract_tool_calls.py")

        # Read pre-platform tasks with their former single-session export layout.
        # Newly created OpenClaw attempts always have AgentRun records.
        if not attempt.agent_runs:
            self._export_legacy_task(task, attempt, export_script, extract_script)
            if not output_path.is_file():
                raise ActionChainUnavailableError("OpenClaw 导出完成，但未生成行动链 Markdown")
            logger.info("action_chain.exported_legacy task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
            return output_path

        available: list[tuple[AgentRun, Path]] = []
        failures: list[tuple[AgentRun, str]] = []
        for run in attempt.agent_runs:
            agent_dir = self._agent_output_dir(output_dir, run)
            agent_output = agent_dir / "output.md"
            if not agent_output.is_file():
                try:
                    self._export_agent_run(task, attempt, run, agent_dir, export_script, extract_script)
                except ActionChainUnavailableError as exc:
                    failures.append((run, str(exc)))
                    logger.warning(
                        "action_chain.agent_export_failed task_id=%s attempt_id=%s agent_id=%s error=%s",
                        task.task_id,
                        attempt.attempt_id,
                        run.agent.agent_id,
                        exc,
                    )
            if agent_output.is_file():
                available.append((run, agent_output))
                failures = [(failed_run, error) for failed_run, error in failures if failed_run is not run]
            elif not any(failed_run is run for failed_run, _ in failures):
                failures.append((run, "OpenClaw 导出完成，但未生成该 Agent 的行动链 Markdown"))

        if not available:
            details = "；".join(f"{run.agent.agent_id}: {error}" for run, error in failures)
            raise ActionChainUnavailableError(f"未能导出任何 Agent 行动链：{details}")

        self._write_task_output(task, attempt, output_dir, output_path, available, failures)
        logger.info(
            "action_chain.exported task_id=%s attempt_id=%s agents=%s unavailable_agents=%s",
            task.task_id,
            attempt.attempt_id,
            len(available),
            len(failures),
        )
        return output_path

    @staticmethod
    def _agent_output_dir(output_dir: Path, run: AgentRun) -> Path:
        return output_dir / "agents" / run.agent.agent_id

    def _is_cached(self, output_dir: Path, output_path: Path, attempt: AuditAttempt) -> bool:
        if not output_path.is_file():
            return False
        return not attempt.agent_runs or all(
            (self._agent_output_dir(output_dir, run) / "output.md").is_file()
            for run in attempt.agent_runs
        )

    @staticmethod
    def _linux_output_dir(task: AuditTask, attempt: AuditAttempt) -> PurePosixPath:
        return (
            PurePosixPath(attempt.result_uri.removeprefix("file://")).parent
            / "action-chain"
            / task.task_id
        )

    def _export_legacy_task(
        self, task: AuditTask, attempt: AuditAttempt, export_script: str, extract_script: str
    ) -> None:
        linux_output_dir = self._linux_output_dir(task, attempt)
        self._run_wsl(
            [
                "env",
                f"DOCGUARD_TRAJECTORY_EXPORT_DIR={linux_output_dir}",
                "bash",
                export_script,
                task.task_id,
            ],
            timeout=120,
        )
        self._run_wsl(["python3", extract_script, str(linux_output_dir)], timeout=60)

    def _export_agent_run(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        run: AgentRun,
        agent_dir: Path,
        export_script: str,
        extract_script: str,
    ) -> None:
        agent_dir.mkdir(parents=True, exist_ok=True)
        linux_agent_dir = self._linux_output_dir(task, attempt) / "agents" / run.agent.agent_id
        self._run_wsl(
            [
                "env",
                f"DOCGUARD_TRAJECTORY_EXPORT_DIR={linux_agent_dir}",
                "bash",
                export_script,
                task.task_id,
                run.agent.agent_id,
                attempt.attempt_id,
            ],
            timeout=120,
        )
        self._run_wsl(["python3", extract_script, str(linux_agent_dir)], timeout=60)

    def _write_task_output(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        output_dir: Path,
        output_path: Path,
        available: list[tuple[AgentRun, Path]],
        failures: list[tuple[AgentRun, str]],
    ) -> None:
        lines = [
            "# DocGuard 多智能体行动链",
            "",
            f"- 任务 ID：`{task.task_id}`",
            f"- 尝试 ID：`{attempt.attempt_id}`",
            f"- 已导出 Agent：{len(available)}/{len(attempt.agent_runs)}",
            "",
            "## Agent 导出状态",
            "",
        ]
        for run, path in available:
            relative_path = path.relative_to(output_dir).as_posix()
            lines.extend(
                [
                    f"- `{run.agent.agent_id}`（{run.agent.dimension}）：已导出，"
                    f"会话响应 `{run.gateway_response_id or 'unknown'}`，"
                    f"文件 `{relative_path}`",
                ]
            )
        for run, error in failures:
            lines.append(f"- `{run.agent.agent_id}`（{run.agent.dimension}）：未导出，原因：{error}")

        for run, path in available:
            lines.extend(
                [
                    "",
                    f"## Agent：`{run.agent.agent_id}`",
                    "",
                    f"- 审核维度：`{run.agent.dimension}`",
                    f"- Scope：`{run.agent.scope or '—'}`",
                    f"- 模型：`{run.agent.agent_model_ref}`",
                    f"- Gateway Response ID：`{run.gateway_response_id or 'unknown'}`",
                    "",
                    self._nest_markdown(path.read_text(encoding="utf-8")),
                ]
            )

        temporary_path = output_path.with_name(f".{output_path.name}.writing")
        temporary_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temporary_path.replace(output_path)

    @staticmethod
    def _nest_markdown(markdown: str) -> str:
        """Move an agent document beneath its task-level heading without touching code blocks."""
        lines: list[str] = []
        inside_fence = False
        for line in markdown.splitlines():
            if line.lstrip().startswith("```"):
                inside_fence = not inside_fence
            if not inside_fence and line.startswith("#"):
                heading_size = len(line) - len(line.lstrip("#"))
                if heading_size and len(line) > heading_size and line[heading_size].isspace():
                    line = "#" * min(6, heading_size + 2) + line[heading_size:]
            lines.append(line)
        return "\n".join(lines)

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
