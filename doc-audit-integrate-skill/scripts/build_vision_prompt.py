#!/usr/bin/env python3
"""Render the vision extraction prompt with its authoritative JSON Schema."""
from __future__ import annotations

import argparse
from pathlib import Path

PLACEHOLDER = "<PASTE architecture-facts.schema.json HERE>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    template = args.template.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError(f"模板必须且只能包含一次占位符：{PLACEHOLDER}")
    prompt = template.replace(PLACEHOLDER, args.schema.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(prompt, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
