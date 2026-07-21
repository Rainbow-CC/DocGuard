from docguard.domain.models import AuditProfile


DEFAULT_PROFILE = AuditProfile(
    profile_id="technical-audit",
    version="1.0.0",
    required_nodes=["agent_audit", "collect", "merge", "render"],
    report_template="technical_audit_v1",
    evidence_policy="accepted_revision_only",
    prompt_versions={"full_text": 1, "architecture": 1, "merge": 1},
)


class ProfileRegistry:
    """Replace with a versioned PostgreSQL repository in production."""

    def get(self, profile_id: str) -> AuditProfile:
        if profile_id != DEFAULT_PROFILE.profile_id:
            raise KeyError(f"Unknown profile: {profile_id}")
        # Pydantic copy freezes the task's profile snapshot from future registry mutations.
        return DEFAULT_PROFILE.model_copy(deep=True)
