#!/bin/sh
set -eu

dsh_home="${DOCGUARD_DSH_HOME:-/var/lib/docguard/dsh}"
skills_dir="${dsh_home}/skills"
export DSH_HOME="$dsh_home"

mkdir -p "$skills_dir" "${HOME:-${dsh_home}/home}" "${XDG_CACHE_HOME:-${dsh_home}/cache}"
cp /opt/docguard-dsh-config/settings.yaml "${dsh_home}/settings.yaml"
ln -sfn /app/doc-audit-integrate-skill \
  "$skills_dir/docx-tech-architecture-audit"
ln -sfn /app/tect-doc-structure-audit-skill \
  "$skills_dir/docx-tech-format-audit"

exec "$@"
