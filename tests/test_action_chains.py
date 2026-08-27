from hashlib import sha256
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from docguard.domain.models import (
    AgentBackend,
    AgentRun,
    AuditAgentDefinition,
    AuditAttempt,
    AuditTask,
    InputDocument,
)
from docguard.services.action_chains import ActionChainUnavailableError, OpenClawActionChainExporter
from docguard.services.artifacts import ArtifactStore


def _task(technical_review_type) -> AuditTask:
    return AuditTask(
        task_id="task-example",
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///sample.docx",
        ),
        profile=technical_review_type.profile,
        agent_backend=AgentBackend.OPENCLAW,
        attempts=[
            AuditAttempt(
                attempt_id="attempt-example",
                input_manifest_uri="file:///docguard-results/task-example/attempt-example/input-manifest.json",
                result_uri="file:///docguard-results/task-example/attempt-example/findings.json",
                input_sha256=sha256(b"sample").hexdigest(),
            )
        ],
    )


def test_action_chain_returns_cached_markdown_without_starting_wsl(tmp_path: Path, technical_review_type) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task(technical_review_type)
    output = tmp_path / task.task_id / task.attempts[0].attempt_id / "action-chain" / task.task_id / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# 完整行动链\n", encoding="utf-8")

    assert OpenClawActionChainExporter(artifacts, enabled=True).download_path(task) == output


def test_action_chain_requires_feature_switch(tmp_path: Path, technical_review_type) -> None:
    exporter = OpenClawActionChainExporter(ArtifactStore(tmp_path, PurePosixPath("/docguard-results")), enabled=False)

    with pytest.raises(ActionChainUnavailableError, match="未启用"):
        exporter.download_path(_task(technical_review_type))


class RecordingActionChainExporter(OpenClawActionChainExporter):
    def __init__(self, artifacts: ArtifactStore) -> None:
        super().__init__(artifacts, enabled=True)
        self.commands: list[list[str]] = []

    def _linux_path(self, path: Path) -> str:
        return f"/tools/{path.name}"

    def _run_wsl(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[0] == "python3":
            agent_id = PurePosixPath(command[-1]).name
            output = (
                self.artifacts.write_root
                / "task-example"
                / "attempt-example"
                / "action-chain"
                / "task-example"
                / "agents"
                / agent_id
                / "output.md"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                f"# {agent_id} 行动链\n\n```text\n# fenced heading\n```\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_action_chain_exports_each_agent_and_combines_their_markdown(
    tmp_path: Path, technical_review_type
) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task(technical_review_type)
    content = technical_review_type.resolved_agents()[0]
    structure = AuditAgentDefinition(
        agent_id="structure-reviewer",
        version="1.0.0",
        dimension="structure",
        agent_model_ref="openclaw/structure-reviewer",
        skill_ref="docx-structure-reviewer",
        rule_pack_ref="technical-architecture/structure-rules.md",
        rule_pack_version="1.0.0",
    )
    attempt = task.attempts[0]
    attempt.agent_runs = [
        AgentRun(agent=content, result_uri="file:///docguard-results/content.findings.json"),
        AgentRun(agent=structure, result_uri="file:///docguard-results/structure.findings.json"),
    ]
    exporter = RecordingActionChainExporter(artifacts)

    output = exporter.download_path(task)

    export_commands = [command for command in exporter.commands if command[0] == "env"]
    assert [command[-2] for command in export_commands] == ["content-reviewer", "structure-reviewer"]
    assert all(command[-1] == attempt.attempt_id for command in export_commands)
    assert all("DOCGUARD_TRAJECTORY_EXPORT_DIR=" in command[1] for command in export_commands)
    assert all("/agents/" in command[1] for command in export_commands)
    assert output.read_text(encoding="utf-8") == (
        "# DocGuard 多智能体行动链\n"
        "\n"
        "- 任务 ID：`task-example`\n"
        "- 尝试 ID：`attempt-example`\n"
        "- 已导出 Agent：2/2\n"
        "\n"
        "## Agent 导出状态\n"
        "\n"
        "- `content-reviewer`（content）：已导出，会话响应 `unknown`，"
        "文件 `agents/content-reviewer/output.md`\n"
        "- `structure-reviewer`（structure）：已导出，会话响应 `unknown`，"
        "文件 `agents/structure-reviewer/output.md`\n"
        "\n"
        "## Agent：`content-reviewer`\n"
        "\n"
        "- 审核维度：`content`\n"
        "- Scope：`—`\n"
        "- 模型：`openclaw/audit-runtime`\n"
        "- Gateway Response ID：`unknown`\n"
        "\n"
        "### content-reviewer 行动链\n"
        "\n"
        "```text\n"
        "# fenced heading\n"
        "```\n"
        "\n"
        "## Agent：`structure-reviewer`\n"
        "\n"
        "- 审核维度：`structure`\n"
        "- Scope：`—`\n"
        "- 模型：`openclaw/structure-reviewer`\n"
        "- Gateway Response ID：`unknown`\n"
        "\n"
        "### structure-reviewer 行动链\n"
        "\n"
        "```text\n"
        "# fenced heading\n"
        "```\n"
    )
