#!/usr/bin/env python3
"""Create one reusable audit index from document-structure.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

KEYWORDS = ("架构", "部署", "网络", "拓扑", "数据流", "流程", "组件", "环境", "高可用", "安全")


def text_of(block: dict[str, Any]) -> str:
    if block.get("type") == "table":
        return "\n".join(" | ".join(str(cell) for cell in row) for row in block.get("rows", []))
    return str(block.get("text", "")).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("structure", type=Path, help="extract_docx_structure.py 生成的 document-structure.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.structure.read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    media = {item["id"]: item for item in data.get("media", [])}
    chapters: dict[str, dict[str, Any]] = {}
    for chapter in data.get("chapters", []):
        chapters[chapter["id"]] = {**chapter, "content": []}
    for block in blocks:
        chapter = chapters.get(block.get("chapter_id"))
        if chapter and (content := text_of(block)):
            chapter["content"].append({"block_index": block["index"], "type": block["type"], "content": content})

    images: list[dict[str, Any]] = []
    for position, block in enumerate(blocks):
        image_ids = list(block.get("image_ids", []))
        for object_id in block.get("embedded_object_ids", []):
            image_ids.extend(media.get(object_id, {}).get("preview_image_ids", []))
        if not image_ids:
            continue
        context = "\n".join(text_of(item) for item in blocks[max(0, position - 2): position + 3] if text_of(item))
        matched = [word for word in KEYWORDS if word in context]
        for image_id in dict.fromkeys(image_ids):
            item = media.get(image_id, {})
            images.append({
                "image_id": image_id,
                "source_block": block["index"],
                "chapter_id": block.get("chapter_id"),
                "rendered_png_file": item.get("rendered_png_file"),
                "candidate": True,
                "candidate_reason": f"附近文本命中：{', '.join(matched)}" if matched else "未命中关键词；为避免遗漏，保留为待判定图件。",
                "nearby_text": context[:2000],
            })

    output = {
        "source": data.get("source"),
        "warnings": data.get("warnings", []),
        "chapters": list(chapters.values()),
        "candidate_images": images,
        "summary": {"blocks": len(blocks), "chapters": len(chapters), "candidate_images": len(images)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
