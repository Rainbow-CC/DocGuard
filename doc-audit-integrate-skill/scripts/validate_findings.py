#!/usr/bin/env python3
"""Fast preflight validation for an agent-produced DocGuard findings artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TOP_LEVEL = {
    "schema_version",
    "task_id",
    "attempt_id",
    "input_sha256",
    "profile_id",
    "profile_version",
    "prompt_versions",
    "findings",
}
FINDING_FIELDS = {
    "finding_id", "schema_version", "rule_id", "category", "review_dimension", "judgment", "severity",
    "confidence", "title", "text_evidence", "image_evidence", "problem_description", "impact",
    "revision_suggestion", "revision_location", "completion_criteria", "evidence_ids", "root_cause_key",
    "agent_backend",
}
JUDGMENTS = {"图文不一致", "文本不一致", "文本不完整", "未提供图示证据", "不确定", "不适用"}
SEVERITIES = {"重大", "一般", "优化", "观察"}
CATEGORIES = {"一致性", "可用性", "部署", "安全", "数据流", "可读性"}


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON: {exc}") from exc


def require_fields(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        missing = fields - set(value) if isinstance(value, dict) else fields
        extra = set(value) - fields if isinstance(value, dict) else set()
        raise ValueError(f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    manifest = require_fields(load_json(args.manifest), {
        "schema_version", "task_id", "attempt_id", "document", "profile", "evidence", "allowed_evidence_ids"
    }, "manifest")
    result = require_fields(load_json(args.input), TOP_LEVEL, "result")
    if result["schema_version"] != "docguard-agent-result-v1":
        raise ValueError("unsupported result schema_version")
    document = manifest["document"]
    profile = manifest["profile"]
    expected = {
        "task_id": manifest["task_id"], "attempt_id": manifest["attempt_id"],
        "input_sha256": document["content_sha256"], "profile_id": profile["profile_id"],
        "profile_version": profile["version"], "prompt_versions": profile["prompt_versions"],
    }
    if {key: result[key] for key in expected} != expected:
        raise ValueError("result metadata does not match manifest")
    if not isinstance(result["findings"], list):
        raise ValueError("findings must be an array")
    allowed = set(manifest["allowed_evidence_ids"])
    for index, raw in enumerate(result["findings"]):
        finding = require_fields(raw, FINDING_FIELDS, f"findings[{index}]")
        if finding["schema_version"] != "finding-v1" or finding["agent_backend"] != "openclaw":
            raise ValueError(f"findings[{index}] has an invalid fixed field")
        if finding["judgment"] not in JUDGMENTS or finding["severity"] not in SEVERITIES:
            raise ValueError(f"findings[{index}] has an invalid judgment or severity")
        if finding["category"] not in CATEGORIES:
            raise ValueError(f"findings[{index}] has an invalid category")
        if not isinstance(finding["confidence"], (int, float)) or not 0 <= finding["confidence"] <= 1:
            raise ValueError(f"findings[{index}] confidence must be between 0 and 1")
        for key in ("text_evidence", "image_evidence", "evidence_ids"):
            if not isinstance(finding[key], list) or not finding[key]:
                raise ValueError(f"findings[{index}].{key} must be a non-empty array")
        if not set(finding["evidence_ids"]).issubset(allowed):
            raise ValueError(f"findings[{index}] references an unknown evidence id")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"findings validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
