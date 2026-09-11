import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docguard.domain.models import AuditAgentDefinition
from docguard.services.skill_sets import DshSkillSetResolver, SkillSetResolutionError


def _agent(**overrides: object) -> AuditAgentDefinition:
    values: dict[str, object] = {
        "agent_id": "content-reviewer",
        "version": "1.0.0",
        "dimension": "content",
        "skill_set_ref": "technical-architecture/content-reviewer",
        "skill_set_version": "1.0.0",
        "rule_pack_ref": "technical-architecture/review-rules.md",
        "rule_pack_version": "1.0.0",
    }
    values.update(overrides)
    return AuditAgentDefinition(**values)


def test_writes_isolated_dsh_patch_without_copying_skills(tmp_path: Path) -> None:
    skill_set_root = tmp_path / "skill-sets"
    skill_set = skill_set_root / "technical-architecture" / "content-reviewer" / "1.0.0"
    skill = skill_set / "content-audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: content-audit\ndescription: audit\n---", encoding="utf-8")
    run_workspace = tmp_path / "runs" / "content"
    run_workspace.mkdir(parents=True)

    patch = DshSkillSetResolver(skill_set_root).write_isolation_patch(_agent(), run_workspace)
    content = patch.read_text(encoding="utf-8")

    assert "includeDefaultRoots: false" in content
    assert "watch: false" in content
    assert json.dumps(str(skill_set.resolve())) in content
    assert not (run_workspace / "content-audit").exists()


def test_rejects_missing_skill_set(tmp_path: Path) -> None:
    with pytest.raises(SkillSetResolutionError, match="does not exist"):
        DshSkillSetResolver(tmp_path / "skill-sets").write_isolation_patch(
            _agent(), tmp_path
        )


@pytest.mark.parametrize("skill_set_ref", ["../outside", "/absolute", "C:/absolute"])
def test_agent_definition_rejects_non_portable_skill_set_refs(skill_set_ref: str) -> None:
    with pytest.raises(ValidationError, match="safe relative path"):
        _agent(skill_set_ref=skill_set_ref)


def test_legacy_backend_fields_are_not_retained_by_agent_definition() -> None:
    agent = _agent(agent_backend="openclaw", agent_model_ref="openclaw/audit-runtime")

    assert "agent_backend" not in agent.model_dump()
    assert "agent_model_ref" not in agent.model_dump()
