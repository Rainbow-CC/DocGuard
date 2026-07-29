from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "doc-audit-integrate-skill" / "scripts" / "validate_findings.py"


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "docguard-audit-input-v1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "document": {"content_sha256": "a" * 64},
        "profile": {
            "profile_id": "technical-audit",
            "version": "1.0.0",
            "prompt_versions": {"full_text": 1, "architecture": 1, "merge": 1},
        },
        "review_type": {
            "review_type_id": "technical-architecture",
            "version": "1.0.0",
            "core_contract_version": 1,
        },
    }


def _evidence() -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "blocks": [
            {"block_index": 28, "type": "paragraph", "text": "名词解释"},
            {"block_index": 29, "type": "paragraph", "text": "无"},
            {
                "block_index": 35,
                "type": "table",
                "rows": [["系统全称", "系统简称"], ["数据中台", "数管平台"]],
            },
        ],
        "candidate_images": [{"image_id": "architecture"}],
    }


def _result(evidence_id: str = "table:35", quote: str = "数据中台 | 数管平台") -> dict[str, object]:
    return {
        "schema_version": "docguard-agent-result-v1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "input_sha256": "a" * 64,
        "profile_id": "technical-audit",
        "profile_version": "1.0.0",
        "prompt_versions": {"full_text": 1, "architecture": 1, "merge": 1},
        "review_type_id": "technical-architecture",
        "review_type_version": "1.0.0",
        "core_contract_version": 1,
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
                "title": "示例",
                "text_evidence": ["table:35"],
                "image_evidence": ["不适用（纯文本审核）"],
                "problem_description": "示例问题",
                "impact": "影响评审",
                "revision_suggestion": "修订内容",
                "revision_location": "第 1 章",
                "completion_criteria": "内容一致",
                "evidence_ids": [evidence_id],
                "evidence_refs": [
                    {
                        "evidence_id": evidence_id,
                        "role": "primary",
                        "quote": quote,
                        "explanation": "支持结论",
                    }
                ],
                "root_cause_key": "example",
                "agent_backend": "openclaw",
            }
        ],
    }


def _run_validator(tmp_path: Path, result: dict[str, object]) -> subprocess.CompletedProcess[str]:
    paths = {
        "manifest": tmp_path / "input-manifest.json",
        "evidence": tmp_path / "audit-evidence.json",
        "input": tmp_path / "findings.partial.json",
    }
    for name, payload in (("manifest", _manifest()), ("evidence", _evidence()), ("input", result)):
        paths[name].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(paths["manifest"]),
            "--evidence",
            str(paths["evidence"]),
            "--input",
            str(paths["input"]),
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def test_preflight_accepts_exact_contiguous_table_quote(tmp_path: Path) -> None:
    completed = _run_validator(tmp_path, _result())

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("evidence_id", "quote", "expected_error"),
    [
        ("block:29", "名词解释\n\n无", "quote is not present in block:29"),
        ("block:35", "数据中台", "unknown evidence_id: block:35"),
        ("table:35", "数据中台 ... 数管平台", "quote is not present in table:35"),
    ],
)
def test_preflight_rejects_synthesized_or_mistyped_evidence(
    tmp_path: Path, evidence_id: str, quote: str, expected_error: str
) -> None:
    completed = _run_validator(tmp_path, _result(evidence_id=evidence_id, quote=quote))

    assert completed.returncode == 2
    assert expected_error in completed.stderr
