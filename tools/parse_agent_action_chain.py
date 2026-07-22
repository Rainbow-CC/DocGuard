#!/usr/bin/env python3
"""Parse an OpenClaw session export into a readable agent action chain.

The input uses a JSONL filename, but the export may contain pretty-printed
JSON objects spanning multiple lines. This parser supports both forms.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def load_objects(path: Path) -> Iterator[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        try:
            value, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析 {path}，位置 {pos}: {exc}") from exc
        if isinstance(value, dict):
            yield value


def compact(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原长度 {len(text)} 字符）"


def timestamp(obj: dict[str, Any]) -> str:
    raw = obj.get("timestamp") or obj.get("message", {}).get("timestamp")
    if not raw:
        return ""
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000).astimezone().strftime("%H:%M:%S")
    return str(raw).replace("T", " ").replace("Z", "")[:19]


def render_content(content: Any, full: bool, result_by_call: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if isinstance(content, str):
        return [compact(content, 4000 if full else 800)]
    if not isinstance(content, list):
        return [compact(content, 4000 if full else 800)]

    for item in content:
        kind = item.get("type")
        if kind == "thinking":
            lines.append(f"思考：{compact(item.get('thinking', ''), 4000 if full else 500)}")
        elif kind in {"toolCall", "tool_use"} or item.get("name"):
            call_id = item.get("id", "")
            name = item.get("name") or item.get("toolName", "unknown")
            args = item.get("arguments", item.get("input", {}))
            lines.append(f"调用工具 `{name}`" + (f"（{call_id}）" if call_id else "") + "：")
            lines.append("```json\n" + compact(args, 12000 if full else 1600) + "\n```")
            result = result_by_call.get(call_id)
            if result:
                result_text = result.get("message", {}).get("content", result)
                lines.append("返回结果：\n```text\n" + compact(result_text, 16000 if full else 1800) + "\n```")
        elif kind == "text":
            lines.append(f"回复：{compact(item.get('text', ''), 4000 if full else 1200)}")
    return lines


def build_chain(objects: list[dict[str, Any]], full: bool) -> str:
    results = {}
    for obj in objects:
        msg = obj.get("message", {})
        if msg.get("role") == "toolResult":
            results[msg.get("toolCallId", "")] = obj

    out = ["# Agent 行动链", ""]
    session = next((o for o in objects if o.get("type") == "session"), {})
    out += [f"- Session ID：`{session.get('id', 'unknown')}`", f"- 工作目录：`{session.get('cwd', 'unknown')}`", f"- 记录对象数：{len(objects)}", ""]
    step = 0
    for obj in objects:
        kind = obj.get("type")
        msg = obj.get("message", {})
        role = msg.get("role")
        if kind == "session":
            out += ["## 会话开始", f"`{timestamp(obj)}` session `{obj.get('id', '')}`", ""]
        elif kind in {"model_change", "thinking_level_change", "custom"}:
            out += [f"## 运行配置：`{kind}`", f"`{timestamp(obj)}`", "```json", compact(obj, 3000), "```", ""]
        elif kind == "message" and role in {"user", "assistant"}:
            step += 1
            label = "用户输入" if role == "user" else "Agent 行动"
            out += [f"## {step}. {label}", f"`{timestamp(obj)}`", *render_content(msg.get("content"), full, results), ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="将 OpenClaw 会话解析为可读的 Agent 行动链")
    parser.add_argument("input", type=Path, help="会话 JSONL/JSON 导出文件")
    parser.add_argument("-o", "--output", type=Path, help="输出 Markdown 文件；不指定则输出到终端")
    parser.add_argument("--full", action="store_true", help="尽量保留完整思考、参数和工具返回结果")
    args = parser.parse_args()
    result = build_chain(list(load_objects(args.input)), args.full)
    if args.output:
        args.output.write_text(result, encoding="utf-8")
        print(f"已写入：{args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
