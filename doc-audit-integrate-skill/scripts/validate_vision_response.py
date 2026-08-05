#!/usr/bin/env python3
"""Extract and strictly validate a provider-neutral vision response."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema


def fail(message: str, error_output: Path | None) -> int:
    if error_output:
        error_output.parent.mkdir(parents=True, exist_ok=True)
        error_output.write_text(message + "\n", encoding="utf-8")
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--error-output", type=Path)
    args = parser.parse_args()
    try:
        outer = json.loads(args.raw.read_text(encoding="utf-8"))
        if not isinstance(outer, dict):
            return fail("视觉响应必须是 JSON 对象。", args.error_output)
        # Some adapters wrap facts in content. Accept direct facts as well so an
        # adapter that already returns structured JSON remains usable.
        content = outer.get("content")
        if isinstance(content, (str, dict)):
            facts = json.loads(content) if isinstance(content, str) else content
        elif {"diagram_type", "components", "connections", "zones", "uncertainties"} <= set(outer):
            facts = outer
        else:
            return fail("外层响应缺少可解析的 content，且自身不是事实 JSON。", args.error_output)
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        jsonschema.validate(facts, schema)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
        return fail(f"视觉事实校验失败：{exc}", args.error_output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
