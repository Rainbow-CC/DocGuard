from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from docguard.domain.models import (
    AgentBackend,
    AgentRun,
    AuditAttempt,
    AuditTask,
    EvidenceRef,
    Finding,
)
from docguard.settings import Settings


logger = logging.getLogger("docguard.artifacts")


class AgentResult(BaseModel):
    """The only durable delivery payload accepted from an agent runtime."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["docguard-agent-result-v1"]
    task_id: str
    attempt_id: str
    input_sha256: str = Field(min_length=64, max_length=64)
    profile_id: str
    profile_version: str | None = None
    prompt_versions: dict[str, int] | None = None
    review_type_id: str | None = None
    review_type_version: str | None = None
    core_contract_version: int | None = None
    dimension: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    scope: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    producer_agent_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    producer_agent_version: str
    producer_model_ref: str
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
        settings = Settings.from_environment()
        return cls(settings.result_write_root, settings.result_agent_root)

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
            "review_type": task.review_type.model_dump(mode="json") if task.review_type else None,
        }
        manifest_path = local_dir / "input-manifest.json"
        self._atomic_write_json(manifest_path, manifest)
        attempt.input_manifest_uri = f"file://{agent_dir / 'input-manifest.json'}"
        findings_dir = local_dir / "findings"
        findings_dir.mkdir()
        attempt.result_uri = f"file://{agent_dir / 'findings'}"
        attempt.agent_runs = [
            AgentRun(
                agent=agent,
                result_uri=f"file://{agent_dir / 'findings' / f'{agent.artifact_stem}.findings.json'}",
            )
            for agent in task.review_type.resolved_agents()
        ] if task.review_type else []
        stems = [run.agent.artifact_stem for run in attempt.agent_runs]
        if len(stems) != len(set(stems)):
            raise ArtifactValidationError("Review type registers duplicate findings artifact names")
        logger.info(
            "artifact.manifest_written task_id=%s attempt_id=%s manifest_path=%s findings_dir=%s",
            task.task_id,
            attempt.attempt_id,
            manifest_path,
            findings_dir,
        )
        return attempt

    def read_results(self, task: AuditTask, attempt: AuditAttempt) -> list[AgentResult]:
        """Read every completed specialist artifact for an attempt.

        Only final ``*.findings.json`` names are scanned. Agents write another
        extension (normally ``.tmp``) and atomically rename only after their
        local validation passes, so partial files are never candidates here.
        """
        findings_dir = self._local_dir(task.task_id, attempt.attempt_id) / "findings"
        if not findings_dir.is_dir():
            return []
        expected = {run.agent.artifact_stem: run for run in attempt.agent_runs}
        results: list[AgentResult] = []
        for path in sorted(findings_dir.glob("*.findings.json")):
            stem = path.name.removesuffix(".findings.json")
            run = expected.get(stem)
            if run is None:
                raise ArtifactValidationError(f"Unexpected findings artifact: {path.name}")
            results.append(self._read_result_file(task, attempt, run, path))
        return results

    def read_result(self, task: AuditTask, attempt: AuditAttempt) -> AgentResult | None:
        """Compatibility accessor for callers expecting a single specialist result."""
        results = self.read_results(task, attempt)
        if not results:
            return None
        if len(results) != 1:
            raise ArtifactValidationError("Attempt has multiple findings artifacts; use read_results")
        return results[0]

    def _read_result_file(
        self, task: AuditTask, attempt: AuditAttempt, run: AgentRun, path: Path
    ) -> AgentResult:
        if not path.is_file():
            logger.info(
                "artifact.result_pending task_id=%s attempt_id=%s result_path=%s",
                task.task_id,
                attempt.attempt_id,
                path,
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"Unable to read findings artifact: {exc}") from exc
        try:
            result = AgentResult.model_validate(data)
        except Exception as exc:
            raise ArtifactValidationError(f"Invalid findings artifact: {exc}") from exc
        self._validate_metadata(task, attempt, run, result)
        evidence = self.read_evidence(task, attempt)
        self._validate_evidence_refs(result.findings, evidence)
        for finding in result.findings:
            if finding.agent_backend is not AgentBackend.OPENCLAW:
                raise ArtifactValidationError(
                    "OpenClaw artifacts must declare agent_backend=openclaw"
                )
        logger.info(
            "artifact.result_validated task_id=%s attempt_id=%s agent_id=%s findings=%s",
            task.task_id,
            attempt.attempt_id,
            run.agent.agent_id,
            len(result.findings),
        )
        return result

    def read_evidence(self, task: AuditTask, attempt: AuditAttempt) -> dict[str, Any] | None:
        """Read the review bundle delivered next to ``findings.json`` when present."""
        path = (
            self._local_dir(task.task_id, attempt.attempt_id) / "evidence" / "audit-evidence.json"
        )
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"Unable to read evidence bundle: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
            raise ArtifactValidationError("Evidence bundle has no blocks array")
        if not isinstance(payload.get("candidate_images", []), list):
            raise ArtifactValidationError("Evidence bundle has an invalid candidate_images array")
        return payload

    def evidence_presentation(self, task: AuditTask) -> dict[str, Any] | None:
        """Return a safe, browser-oriented projection of the completed evidence bundle."""
        if not task.attempts:
            return None
        attempt = task.attempts[-1]
        evidence = self.read_evidence(task, attempt)
        if evidence is None:
            return None
        image_base = f"/api/v1/tasks/{task.task_id}/evidence/images"
        images = []
        for image in evidence.get("candidate_images", []):
            if not isinstance(image, dict) or not isinstance(image.get("image_id"), str):
                continue
            images.append(
                {
                    key: value
                    for key, value in image.items()
                    if key not in {"rendered_png_file", "nearby_text"}
                }
                | {"asset_url": f"{image_base}/{image['image_id']}"}
            )
        return {
            "schema_version": evidence.get("schema_version", "unknown"),
            "chapters": evidence.get("chapters", []),
            "blocks": evidence["blocks"],
            "candidate_images": images,
        }

    def evidence_image_path(self, task: AuditTask, image_id: str) -> Path | None:
        """Resolve an image from the bundle without accepting a caller-supplied path."""
        if not task.attempts:
            return None
        attempt = task.attempts[-1]
        evidence = self.read_evidence(task, attempt)
        if evidence is None:
            return None
        image = next(
            (
                item
                for item in evidence.get("candidate_images", [])
                if isinstance(item, dict) and item.get("image_id") == image_id
            ),
            None,
        )
        if not image or not isinstance(image.get("rendered_png_file"), str):
            return None
        evidence_dir = (self._local_dir(task.task_id, attempt.attempt_id) / "evidence").resolve()
        candidate = (evidence_dir / image["rendered_png_file"]).resolve()
        try:
            candidate.relative_to(evidence_dir)
        except ValueError:
            raise ArtifactValidationError("Evidence image path escapes its bundle") from None
        return candidate if candidate.is_file() else None

    def _validate_metadata(
        self, task: AuditTask, attempt: AuditAttempt, run: AgentRun, result: AgentResult
    ) -> None:
        if result.schema_version != "docguard-agent-result-v1":
            raise ArtifactValidationError(f"Unsupported result schema: {result.schema_version}")
        expected = {
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "input_sha256": task.document.content_sha256,
            "profile_id": task.profile.profile_id,
            "profile_version": task.profile.version,
            "prompt_versions": task.profile.prompt_versions,
            "review_type_id": task.review_type.review_type_id
            if task.review_type
            else "legacy-technical-architecture",
            "review_type_version": task.review_type.version if task.review_type else "1.0.0",
            "core_contract_version": task.review_type.core_contract_version
            if task.review_type
            else 1,
            "dimension": run.agent.dimension,
            "scope": run.agent.scope,
            "producer_agent_id": run.agent.agent_id,
            "producer_agent_version": run.agent.version,
            "producer_model_ref": run.agent.agent_model_ref,
        }
        actual = result.model_dump(include=set(expected))
        if actual != expected:
            raise ArtifactValidationError("Findings artifact does not match this task attempt")

    def _validate_evidence_refs(
        self, findings: list[Finding], evidence: dict[str, Any] | None
    ) -> None:
        refs = [ref for finding in findings for ref in finding.evidence_refs]
        if not refs:
            return
        if evidence is None:
            raise ArtifactValidationError("Structured evidence_refs require an evidence bundle")
        blocks = {
            f"{'table' if block.get('type') == 'table' else 'block'}:{block.get('block_index')}": block
            for block in evidence["blocks"]
            if isinstance(block, dict) and isinstance(block.get("block_index"), int)
        }
        images = {
            f"image:{image.get('image_id')}": image
            for image in evidence.get("candidate_images", [])
            if isinstance(image, dict) and isinstance(image.get("image_id"), str)
        }
        for ref in refs:
            self._validate_evidence_ref(ref, blocks, images)

    @staticmethod
    def _validate_evidence_ref(
        ref: EvidenceRef, blocks: dict[str, dict[str, Any]], images: dict[str, dict[str, Any]]
    ) -> None:
        item = blocks.get(ref.evidence_id) or images.get(ref.evidence_id)
        if item is None:
            raise ArtifactValidationError(f"Unknown evidence_id: {ref.evidence_id}")
        is_image = ref.evidence_id.startswith("image:")
        if ref.region is not None and not is_image:
            raise ArtifactValidationError(
                f"Only image evidence may define region: {ref.evidence_id}"
            )
        if is_image:
            if ref.selector is not None:
                raise ArtifactValidationError(
                    f"Only text/table evidence may define selector: {ref.evidence_id}"
                )
            return
        if _normalize(ref.quote) not in _normalize(_block_content(item)):
            raise ArtifactValidationError(f"Evidence quote is not present in {ref.evidence_id}")
        if ref.selector is not None:
            _validate_selector(ref.evidence_id, item, ref)

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


def _block_content(block: dict[str, Any]) -> str:
    if block.get("type") == "table":
        return "\n".join(" | ".join(str(cell) for cell in row) for row in block.get("rows", []))
    return str(block.get("text", ""))


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _validate_selector(evidence_id: str, block: dict[str, Any], ref: EvidenceRef) -> None:
    selector = ref.selector
    assert selector is not None
    if block.get("type") != "table":
        raise ArtifactValidationError(f"Only table evidence may define selector: {evidence_id}")
    rows = block.get("rows", [])
    if not rows:
        raise ArtifactValidationError(f"Cannot select a row from an empty table: {evidence_id}")
    headers = [str(value) for value in rows[0]]
    if any(column not in headers for column in selector.columns) or any(
        key not in headers for key in selector.row_match
    ):
        raise ArtifactValidationError(f"Evidence selector has unknown table columns: {evidence_id}")
    if selector.row_match:
        matches = [
            row
            for row in rows[1:]
            if all(
                headers.index(key) < len(row) and str(row[headers.index(key)]) == expected
                for key, expected in selector.row_match.items()
            )
        ]
        if len(matches) != 1:
            raise ArtifactValidationError(
                f"Evidence selector must match exactly one table row: {evidence_id}"
            )
