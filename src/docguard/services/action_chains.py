from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path, PurePosixPath

import httpx

from docguard.domain.models import AgentBackend, AuditTask
from docguard.services.artifacts import ArtifactStore


logger = logging.getLogger("docguard.action_chains")


class ActionChainUnavailableError(ValueError):
    """The task cannot provide a downloadable session trajectory."""


class DshActionChainExporter:
    """Export and cache a DSH session trajectory via GET /history."""

    def __init__(self, artifacts: ArtifactStore, *, enabled: bool | None = None) -> None:
        self.artifacts = artifacts
        self.enabled = enabled if enabled is not None else _enabled_from_environment()
        self.gateway_url = os.getenv("DSH_GATEWAY_URL", "").rstrip("/")
        self.api_key = os.getenv("DSH_AGW_KEY", "")

    def download_path(self, task: AuditTask) -> Path:
        """Return cached Markdown, or fetch the DSH session history and create it."""
        if not self.enabled:
            raise ActionChainUnavailableError("行动链导出功能未启用")
        if task.agent_backend is not AgentBackend.DSH:
            raise ActionChainUnavailableError("只有 DSH 任务可导出行动链")
        if not task.attempts:
            raise ActionChainUnavailableError("任务尚未创建 DSH 尝试")

        attempt = task.attempts[-1]
        if not attempt.gateway_response_id:
            raise ActionChainUnavailableError("DSH 会话 ID 不存在，无法导出行动链")

        output_dir = self.artifacts.write_root / task.task_id / attempt.attempt_id / "action-chain" / task.task_id
        output_path = output_dir / "output.md"
        if output_path.is_file():
            return output_path

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        history = self._fetch_history(attempt.gateway_response_id)
        self._write_markdown(output_path, history)
        logger.info("action_chain.exported task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
        return output_path

    def _fetch_history(self, session_id: str) -> list[dict]:
        """Fetch full session history from DSH gateway."""
        if not self.gateway_url or not self.api_key:
            raise ActionChainUnavailableError("DSH_GATEWAY_URL 或 DSH_API_KEY 未配置")

        url = f"{self.gateway_url}/sessions/{session_id}/history"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.json().get("events", [])
        except httpx.HTTPError as exc:
            raise ActionChainUnavailableError(f"无法获取 DSH 会话历史：{exc}") from exc

    def _write_markdown(self, output_path: Path, history: list[dict]) -> None:
        """Write session history as readable markdown."""
        lines = ["# 会话行动链\n"]
        for event in history:
            kind = event.get("kind", "")
            role = event.get("role", "")
            text = event.get("text", "") or ""
            reasoning = event.get("reasoning", "") or ""
            if kind == "message" and role == "user":
                lines.append(f"## 用户\n{text}\n")
            elif kind == "message" and role == "assistant":
                if reasoning:
                    lines.append(f"## 助手（思考）\n{reasoning}\n")
                if text:
                    lines.append(f"## 助手（回答）\n{text}\n")
            elif kind == "tool_call":
                lines.append(f"## 工具调用\n{json.dumps(event, ensure_ascii=False, indent=2)}\n")
            elif kind == "tool_result":
                lines.append(f"## 工具结果\n{json.dumps(event, ensure_ascii=False, indent=2)}\n")
        output_path.write_text("\n".join(lines), encoding="utf-8")


class OpenClawActionChainExporter:
    """Export and cache a development-only, full OpenClaw session trajectory."""

    def __init__(self, artifacts: ArtifactStore, *, enabled: bool | None = None) -> None:
        self.artifacts = artifacts
        self.enabled = enabled if enabled is not None else _enabled_from_environment()
        self.wsl_distribution = os.getenv("DOCGUARD_WSL_DISTRIBUTION", "").strip()
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


def _enabled_from_environment() -> bool:
    return os.getenv("DOCGUARD_ACTION_CHAIN_EXPORT_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
