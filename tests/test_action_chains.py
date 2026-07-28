from hashlib import sha256
from pathlib import Path, PurePosixPath

import pytest

from docguard.domain.models import AgentBackend, AuditAttempt, AuditTask, InputDocument
from docguard.services.action_chains import ActionChainUnavailableError, OpenClawActionChainExporter
from docguard.services.artifacts import ArtifactStore
from docguard.services.profiles import ProfileRegistry


def _task() -> AuditTask:
    return AuditTask(
        task_id="task-example",
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///sample.docx",
        ),
        profile=ProfileRegistry().get("technical-audit"),
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


def test_action_chain_returns_cached_markdown_without_starting_wsl(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task()
    output = tmp_path / task.task_id / task.attempts[0].attempt_id / "action-chain" / task.task_id / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# 完整行动链\n", encoding="utf-8")

    assert OpenClawActionChainExporter(artifacts, enabled=True).download_path(task) == output


def test_action_chain_requires_feature_switch(tmp_path: Path) -> None:
    exporter = OpenClawActionChainExporter(ArtifactStore(tmp_path, PurePosixPath("/docguard-results")), enabled=False)

    with pytest.raises(ActionChainUnavailableError, match="未启用"):
        exporter.download_path(_task())
