#!/usr/bin/env python3
"""Extract tool calls from an OpenClaw trajectory export into Markdown.

The input is normally JSONL.  For convenience, this script also accepts
whitespace-separated JSON objects, including pretty-printed objects.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def load_objects(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a JSONL or whitespace-separated JSON file."""
    text = path.read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    position = 0

    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return
        try:
            value, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析 {path}，位置 {position}: {exc}") from exc
        if isinstance(value, dict):
            yield value


def parse_arguments(value: Any) -> Any:
    """Decode arguments when an exporter stores them as a JSON string."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def format_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).astimezone().isoformat(
            timespec="seconds"
        )
    return str(value)


def collect_content_items(objects: Iterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one normalized output item for every message content element."""
    output: list[dict[str, Any]] = []
    for line_number, event in enumerate(objects, start=1):
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        message = data.get("message")
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        for content_index, item in enumerate(content):
            if not isinstance(item, dict):
                item = {"type": "unknown", "value": item}

            arguments = parse_arguments(item.get("arguments", {}))
            record: dict[str, Any] = {
                "event_line": line_number,
                "event_seq": event.get("seq"),
                "timestamp": format_timestamp(event.get("ts")),
                "content_index": content_index,
                "call_id": item.get("id"),
                "content_type": item.get("type", "unknown"),
                "content": item,
                "text": item.get("text"),
                "thinking": item.get("thinking"),
                "tool_name": item.get("name") or item.get("toolName"),
                "arguments": arguments if item.get("type") == "toolCall" else None,
            }
            if record["content_type"] == "toolCall" and record["tool_name"] is None:
                record["tool_name"] = "(未命名工具)"
            if record["tool_name"] == "exec" and isinstance(arguments, dict):
                command = arguments.get("command")
                if command is not None:
                    record["command"] = command
            output.append(record)
    return output


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def markdown_code(value: Any, language: str = "text") -> str:
    text = value if isinstance(value, str) else json_text(value)
    fence = "`````" if "```" in text else "```"
    return f"{fence}{language}\n{text}\n{fence}"


def find_input_file(input_dir: Path) -> Path:
    """Find the trajectory JSONL file inside the input directory."""
    candidates = [
        input_dir / "trajectory-export" / "events.jsonl",
        input_dir / "events.jsonl",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    jsonl_files = sorted(input_dir.rglob("*.jsonl"))
    if len(jsonl_files) == 1:
        return jsonl_files[0]
    if not jsonl_files:
        raise FileNotFoundError(f"输入文件夹中未找到 JSONL 文件：{input_dir}")
    files = "、".join(str(path.relative_to(input_dir)) for path in jsonl_files)
    raise ValueError(f"输入文件夹中找到多个 JSONL 文件，请保留 trajectory-export/events.jsonl：{files}")


def render_markdown(
    records: list[dict[str, Any]], input_path: Path, title: str
) -> str:
    type_counts: dict[str, int] = {}
    for record in records:
        kind = record["content_type"]
        type_counts[kind] = type_counts.get(kind, 0) + 1

    lines = [
        f"# {title}",
        "",
        f"- 输入文件：`{input_path}`",
        f"- 内容元素数：{len(records)}",
        "- 类型统计："
        + "；".join(f"`{kind}` {count} 个" for kind, count in type_counts.items()),
        "",
    ]
    if not records:
        lines.append("未找到 `data.message.content` 中的内容元素。")
        return "\n".join(lines) + "\n"

    lines += ["## 内容列表", ""]
    for index, record in enumerate(records, start=1):
        title = f"### {index}. `{record['content_type']}`"
        lines += [title, "", f"- 事件行号：{record['event_line']}"]
        if record.get("event_seq") is not None:
            lines.append(f"- 事件序号：{record['event_seq']}")
        if record.get("timestamp"):
            lines.append(f"- 时间：`{record['timestamp']}`")
        if record.get("call_id"):
            lines.append(f"- 调用 ID：`{record['call_id']}`")
        if record["content_type"] == "toolCall" and record.get("tool_name"):
            lines.append(f"- 工具名称：`{record['tool_name']}`")
        if record["content_type"] == "text" and record.get("text") is not None:
            lines += ["", markdown_code(record["text"])]
        elif record["content_type"] == "thinking" and record.get("thinking") is not None:
            lines += ["", markdown_code(record["thinking"])]
        elif record["content_type"] == "image":
            lines += ["", "**image**", "", markdown_code(record["content"], "json")]
        elif "command" in record:
            lines += ["", "**command**", "", markdown_code(record["command"], "bash")]
        if record["content_type"] == "toolCall":
            lines += ["", "**arguments**", "", markdown_code(record["arguments"], "json")]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从输入文件夹中的 trajectory JSONL 提取完整消息内容和 toolCall，并生成两个 Markdown 文件"
    )
    parser.add_argument("input", type=Path, help="输入文件夹，默认读取其中的 trajectory-export/events.jsonl")
    args = parser.parse_args()

    input_dir = args.input
    if not input_dir.is_dir():
        parser.error(f"输入路径不是文件夹：{input_dir}")
    input_file = find_input_file(input_dir)
    records = collect_content_items(load_objects(input_file))
    full_output = input_dir / "output.md"
    tool_call_output = input_dir / "output-toolCall.md"
    tool_calls = [record for record in records if record["content_type"] == "toolCall"]

    full_output.write_text(
        render_markdown(records, input_file, "消息内容明细"), encoding="utf-8"
    )
    tool_call_output.write_text(
        render_markdown(tool_calls, input_file, "Tool Calls"), encoding="utf-8"
    )
    print(f"已提取 {len(records)} 个消息内容元素，写入：{full_output}")
    print(f"已提取 {len(tool_calls)} 个 toolCall，写入：{tool_call_output}")


if __name__ == "__main__":
    main()
