#!/bin/sh
set -eu

dsh_home="${DOCGUARD_DSH_HOME:-/var/lib/docguard/dsh}"
skills_dir="${dsh_home}/skills"

mkdir -p "$skills_dir"
ln -sfn /app/doc-audit-integrate-skill \
  "$skills_dir/docx-tech-architecture-audit"
ln -sfn /app/tect-doc-structure-audit-skill \
  "$skills_dir/docx-tech-format-audit"

exec "$@"
