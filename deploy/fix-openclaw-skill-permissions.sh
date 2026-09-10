#!/usr/bin/env bash
set -euo pipefail

state_home="${1:-${OPENCLAW_STATE_HOME:-}}"
if [[ -z "$state_home" || "$state_home" != /* ]]; then
  echo "usage: $0 /absolute/path/to/openclaw-state" >&2
  exit 2
fi

skill_roots=(
  "$state_home/workspace-audit-runtime/skills/docx-tech-architecture-audit"
  "$state_home/workspace/tech-audit-structure-reviewer/skills/docx-tech-format-audit"
)

for skill_root in "${skill_roots[@]}"; do
  if [[ ! -f "$skill_root/SKILL.md" ]]; then
    echo "missing installed skill: $skill_root/SKILL.md" >&2
    exit 1
  fi

  # OpenClaw installs the skill root as 0700 for the Gateway uid. Sandboxes run
  # as DocGuard uid 10001, so every directory must be traversable and every
  # contract/rule/script file must be readable. This does not grant write access.
  find "$skill_root" -type d -exec chmod 0755 {} +
  find "$skill_root" -type f -exec chmod a+r {} +
done

echo "OpenClaw skill trees are sandbox-readable:"
printf '  %s\n' "${skill_roots[@]}"
