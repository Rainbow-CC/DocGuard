from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from docguard.domain.models import AgentBackend, AuditProfile, ReviewTypeDefinition


DEFAULT_TECHNICAL_PROFILE = AuditProfile(
    profile_id="technical-audit",
    version="1.0.0",
    required_nodes=["agent_audit", "collect", "merge", "render"],
    report_template="technical_audit_v1",
    evidence_policy="accepted_revision_only",
    prompt_versions={"full_text": 1, "architecture": 1, "merge": 1},
)

DEFAULT_TECHNICAL_REVIEW_TYPE = ReviewTypeDefinition(
    review_type_id="technical-architecture",
    version="1.0.0",
    display_name="技术架构报告审核",
    description="审核技术文档的架构、部署、容量、图文一致性与文档完整性。",
    agent_backend=AgentBackend.DSH,
    agent_model_ref="openclaw/audit-runtime",
    skill_ref="docx-tech-architecture-audit",
    core_contract_version=1,
    rule_pack_ref="technical-architecture/review-rules.md",
    rule_pack_version="1.0.0",
    visual_policy={"enabled": True, "policy_ref": "technical-architecture/visual-policy.yaml"},
    profile=DEFAULT_TECHNICAL_PROFILE,
)


class ReviewTypeRegistry:
    """Versioned review type metadata loaded from the platform database at startup."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self._initialize()
        self._definitions: dict[str, ReviewTypeDefinition] = {}
        self.reload()

    @classmethod
    def from_environment(cls) -> ReviewTypeRegistry:
        from docguard.services.store import SQLiteTaskStore

        return cls(os.getenv("DOCGUARD_DATABASE_PATH", str(SQLiteTaskStore.DEFAULT_DATABASE_PATH)))

    def reload(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT definition FROM review_type_definitions WHERE enabled = 1 ORDER BY review_type_id, version"
            ).fetchall()
        definitions = [ReviewTypeDefinition.model_validate_json(row["definition"]) for row in rows]
        self._definitions = {definition.review_type_id: definition for definition in definitions}

    def get(self, review_type_id: str) -> ReviewTypeDefinition:
        try:
            return self._definitions[review_type_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(f"Unknown or disabled review type: {review_type_id}") from exc

    def list(self) -> list[ReviewTypeDefinition]:
        return [definition.model_copy(deep=True) for definition in self._definitions.values()]

    def register(self, definition: ReviewTypeDefinition, *, enabled: bool = True) -> None:
        """Install a new immutable version and make it the active version when requested."""
        with self._connect() as connection:
            if enabled:
                connection.execute(
                    "UPDATE review_type_definitions SET enabled = 0 WHERE review_type_id = ?",
                    (definition.review_type_id,),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO review_type_definitions
                    (review_type_id, version, enabled, definition)
                VALUES (?, ?, ?, ?)
                """,
                (
                    definition.review_type_id,
                    definition.version,
                    int(enabled),
                    definition.model_dump_json(),
                ),
            )
        self.reload()

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_type_definitions (
                    review_type_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    definition TEXT NOT NULL,
                    PRIMARY KEY (review_type_id, version)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_review_type_one_active
                ON review_type_definitions (review_type_id)
                WHERE enabled = 1
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO review_type_definitions
                    (review_type_id, version, enabled, definition)
                VALUES (?, ?, 1, ?)
                """,
                (
                    DEFAULT_TECHNICAL_REVIEW_TYPE.review_type_id,
                    DEFAULT_TECHNICAL_REVIEW_TYPE.version,
                    DEFAULT_TECHNICAL_REVIEW_TYPE.model_dump_json(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection


class ProfileRegistry:
    """Compatibility adapter retained for graph-only callers and older integrations."""

    def get(self, profile_id: str) -> AuditProfile | ReviewTypeDefinition:
        if profile_id == DEFAULT_TECHNICAL_REVIEW_TYPE.review_type_id:
            return DEFAULT_TECHNICAL_REVIEW_TYPE.model_copy(deep=True)
        if profile_id != DEFAULT_TECHNICAL_PROFILE.profile_id:
            raise KeyError(f"Unknown profile: {profile_id}")
        return DEFAULT_TECHNICAL_PROFILE.model_copy(deep=True)
