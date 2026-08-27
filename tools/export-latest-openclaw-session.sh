#!/usr/bin/env bash

set -euo pipefail

# Runtime portability notes
# -------------------------
# This file is ordinary Bash and does not directly depend on WSL.  On Windows,
# ``OpenClawActionChainExporter`` launches it through ``wsl.exe`` after turning
# this repository path into a WSL path.  On a native Linux server, update that
# Python launcher to run ``bash`` directly instead of ``wsl.exe``; no copy of
# this script into $HOME is required.
#
# Before running on Linux, verify all of the following:
#   1. ``openclaw`` and ``python3`` are available to the service account.
#      Set OPENCLAW_BIN_DIR when OpenClaw is not installed in ~/.npm-global/bin.
#   2. OPENCLAW_STATE_DIR points at the directory where that account stores
#      OpenClaw state and trajectory exports (defaults to ~/.openclaw).
#   3. DOCGUARD_TRAJECTORY_EXPORT_DIR (or DOCGUARD_TRAJECTORY_EXPORT_ROOT) and
#      DocGuard's result paths point at persistent storage shared with the API.
#   4. The API service account can read the OpenClaw session store and write the
#      result directory.  Docker deployments normally need matching bind mounts.

HOME_DIR="${HOME:?HOME is not set}"
DEST_ROOT="${DOCGUARD_TRAJECTORY_EXPORT_ROOT:-$HOME_DIR/trajectory-export}"
OPENCLAW_BIN_DIR="${OPENCLAW_BIN_DIR:-$HOME_DIR/.npm-global/bin}"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME_DIR/.openclaw}"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"

# DocGuard invokes this script through a non-interactive WSL process, which does
# not load the shell profile that normally adds the user-installed OpenClaw CLI.
# ``OPENCLAW_BIN_DIR`` makes the same assumption configurable for Linux servers.
export PATH="$OPENCLAW_BIN_DIR:$PATH"

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "用法：$0 <task-id> [agent-id [attempt-id]]" >&2
  exit 2
fi

TASK_ID="$1"
AGENT_ID="${2:-}"
ATTEMPT_ID="${3:-}"
if [[ ! "$TASK_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "错误：task ID 只能包含字母、数字、点、下划线和连字符。" >&2
  exit 2
fi
if [ -n "$AGENT_ID" ] && [[ ! "$AGENT_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "错误：agent ID 只能包含字母、数字、点、下划线和连字符。" >&2
  exit 2
fi
if [ -n "$ATTEMPT_ID" ] && [[ ! "$ATTEMPT_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "错误：attempt ID 只能包含字母、数字、点、下划线和连字符。" >&2
  exit 2
fi

if [ -n "${DOCGUARD_TRAJECTORY_EXPORT_DIR:-}" ]; then
  DEST_DIR="$DOCGUARD_TRAJECTORY_EXPORT_DIR"
elif [ -n "$AGENT_ID" ]; then
  DEST_DIR="$DEST_ROOT/$TASK_ID/agents/$AGENT_ID"
else
  DEST_DIR="$DEST_ROOT/$TASK_ID"
fi

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
task_id, agent_id, attempt_id = sys.argv[1:]
if agent_id:
    candidates = []
    if attempt_id:
        candidates.append((
            "task+attempt+agent",
            f":task:{task_id}:attempt:{attempt_id}:agent:{agent_id}",
        ))
    # Compatibility for task sessions created before attempt ID entered the user key.
    candidates.append(("task+agent", f":task:{task_id}:agent:{agent_id}"))
else:
    # Compatibility for pre-platform, single-session tasks.
    candidates = [("task", f":task:{task_id}")]

for match_kind, suffix in candidates:
    matches = [s for s in sessions if s.get("key", "").endswith(suffix)]
    if len(matches) == 1:
        session = matches[0]
        key = session.get("key")
        if not key:
            raise SystemExit(f"session 缺少 key：{task_id}")
        print(key + "\t" + session.get("sessionId", "unknown") + "\t" + match_kind)
        break
    if len(matches) > 1:
        keys = "\n".join(s.get("key", "") for s in matches)
        raise SystemExit(f"{match_kind} 匹配到多个 session，拒绝导出不确定的行动链：\n{keys}")
else:
    identity = f"task={task_id}"
    if agent_id:
        identity += f", agent={agent_id}"
    if attempt_id:
        identity += f", attempt={attempt_id}"
    raise SystemExit(f"没有找到对应的 session：{identity}")' "$TASK_ID" "$AGENT_ID" "$ATTEMPT_ID"
)"

IFS=$'\t' read -r SESSION_KEY SESSION_ID SESSION_MATCH_KIND <<< "$SESSION_INFO"

EXPORT_NAME="openclaw-trajectory-task-${TASK_ID}-${AGENT_ID:-legacy}-${ATTEMPT_ID:-legacy}-${SESSION_ID}-${STAMP}"
RESULT_FILE="$DEST_DIR/export-result.json"

mkdir -p "$DEST_DIR"

echo "正在导出 task：$TASK_ID"
if [ -n "$AGENT_ID" ]; then
  echo "agent id：$AGENT_ID"
fi
if [ -n "$ATTEMPT_ID" ]; then
  echo "attempt id：$ATTEMPT_ID"
fi
echo "session key：$SESSION_KEY"
echo "匹配规则：$SESSION_MATCH_KIND"
openclaw sessions export-trajectory \
  --session-key "$SESSION_KEY" \
  --workspace "$HOME_DIR" \
  --output "$EXPORT_NAME" \
  --json > "$RESULT_FILE"

# OpenClaw's default state directory is ~/.openclaw.  Use OPENCLAW_STATE_DIR on
# a server where the service stores state elsewhere (for example a data volume).
EXPORT_DIR="$OPENCLAW_STATE_DIR/trajectory-exports/$EXPORT_NAME"
if [ ! -d "$EXPORT_DIR" ]; then
  echo "错误：OpenClaw 未生成预期的导出目录：$EXPORT_DIR" >&2
  exit 1
fi

cp -a "$EXPORT_DIR/." "$DEST_DIR/"
python3 - "$DEST_DIR/session-info.json" "$TASK_ID" "$ATTEMPT_ID" "$AGENT_ID" "$SESSION_KEY" "$SESSION_ID" "$SESSION_MATCH_KIND" <<'PY'
import json
import sys
from pathlib import Path

path, task_id, attempt_id, agent_id, session_key, session_id, match_kind = sys.argv[1:]
Path(path).write_text(
    json.dumps(
        {
            "task_id": task_id,
            "attempt_id": attempt_id or None,
            "agent_id": agent_id or None,
            "session_key": session_key,
            "session_id": session_id,
            "match_kind": match_kind,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

echo "导出完成：$DEST_DIR"
echo "session id：$SESSION_ID"
echo "文件列表："
find "$DEST_DIR" -maxdepth 1 -type f -printf "%f\t%s bytes\n" | sort
