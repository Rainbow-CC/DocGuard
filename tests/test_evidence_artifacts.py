import importlib
import json
from hashlib import sha256
from pathlib import Path, PurePosixPath

from fastapi.testclient import TestClient

from docguard.domain.models import AgentBackend, AuditAgentDefinition, AuditTask, InputDocument
from docguard.services.artifacts import ArtifactStore, ArtifactValidationError
from docguard.services.store import InMemoryTaskStore
from docguard.services.tasks import AuditTaskService


def _task(technical_review_type) -> AuditTask:
    return AuditTask(
        document=InputDocument(
            filename="sample.docx",
            content_sha256=sha256(b"sample").hexdigest(),
            source_uri="file:///docguard-inbox/reviewer/sample/source.docx",
        ),
        profile=technical_review_type.profile,
        review_type=technical_review_type,
        agent_backend=AgentBackend.OPENCLAW,
    )


def _bundle() -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "source": "/private/source.docx",
        "chapters": [{"id": "chapter-1", "chapter_number": 1, "title": "概述"}],
        "blocks": [
            {"block_index": 4, "type": "paragraph", "chapter_id": "chapter-1", "text": "统一鉴权入口。"},
            {
                "block_index": 7,
                "type": "table",
                "chapter_id": "chapter-1",
                "rows": [["系统指标", "目标值"], ["允许系统数据丢失时间", "故障恢复<=24小时；"]],
            },
        ],
        "candidate_images": [
            {
                "image_id": "image-example",
                "chapter_id": "chapter-1",
                "chapter_number": 1,
                "chapter_title": "概述",
                "rendered_png_file": "rendered/image-example.png",
            }
        ],
    }


def _result(task: AuditTask, attempt_id: str, quote: str = "故障恢复<=24小时；", run=None) -> dict[str, object]:
    agent = run.agent if run else task.review_type.resolved_agents()[0]
    return {
        "schema_version": "docguard-agent-result-v1",
        "task_id": task.task_id,
        "attempt_id": attempt_id,
        "input_sha256": task.document.content_sha256,
        "profile_id": task.profile.profile_id,
        "profile_version": task.profile.version,
        "prompt_versions": task.profile.prompt_versions,
        "review_type_id": task.review_type.review_type_id,
        "review_type_version": task.review_type.version,
        "core_contract_version": task.review_type.core_contract_version,
        "dimension": agent.dimension,
        "scope": agent.scope,
        "producer_agent_id": agent.agent_id,
        "producer_agent_version": agent.version,
        "producer_model_ref": agent.agent_model_ref,
        "findings": [
            {
                "finding_id": "fd_example",
                "schema_version": "finding-v1",
                "rule_id": "DG-001",
                "category": "一致性",
                "review_dimension": "一致性与可读性",
                "judgment": "文本不一致",
                "severity": "一般",
                "confidence": 0.9,
                "title": "指标口径不一致",
                "text_evidence": ["第1章，table:7：故障恢复<=24小时；"],
                "image_evidence": ["不适用（纯文本审核）"],
                "problem_description": "目标值的表述与字段语义不一致。",
                "impact": "影响评审理解。",
                "revision_suggestion": "明确数据丢失窗口。",
                "revision_location": "第 1 章",
                "completion_criteria": "指标表述一致。",
                "evidence_ids": ["txt_007"],
                "evidence_refs": [
                    {
                        "evidence_id": "table:7",
                        "role": "primary",
                        "quote": quote,
                        "explanation": "该目标值使用了故障恢复表述。",
                        "selector": {
                            "row_match": {"系统指标": "允许系统数据丢失时间"},
                            "columns": ["系统指标", "目标值"],
                        },
                    }
                ],
                "root_cause_key": "indicator:example",
                "agent_backend": "openclaw",
            }
        ],
    }


def _write_bundle(root: Path, task: AuditTask, attempt_id: str) -> None:
    evidence_dir = root / task.task_id / attempt_id / "evidence"
    (evidence_dir / "rendered").mkdir(parents=True)
    (evidence_dir / "audit-evidence.json").write_text(
        json.dumps(_bundle(), ensure_ascii=False), encoding="utf-8"
    )
    (evidence_dir / "rendered" / "image-example.png").write_bytes(b"PNG")


def test_artifact_validates_structured_evidence_and_projects_safe_view(
    tmp_path: Path, technical_review_type
) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task(technical_review_type)
    attempt = artifacts.prepare(task)
    task.attempts.append(attempt)
    _write_bundle(tmp_path, task, attempt.attempt_id)
    result_path = tmp_path / task.task_id / attempt.attempt_id / "findings" / "content.findings.json"
    result_path.write_text(json.dumps(_result(task, attempt.attempt_id), ensure_ascii=False), encoding="utf-8")

    result = artifacts.read_result(task, attempt)
    presentation = artifacts.evidence_presentation(task)

    assert result is not None
    assert presentation is not None
    assert presentation["candidate_images"][0]["asset_url"].endswith("/image-example")
    assert "source" not in presentation
    assert artifacts.evidence_image_path(task, "image-example").read_bytes() == b"PNG"


def test_artifact_rejects_quote_not_in_evidence(tmp_path: Path, technical_review_type) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task(technical_review_type)
    attempt = artifacts.prepare(task)
    _write_bundle(tmp_path, task, attempt.attempt_id)
    result_path = tmp_path / task.task_id / attempt.attempt_id / "findings" / "content.findings.json"
    result_path.write_text(
        json.dumps(_result(task, attempt.attempt_id, quote="不存在的原文"), ensure_ascii=False), encoding="utf-8"
    )

    try:
        artifacts.read_result(task, attempt)
    except ArtifactValidationError as exc:
        assert "Evidence quote is not present" in str(exc)
    else:
        raise AssertionError("Expected invalid quote to be rejected")


def test_artifact_scans_multiple_final_files_and_ignores_temporary_files(
    tmp_path: Path, technical_review_type
) -> None:
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    task = _task(technical_review_type)
    architecture = AuditAgentDefinition(
        agent_id="architecture-advisor",
        version="1.0.0",
        dimension="architecture",
        agent_model_ref="openclaw/architect",
        skill_ref="architecture-advisor",
        rule_pack_ref="technical-architecture/review-rules.md",
        rule_pack_version="1.0.0",
    )
    task.review_type.agents.append(architecture)
    attempt = artifacts.prepare(task)
    _write_bundle(tmp_path, task, attempt.attempt_id)
    findings_dir = tmp_path / task.task_id / attempt.attempt_id / "findings"
    for run in attempt.agent_runs:
        (findings_dir / f"{run.agent.artifact_stem}.findings.json").write_text(
            json.dumps(_result(task, attempt.attempt_id, run=run), ensure_ascii=False), encoding="utf-8"
        )
    (findings_dir / "security.findings.json.tmp").write_text("partial", encoding="utf-8")

    results = artifacts.read_results(task, attempt)

    assert [result.dimension for result in results] == ["architecture", "content"]
    assert sum(len(result.findings) for result in results) == 2


def test_evidence_api_returns_bundle_and_image(
    tmp_path: Path, monkeypatch, review_type_registry, technical_review_type
) -> None:
    api = importlib.import_module("docguard.api.app")
    artifacts = ArtifactStore(tmp_path, PurePosixPath("/docguard-results"))
    store = InMemoryTaskStore()
    task = _task(technical_review_type)
    attempt = artifacts.prepare(task)
    task.attempts.append(attempt)
    store.create(task)
    _write_bundle(tmp_path, task, attempt.attempt_id)
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "service", AuditTaskService(store, review_type_registry, artifacts=artifacts))

    client = TestClient(api.app)
    evidence = client.get(f"/api/v1/tasks/{task.task_id}/evidence")
    image = client.get(f"/api/v1/tasks/{task.task_id}/evidence/images/image-example")

    assert evidence.status_code == 200
    assert evidence.json()["blocks"][1]["block_index"] == 7
    assert image.status_code == 200
    assert image.content == b"PNG"
