set -euo pipefail

SRC="/mnt/c/Code/fromGitHub/DocGuard/doc-audit-integrate-skill/"
DST="$HOME/.openclaw/workspace-audit-runtime/skills/docx-tech-architecture-audit"
BACKUP="$HOME/.openclaw/backups/docx-tech-architecture-audit-$(date +%Y%m%d-%H%M%S)"

test -s "${SRC}SKILL.md"
mkdir -p "$DST"

mkdir -p "$BACKUP"
cp -a "$DST/." "$BACKUP/"

rsync -a --delete \
  --exclude ".openclaw/" \
  --exclude ".idea/" \
  --exclude ".obsidian/" \
  --exclude "__pycache__/" \
  "$SRC" "$DST/"

sha256sum   /mnt/c/Code/fromGitHub/DocGuard/doc-audit-integrate-skill/SKILL.md   "$HOME/.openclaw/workspace-audit-runtime/skills/docx-tech-architecture-audit/SKILL.md"

openclaw gateway restart
