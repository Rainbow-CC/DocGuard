from __future__ import annotations

import sqlite3
from pathlib import Path

from docguard.domain.models import AgentBackend, AuditAgentDefinition, AuditProfile, ReviewTypeDefinition
from docguard.settings import Settings


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
    skill_ref="docx-tech-architecture-audit",
    core_contract_version=1,
    rule_pack_ref="technical-architecture/review-rules.md",
    rule_pack_version="1.0.0",
    visual_policy={"enabled": True, "policy_ref": "technical-architecture/visual-policy.yaml"},
    profile=DEFAULT_TECHNICAL_PROFILE,
    agents=[
        AuditAgentDefinition(
            agent_id="content-reviewer",
            version="1.0.0",
            dimension="content",
            agent_backend=AgentBackend.OPENCLAW,
            agent_model_ref="openclaw/audit-runtime",
            skill_ref="docx-tech-architecture-audit",
            rule_pack_ref="technical-architecture/review-rules.md",
            rule_pack_version="1.0.0",
        )
    ],
)


class ReviewTypeRegistry:
    """Versioned review types assembled from reusable agent definitions in SQLite."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self._initialize()
        self._definitions: dict[str, ReviewTypeDefinition] = {}
        self.reload()

    @classmethod
    def from_environment(cls) -> ReviewTypeRegistry:
        return cls(Settings.from_environment().database_path)

    def reload(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT review.definition AS review_definition, agent.definition AS agent_definition
                FROM review_type_definitions AS review
                LEFT JOIN review_type_agent_definitions AS assignment
                  ON assignment.review_type_id = review.review_type_id
                 AND assignment.review_type_version = review.version
                LEFT JOIN agent_definitions AS agent
                  ON agent.agent_definition_pk = assignment.agent_definition_pk
                WHERE review.enabled = 1
                ORDER BY review.review_type_id, review.version, assignment.position
                """
            ).fetchall()
        definitions_by_key: dict[tuple[str, str], ReviewTypeDefinition] = {}
        for row in rows:
            definition = ReviewTypeDefinition.model_validate_json(row["review_definition"])
            key = (definition.review_type_id, definition.version)
            if key not in definitions_by_key:
                definition.agents = []
                definitions_by_key[key] = definition
            if row["agent_definition"] is not None:
                definitions_by_key[key].agents.append(
                    AuditAgentDefinition.model_validate_json(row["agent_definition"])
                )
        definitions = list(definitions_by_key.values())
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
                    definition.model_dump_json(exclude={"agents"}),
                ),
            )
            self._replace_agent_assignments(connection, definition)
        self.reload()

    def register_agent(self, definition: AuditAgentDefinition, *, enabled: bool = True) -> None:
        """Register an immutable Agent definition for later reuse by review types."""
        with self._connect() as connection:
            self._get_or_create_agent_pk(connection, definition, enabled=enabled)

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
                CREATE TABLE IF NOT EXISTS agent_definitions (
                    agent_definition_pk INTEGER PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    definition TEXT NOT NULL,
                    UNIQUE (agent_id, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_type_agent_definitions (
                    review_type_id TEXT NOT NULL,
                    review_type_version TEXT NOT NULL,
                    agent_definition_pk INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (review_type_id, review_type_version, agent_definition_pk),
                    FOREIGN KEY (review_type_id, review_type_version)
                      REFERENCES review_type_definitions (review_type_id, version) ON DELETE CASCADE,
                    FOREIGN KEY (agent_definition_pk)
                      REFERENCES agent_definitions (agent_definition_pk) ON DELETE RESTRICT
                )
                """
            )
            # Migrate legacy JSON-embedded agents before seeding new installations.
            self._migrate_embedded_agents(connection)
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
                    DEFAULT_TECHNICAL_REVIEW_TYPE.model_dump_json(exclude={"agents"}),
                ),
            )
            self._add_default_agent_assignments(connection)

    @staticmethod
    def _replace_agent_assignments(connection: sqlite3.Connection, definition: ReviewTypeDefinition) -> None:
        connection.execute(
            "DELETE FROM review_type_agent_definitions WHERE review_type_id = ? AND review_type_version = ?",
            (definition.review_type_id, definition.version),
        )
        for position, agent in enumerate(definition.agents):
            agent_pk = ReviewTypeRegistry._get_or_create_agent_pk(connection, agent)
            connection.execute(
                """
                INSERT INTO review_type_agent_definitions
                    (review_type_id, review_type_version, agent_definition_pk, position)
                VALUES (?, ?, ?, ?)
                """,
                (definition.review_type_id, definition.version, agent_pk, position),
            )

    @staticmethod
    def _get_or_create_agent_pk(
        connection: sqlite3.Connection, definition: AuditAgentDefinition, *, enabled: bool = True
    ) -> int:
        existing = connection.execute(
            "SELECT agent_definition_pk, definition FROM agent_definitions WHERE agent_id = ? AND version = ?",
            (definition.agent_id, definition.version),
        ).fetchone()
        if existing:
            if AuditAgentDefinition.model_validate_json(existing["definition"]) != definition:
                raise ValueError(
                    f"Agent {definition.agent_id}@{definition.version} is immutable; register a new version"
                )
            return existing["agent_definition_pk"]
        cursor = connection.execute(
            """
            INSERT INTO agent_definitions (agent_id, version, enabled, definition)
            VALUES (?, ?, ?, ?)
            """,
            (definition.agent_id, definition.version, int(enabled), definition.model_dump_json()),
        )
        return int(cursor.lastrowid)

    def _migrate_embedded_agents(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT review_type_id, version, definition FROM review_type_definitions"
        ).fetchall()
        for row in rows:
            assigned = connection.execute(
                """
                SELECT 1 FROM review_type_agent_definitions
                WHERE review_type_id = ? AND review_type_version = ?
                """,
                (row["review_type_id"], row["version"]),
            ).fetchone()
            legacy = ReviewTypeDefinition.model_validate_json(row["definition"])
            if not assigned and legacy.agents:
                self._replace_agent_assignments(connection, legacy)
            connection.execute(
                """
                UPDATE review_type_definitions SET definition = ?
                WHERE review_type_id = ? AND version = ?
                """,
                (
                    legacy.model_dump_json(exclude={"agents"}),
                    legacy.review_type_id,
                    legacy.version,
                ),
            )

    def _add_default_agent_assignments(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            """
            SELECT 1 FROM review_type_agent_definitions
            WHERE review_type_id = ? AND review_type_version = ?
            """,
            (DEFAULT_TECHNICAL_REVIEW_TYPE.review_type_id, DEFAULT_TECHNICAL_REVIEW_TYPE.version),
        ).fetchone()
        if not existing:
            self._replace_agent_assignments(connection, DEFAULT_TECHNICAL_REVIEW_TYPE)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class ProfileRegistry:
    """Compatibility adapter retained for graph-only callers and older integrations."""

    def get(self, profile_id: str) -> AuditProfile | ReviewTypeDefinition:
        if profile_id == DEFAULT_TECHNICAL_REVIEW_TYPE.review_type_id:
            return DEFAULT_TECHNICAL_REVIEW_TYPE.model_copy(deep=True)
        if profile_id != DEFAULT_TECHNICAL_PROFILE.profile_id:
            raise KeyError(f"Unknown profile: {profile_id}")
        return DEFAULT_TECHNICAL_PROFILE.model_copy(deep=True)
