#!/usr/bin/env bash

set -euo pipefail

HOME_DIR="${HOME:?HOME is not set}"
DEST_ROOT="${DOCGUARD_TRAJECTORY_EXPORT_ROOT:-$HOME_DIR/trajectory-export}"
STAMP="$(date +%Y%m%d-%H%M%S)"

# DocGuard invokes this script through a non-interactive WSL process, which does
# not load the shell profile that normally adds the user-installed OpenClaw CLI.
export PATH="$HOME_DIR/.npm-global/bin:$PATH"

if [ "$#" -ne 1 ]; then
  echo "用法：$0 <task-id>" >&2
  exit 2
fi

TASK_ID="$1"
if [[ ! "$TASK_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "错误：task ID 只能包含字母、数字、点、下划线和连字符。" >&2
  exit 2
fi

DEST_DIR="$DEST_ROOT/$TASK_ID"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "错误：找不到 openclaw 命令。" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：找不到 python3 命令。" >&2
  exit 1
fi

SESSION_INFO="$(
  openclaw sessions --all-agents --limit all --json |
    python3 -c 'import json, sys
data = json.load(sys.stdin)
sessions = data.get("sessions", [])
task_id = sys.argv[1]
suffix = ":task:" + task_id
matches = [s for s in sessions if s.get("key", "").endswith(suffix)]
if not matches:
    raise SystemExit(f"没有找到 task ID 对应的 session：{task_id}")
if len(matches) > 1:
    keys = "\n".join(s.get("key", "") for s in matches)
    raise SystemExit(f"task ID 匹配到多个 session：\n{keys}")
session = matches[0]
key = session.get("key")
if not key:
    raise SystemExit(f"task ID 对应的 session 缺少 key：{task_id}")
print(key + "\t" + session.get("sessionId", "unknown"))' "$TASK_ID"
)"

IFS=$'\t' read -r SESSION_KEY SESSION_ID <<< "$SESSION_INFO"

EXPORT_NAME="openclaw-trajectory-task-${TASK_ID}-${SESSION_ID}-${STAMP}"
RESULT_FILE="$DEST_DIR/export-result.json"

mkdir -p "$DEST_DIR"

echo "正在导出 task：$TASK_ID"
echo "session key：$SESSION_KEY"
openclaw sessions export-trajectory \
  --session-key "$SESSION_KEY" \
  --workspace "$HOME_DIR" \
  --output "$EXPORT_NAME" \
  --json > "$RESULT_FILE"

EXPORT_DIR="$HOME_DIR/.openclaw/trajectory-exports/$EXPORT_NAME"
if [ ! -d "$EXPORT_DIR" ]; then
  echo "错误：OpenClaw 未生成预期的导出目录：$EXPORT_DIR" >&2
  exit 1
fi

cp -a "$EXPORT_DIR/." "$DEST_DIR/"

echo "导出完成：$DEST_DIR"
echo "session id：$SESSION_ID"
echo "文件列表："
find "$DEST_DIR" -maxdepth 1 -type f -printf "%f\t%s bytes\n" | sort
