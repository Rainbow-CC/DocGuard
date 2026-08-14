#!/usr/bin/env bash
set -euo pipefail

input_docx="${1:?INPUT_DOCX is required}"
attempt_dir="${2:?ATTEMPT_DIR is required}"
skill_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

printf 'DocGuard preprocess parameters: input_docx=<%s> skill_root=<%s> attempt_dir=<%s>\n' \
  "$input_docx" "$skill_root" "$attempt_dir" >&2

require_file() {
  if [[ ! -s "$1" ]]; then
    echo "Required file is missing or empty: $1" >&2
    exit 2
  fi
}

require_file "$input_docx"
[[ -d "$skill_root" ]] || { echo "Required directory is missing: $skill_root" >&2; exit 2; }

work="$attempt_dir/work"
mkdir -p "$work/vision-responses" "$attempt_dir/evidence"
python3 "$skill_root/scripts/extract_docx_structure.py" "$input_docx" --output "$work/extracted" --render-png --revision-mode accept
python3 "$skill_root/scripts/build_audit_packet.py" "$work/extracted/document-structure.json" --context-output "$work/audit-context.md" --evidence-output "$work/audit-evidence.json"
python3 "$skill_root/scripts/build_vision_prompt.py" --template "$skill_root/review-packs/technical-architecture/vision-prompt.md" --schema "$skill_root/review-packs/technical-architecture/vision-facts.schema.json" --output "$work/vision-prompt.txt"
cp "$work/audit-evidence.json" "$attempt_dir/evidence/audit-evidence.json"
cp -a "$work/extracted/rendered" "$attempt_dir/evidence/rendered"
