"""Resolve logical SkillSet identities and build per-run DSH isolation patches."""

from __future__ import annotations

import json
from pathlib import Path

from docguard.domain.models import AuditAgentDefinition


class SkillSetResolutionError(ValueError):
    """Raised when a logical SkillSet cannot be resolved safely."""


class DshSkillSetResolver:
    """Expose one fixed SkillSet without copying it into an AgentRun workspace."""

    PATCH_FILENAME = ".dsh-skill-isolation.cordis.patch.yml"

    def __init__(self, skill_set_root: Path | str) -> None:
        self.skill_set_root = Path(skill_set_root).resolve()

    def resolve(self, agent: AuditAgentDefinition) -> Path:
        assert agent.skill_set_version is not None
        skill_set = (self.skill_set_root / agent.skill_set_ref / agent.skill_set_version).resolve()
        try:
            skill_set.relative_to(self.skill_set_root)
        except ValueError:
            raise SkillSetResolutionError("SkillSet escapes configured root") from None
        if not skill_set.is_dir():
            raise SkillSetResolutionError(
                f"SkillSet does not exist: {agent.skill_set_ref}@{agent.skill_set_version}"
            )
        return skill_set

    def write_isolation_patch(self, agent: AuditAgentDefinition, run_workspace: Path) -> Path:
        skill_set = self.resolve(agent)
        patch = run_workspace / self.PATCH_FILENAME
        # JSON strings are valid YAML scalars and avoid platform-specific path escaping.
        skill_dir = json.dumps(str(skill_set), ensure_ascii=False)
        patch.write_text(
            "\n".join(
                [
                    "- id: skill-filesystem",
                    "  config:",
                    "    includeDefaultRoots: false",
                    "    customSkillDirs:",
                    f"      - {skill_dir}",
                    "    watch: false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return patch
