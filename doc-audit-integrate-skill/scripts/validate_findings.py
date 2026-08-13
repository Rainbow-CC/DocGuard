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
    "review_type_id",
    "review_type_version",
    "core_contract_version",
    "dimension",
    "scope",
    "producer_agent_id",
    "producer_agent_version",
    "producer_model_ref",
    "findings",
}
FINDING_FIELDS = {
    "finding_id", "schema_version", "rule_id", "category", "review_dimension", "judgment", "severity",
    "confidence", "title", "text_evidence", "image_evidence", "problem_description", "impact",
    "revision_suggestion", "revision_location", "completion_criteria", "evidence_ids", "evidence_refs", "root_cause_key",
    "agent_backend",
}
JUDGMENTS = {"图文不一致", "文本不一致", "文本不完整", "未提供图示证据", "不确定", "不适用"}
SEVERITIES = {"重大", "一般", "优化", "观察"}
CATEGORIES = {"一致性", "可用性", "部署", "安全", "数据流", "可读性"}
REF_FIELDS = {"evidence_id", "role", "quote", "explanation", "selector", "region"}


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


def normalize(value: str) -> str:
    return " ".join(value.split())


def block_content(block: dict[str, object]) -> str:
    if block.get("type") == "table":
        rows = block.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("table evidence rows must be an array")
        return "\n".join(
            " | ".join(str(cell) for cell in row)
            for row in rows
            if isinstance(row, list)
        )
    return str(block.get("text", ""))


def build_evidence_index(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise ValueError("evidence bundle has no blocks array")
    images = payload.get("candidate_images", [])
    if not isinstance(images, list):
        raise ValueError("evidence bundle has an invalid candidate_images array")

    indexed: dict[str, dict[str, object]] = {}
    for block in payload["blocks"]:
        if not isinstance(block, dict) or not isinstance(block.get("block_index"), int):
            continue
        prefix = "table" if block.get("type") == "table" else "block"
        evidence_id = f"{prefix}:{block['block_index']}"
        if evidence_id in indexed:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        indexed[evidence_id] = block
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("image_id"), str):
            continue
        evidence_id = f"image:{image['image_id']}"
        if evidence_id in indexed:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        indexed[evidence_id] = image
    return indexed


def validate_selector(selector: object, evidence_id: str, block: dict[str, object]) -> None:
    if not isinstance(selector, dict) or set(selector) - {"row_match", "columns"}:
        raise ValueError(f"selector has invalid fields for {evidence_id}")
    if block.get("type") != "table":
        raise ValueError(f"only table evidence may define selector: {evidence_id}")
    row_match = selector.get("row_match", {})
    columns = selector.get("columns", [])
    if not isinstance(row_match, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in row_match.items()
    ):
        raise ValueError(f"selector.row_match is invalid for {evidence_id}")
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise ValueError(f"selector.columns is invalid for {evidence_id}")
    rows = block.get("rows", [])
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        raise ValueError(f"cannot select a row from an empty table: {evidence_id}")
    headers = [str(value) for value in rows[0]]
    if any(column not in headers for column in columns) or any(key not in headers for key in row_match):
        raise ValueError(f"selector has unknown table columns: {evidence_id}")
    if row_match:
        matches = [
            row
            for row in rows[1:]
            if isinstance(row, list) and all(
                headers.index(key) < len(row) and str(row[headers.index(key)]) == expected
                for key, expected in row_match.items()
            )
        ]
        if len(matches) != 1:
            raise ValueError(f"selector must match exactly one table row: {evidence_id}")


def validate_region(region: object, evidence_id: str) -> None:
    fields = {"x", "y", "width", "height"}
    if not isinstance(region, dict) or set(region) != fields:
        raise ValueError(f"region has invalid fields for {evidence_id}")
    if not all(isinstance(region[key], (int, float)) and not isinstance(region[key], bool) for key in fields):
        raise ValueError(f"region must contain numeric coordinates for {evidence_id}")
    x, y, width, height = (float(region[key]) for key in ("x", "y", "width", "height"))
    if not 0 <= x <= 1 or not 0 <= y <= 1 or not 0 < width <= 1 or not 0 < height <= 1:
        raise ValueError(f"region is outside normalized bounds for {evidence_id}")
    if x + width > 1 or y + height > 1:
        raise ValueError(f"region must stay within the image bounds for {evidence_id}")


def validate_evidence_ref(ref: dict[str, object], label: str, indexed: dict[str, dict[str, object]]) -> None:
    evidence_id = str(ref["evidence_id"])
    item = indexed.get(evidence_id)
    if item is None:
        raise ValueError(f"{label} has unknown evidence_id: {evidence_id}")
    is_image = evidence_id.startswith("image:")
    if ref.get("region") is not None:
        if not is_image:
            raise ValueError(f"only image evidence may define region: {evidence_id}")
        validate_region(ref["region"], evidence_id)
    if is_image:
        if ref.get("selector") is not None:
            raise ValueError(f"only text/table evidence may define selector: {evidence_id}")
        return
    quote = str(ref["quote"])
    if normalize(quote) not in normalize(block_content(item)):
        raise ValueError(f"{label} quote is not present in {evidence_id}")
    if ref.get("selector") is not None:
        validate_selector(ref["selector"], evidence_id, item)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    manifest = require_fields(load_json(args.manifest), {
        "schema_version", "task_id", "attempt_id", "document", "profile", "review_type"
    }, "manifest")
    result = require_fields(load_json(args.input), TOP_LEVEL, "result")
    indexed_evidence = build_evidence_index(load_json(args.evidence))
    if result["schema_version"] != "docguard-agent-result-v1":
        raise ValueError("unsupported result schema_version")
    document = manifest["document"]
    profile = manifest["profile"]
    review_type = manifest["review_type"]
    if not isinstance(review_type, dict):
        raise ValueError("manifest has no review type definition")
    expected = {
        "task_id": manifest["task_id"], "attempt_id": manifest["attempt_id"],
        "input_sha256": document["content_sha256"], "profile_id": profile["profile_id"],
        "profile_version": profile["version"], "prompt_versions": profile["prompt_versions"],
        "review_type_id": review_type["review_type_id"],
        "review_type_version": review_type["version"],
        "core_contract_version": review_type["core_contract_version"],
    }
    if {key: result[key] for key in expected} != expected:
        raise ValueError("result metadata does not match manifest")
    agents = review_type.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("review type agents must be an array")
    if not agents:
        # Compatibility with review-type snapshots created before specialists
        # were registered explicitly; mirrors ReviewTypeDefinition.resolved_agents.
        agents = [
            {
                "agent_id": "default",
                "version": review_type.get("version"),
                "dimension": "content",
                "scope": None,
                "agent_model_ref": review_type.get("agent_model_ref"),
            }
        ]
    agent = next(
        (
            item
            for item in agents
            if isinstance(item, dict) and item.get("agent_id") == result["producer_agent_id"]
        ),
        None,
    )
    if agent is None:
        raise ValueError("result producer_agent_id is not registered for this review type")
    expected_producer = {
        "dimension": agent.get("dimension"),
        "scope": agent.get("scope"),
        "producer_agent_id": agent.get("agent_id"),
        "producer_agent_version": agent.get("version"),
        "producer_model_ref": agent.get("agent_model_ref"),
    }
    if {key: result[key] for key in expected_producer} != expected_producer:
        raise ValueError("result producer metadata does not match the registered agent")
    if not isinstance(result["findings"], list):
        raise ValueError("findings must be an array")
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
        if not isinstance(finding["evidence_refs"], list) or not finding["evidence_refs"]:
            raise ValueError(f"findings[{index}].evidence_refs must be a non-empty array")
        for ref_index, ref in enumerate(finding["evidence_refs"]):
            if not isinstance(ref, dict) or set(ref) - REF_FIELDS:
                raise ValueError(f"findings[{index}].evidence_refs[{ref_index}] has invalid fields")
            for key in ("evidence_id", "quote", "explanation"):
                if not isinstance(ref.get(key), str) or not ref[key].strip():
                    raise ValueError(f"findings[{index}].evidence_refs[{ref_index}].{key} must be a non-empty string")
            if ref.get("role", "primary") not in {"primary", "supporting"}:
                raise ValueError(f"findings[{index}].evidence_refs[{ref_index}].role is invalid")
            validate_evidence_ref(ref, f"findings[{index}].evidence_refs[{ref_index}]", indexed_evidence)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"findings validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
