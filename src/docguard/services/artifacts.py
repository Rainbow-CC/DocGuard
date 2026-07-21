from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from docguard.domain.models import AgentBackend, AuditAttempt, AuditTask, Finding


class AgentResult(BaseModel):
    """The only durable delivery payload accepted from an agent runtime."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["docguard-agent-result-v1"]
    task_id: str
    attempt_id: str
    input_sha256: str = Field(min_length=64, max_length=64)
    profile_id: str
    profile_version: str
    prompt_versions: dict[str, int]
    findings: list[Finding]


class ArtifactValidationError(ValueError):
    pass


class ArtifactStore:
    """Maps a task attempt to a shared WSL directory with atomic result delivery."""

    def __init__(self, write_root: Path | str, agent_root: PurePosixPath | str) -> None:
        self.write_root = Path(write_root)
        self.agent_root = PurePosixPath(agent_root)

    @classmethod
    def from_environment(cls) -> ArtifactStore:
        return cls(
            write_root=os.getenv(
                "DOCGUARD_RESULT_WRITE_ROOT",
                r"\\wsl.localhost\Ubuntu\home\ubuntu\docguard-results",
            ),
            agent_root=os.getenv("DOCGUARD_RESULT_AGENT_ROOT", "/home/ubuntu/docguard-results"),
        )

    def prepare(self, task: AuditTask) -> AuditAttempt:
        attempt = AuditAttempt(
            input_manifest_uri="pending",
            result_uri="pending",
            input_sha256=task.document.content_sha256,
        )
        local_dir = self._local_dir(task.task_id, attempt.attempt_id)
        local_dir.mkdir(parents=True, exist_ok=False)
        agent_dir = self._agent_dir(task.task_id, attempt.attempt_id)
        manifest = {
            "schema_version": "docguard-audit-input-v1",
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "document": task.document.model_dump(mode="json"),
            "profile": task.profile.model_dump(mode="json"),
        }
        manifest_path = local_dir / "input-manifest.json"
        self._atomic_write_json(manifest_path, manifest)
        attempt.input_manifest_uri = f"file://{agent_dir / 'input-manifest.json'}"
        attempt.result_uri = f"file://{agent_dir / 'findings.json'}"
        return attempt

    def read_result(self, task: AuditTask, attempt: AuditAttempt) -> AgentResult | None:
        path = self._local_dir(task.task_id, attempt.attempt_id) / "findings.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"Unable to read findings artifact: {exc}") from exc
        try:
            result = AgentResult.model_validate(data)
        except Exception as exc:
            raise ArtifactValidationError(f"Invalid findings artifact: {exc}") from exc
        self._validate_metadata(task, attempt, result)
        for finding in result.findings:
            if finding.agent_backend is not AgentBackend.OPENCLAW:
                raise ArtifactValidationError("OpenClaw artifacts must declare agent_backend=openclaw")
        return result

    def _validate_metadata(self, task: AuditTask, attempt: AuditAttempt, result: AgentResult) -> None:
        if result.schema_version != "docguard-agent-result-v1":
            raise ArtifactValidationError(f"Unsupported result schema: {result.schema_version}")
        expected = {
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "input_sha256": task.document.content_sha256,
            "profile_id": task.profile.profile_id,
            "profile_version": task.profile.version,
            "prompt_versions": task.profile.prompt_versions,
        }
        actual = result.model_dump(include=set(expected))
        if actual != expected:
            raise ArtifactValidationError("Findings artifact does not match this task attempt")

    def _local_dir(self, task_id: str, attempt_id: str) -> Path:
        return self.write_root / task_id / attempt_id

    def _agent_dir(self, task_id: str, attempt_id: str) -> PurePosixPath:
        return self.agent_root / task_id / attempt_id

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_name(f".{path.name}.writing")
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
