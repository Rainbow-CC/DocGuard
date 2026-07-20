#!/usr/bin/env python3
"""Build model-readable audit context and machine-readable evidence from a DOCX extraction."""
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


def image_ids_of(block: dict[str, Any], media: dict[str, dict[str, Any]]) -> list[str]:
    image_ids = list(block.get("image_ids", []))
    for object_id in block.get("embedded_object_ids", []):
        image_ids.extend(media.get(object_id, {}).get("preview_image_ids", []))
    return list(dict.fromkeys(image_ids))


def markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def table_markdown(rows: list[list[Any]]) -> str:
    if not rows:
        return "（空表）"
    width = max(len(row) for row in rows)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(markdown_cell(cell) for cell in padded[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend("| " + " | ".join(markdown_cell(cell) for cell in row) + " |" for row in padded[1:])
    return "\n".join(lines)


def build_evidence(data: dict[str, Any]) -> dict[str, Any]:
    raw_blocks = data.get("blocks", [])
    chapters = [{**chapter, "chapter_number": number} for number, chapter in enumerate(data.get("chapters", []), start=1)]
    media = {item["id"]: item for item in data.get("media", []) if item.get("id")}
    chapter_by_id = {chapter["id"]: chapter for chapter in chapters if chapter.get("id")}
    blocks: list[dict[str, Any]] = []
    for block in raw_blocks:
        item: dict[str, Any] = {"block_index": block["index"], "type": block["type"], "chapter_id": block.get("chapter_id")}
        if block["type"] == "table":
            item["rows"] = block.get("rows", [])
        else:
            item["text"] = block.get("text", "")
            item["style"] = block.get("style")
        if ids := image_ids_of(block, media):
            item["image_ids"] = ids
        blocks.append(item)

    images_by_id: dict[str, dict[str, Any]] = {}
    for position, block in enumerate(raw_blocks):
        image_ids = image_ids_of(block, media)
        if not image_ids:
            continue
        nearby = raw_blocks[max(0, position - 2): position + 3]
        context = "\n".join(text_of(item) for item in nearby if text_of(item))
        matched = [word for word in KEYWORDS if word in context]
        chapter = chapter_by_id.get(block.get("chapter_id"), {})
        for image_id in image_ids:
            if image_id in images_by_id:
                candidate = images_by_id[image_id]
                if block["index"] not in candidate["source_blocks"]:
                    candidate["source_blocks"].append(block["index"])
                candidate["nearby_block_indexes"] = list(dict.fromkeys(candidate["nearby_block_indexes"] + [item["index"] for item in nearby]))
                candidate["nearby_text"] = (candidate["nearby_text"] + f"\n\n【来源 block:{block['index']}】\n{context}")[:2000]
                continue
            image = media.get(image_id, {})
            images_by_id[image_id] = {
                "image_id": image_id,
                "source_blocks": [block["index"]],
                "chapter_id": block.get("chapter_id"),
                "chapter_number": chapter.get("chapter_number"),
                "chapter_title": chapter.get("title"),
                "rendered_png_file": image.get("rendered_png_file"),
                "candidate": True,
                "candidate_reason": f"附近文本命中：{', '.join(matched)}" if matched else "未命中关键词；为避免遗漏，保留为待判定图件。",
                "nearby_block_indexes": [item["index"] for item in nearby],
                "nearby_text": context[:2000],
            }
    return {
        "schema_version": "1.1",
        "source": data.get("source"),
        "revisions": data.get("revisions", {"mode": "unknown"}),
        "warnings": data.get("warnings", []),
        "chapters": chapters,
        "blocks": blocks,
        "candidate_images": list(images_by_id.values()),
        "summary": {"blocks": len(blocks), "chapters": len(chapters), "candidate_images": len(images_by_id)},
    }


def context_markdown(evidence: dict[str, Any]) -> str:
    revisions = evidence["revisions"]
    mode = {"accept": "接受修订后的最终视图", "all": "包含全部修订内容的历史视图"}.get(revisions.get("mode"), "未知")
    lines = [
        "# 文档审核上下文", "",
        "此文件是审核模型的唯一文档读取入口。证据引用使用 `block:<索引>`、`table:<索引>` 和 `image:<ID>`。",
        "不要读取 `document-structure.json`；它仅用于重建本审计包。需要机器可读的完整证据时读取同目录的 `audit-evidence.json`。", "",
        "## 文档概况", "",
        f"- 源文件：`{evidence.get('source', '')}`",
        f"- 修订视图：{mode}；插入修订 {revisions.get('insertions', 0)} 处，删除修订 {revisions.get('deletions', 0)} 处，移出 {revisions.get('moves_from', 0)} 处，移入 {revisions.get('moves_to', 0)} 处。",
        f"- 内容块：{evidence['summary']['blocks']}；章节：{evidence['summary']['chapters']}；最终可见候选图片：{evidence['summary']['candidate_images']}",
    ]
    if warnings := evidence.get("warnings", []):
        lines.extend(["", "## 提取警告", "", *(f"- {warning}" for warning in warnings)])

    lines.extend(["", "## 最终可见图件清单", ""])
    for image in evidence["candidate_images"]:
        chapter = f"第{image['chapter_number']}章：{image['chapter_title']}" if image.get("chapter_number") else "未归属章节"
        lines.extend([
            f"### image:{image['image_id']}", "",
            f"- 所属章节：{chapter}",
            f"- 来源：{', '.join('block:' + str(index) for index in image['source_blocks'])}；PNG：`{image.get('rendered_png_file') or '未生成'}`",
            f"- 候选原因：{image['candidate_reason']}",
            f"- 附近证据块：{', '.join('block:' + str(index) for index in image['nearby_block_indexes']) or '无'}",
            "- 附近文本：", "", image["nearby_text"] or "（无）", "",
        ])

    blocks_by_chapter: dict[str | None, list[dict[str, Any]]] = {}
    for block in evidence["blocks"]:
        blocks_by_chapter.setdefault(block.get("chapter_id"), []).append(block)

    def render_blocks(blocks: list[dict[str, Any]]) -> None:
        for block in blocks:
            index = block["block_index"]
            if block["type"] == "table":
                lines.extend([f"#### table:{index}", "", table_markdown(block["rows"]), ""])
            elif text := block.get("text", ""):
                lines.extend([f"<!-- block:{index} -->", "", text, ""])
            for image_id in block.get("image_ids", []):
                lines.extend([f"- 图件引用：image:{image_id}（来源 block:{index}）", ""])

    if unassigned := blocks_by_chapter.pop(None, []):
        lines.extend(["## 未归属章节内容", ""])
        render_blocks(unassigned)
    for chapter in evidence["chapters"]:
        lines.extend([f"## 第{chapter['chapter_number']}章：{chapter['title']}", "", f"- 章节证据范围：block:{chapter['block_start']} 至 block:{chapter['block_end']}", ""])
        render_blocks(blocks_by_chapter.get(chapter["id"], []))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("structure", type=Path, help="extract_docx_structure.py 生成的内部结构文件")
    parser.add_argument("--evidence-output", type=Path, required=True, help="输出 audit-evidence.json")
    parser.add_argument("--context-output", type=Path, required=True, help="输出 audit-context.md")
    args = parser.parse_args()
    evidence = build_evidence(json.loads(args.structure.read_text(encoding="utf-8")))
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    args.context_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    args.context_output.write_text(context_markdown(evidence), encoding="utf-8")
    print(args.context_output)
    print(args.evidence_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
