from __future__ import annotations

import sqlite3
from pathlib import Path

from docguard.domain.models import AgentRouteDefinition, AuditAgentDefinition, ReviewTypeDefinition
from docguard.settings import Settings
from docguard.services.sqlite import connect_existing_database


class ReviewTypeRegistry:
    """Versioned review types assembled from reusable agent definitions in SQLite.

    The database schema and initial review types are provisioned by operations
    before the application starts.
    """

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
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
            route_rows = connection.execute(
                """
                SELECT assignment.review_type_id, assignment.review_type_version,
                       route.definition AS route_definition,
                       route.is_default
                FROM review_type_agent_definitions AS assignment
                JOIN agent_routes AS route
                  ON route.agent_definition_pk = assignment.agent_definition_pk
                WHERE route.enabled = 1
                ORDER BY assignment.review_type_id, assignment.review_type_version,
                         route.is_default DESC, route.agent_route_pk
                """
            ).fetchall()
        definitions_by_key: dict[tuple[str, str], ReviewTypeDefinition] = {}
        for row in rows:
            definition = ReviewTypeDefinition.model_validate_json(row["review_definition"])
            key = (definition.review_type_id, definition.version)
            if key not in definitions_by_key:
                definition.agents = []
                definition.agent_routes = []
                definitions_by_key[key] = definition
            if row["agent_definition"] is not None:
                definitions_by_key[key].agents.append(
                    AuditAgentDefinition.model_validate_json(row["agent_definition"])
                )
        for row in route_rows:
            key = (row["review_type_id"], row["review_type_version"])
            definition = definitions_by_key.get(key)
            if definition is None:
                continue
            route = AgentRouteDefinition.model_validate_json(row["route_definition"])
            route.is_default = bool(row["is_default"])
            if route not in definition.agent_routes:
                definition.agent_routes.append(route)
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
                    definition.model_dump_json(exclude={"agents", "agent_routes"}),
                ),
            )
            self._replace_agent_assignments(connection, definition)
            self._register_agent_routes(connection, definition)
        self.reload()

    def register_agent(self, definition: AuditAgentDefinition, *, enabled: bool = True) -> None:
        """Register an immutable Agent definition for later reuse by review types."""
        with self._connect() as connection:
            self._get_or_create_agent_pk(connection, definition, enabled=enabled)

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

    @staticmethod
    def _register_agent_routes(
        connection: sqlite3.Connection, definition: ReviewTypeDefinition
    ) -> None:
        agent_pks = {
            (agent.agent_id, agent.version): ReviewTypeRegistry._get_or_create_agent_pk(
                connection, agent
            )
            for agent in definition.agents
        }
        for route in definition.agent_routes:
            agent_pk = agent_pks.get((route.agent_id, route.agent_version))
            if agent_pk is None:
                raise ValueError(
                    f"Agent route {route.route_id} references an unassigned Agent"
                )
            payload = route.model_dump_json(exclude={"is_default"})
            existing = connection.execute(
                """
                SELECT agent_route_pk, definition
                FROM agent_routes
                WHERE route_id = ? AND version = ?
                """,
                (route.route_id, route.version),
            ).fetchone()
            if existing is not None:
                stored = AgentRouteDefinition.model_validate_json(existing["definition"])
                if stored.model_copy(update={"is_default": False}) != route.model_copy(
                    update={"is_default": False}
                ):
                    raise ValueError(
                        f"Agent route {route.route_id}@{route.version} is immutable"
                    )
                route_pk = existing["agent_route_pk"]
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO agent_routes
                        (route_id, version, agent_definition_pk, enabled, is_default, definition)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (
                        route.route_id,
                        route.version,
                        agent_pk,
                        int(route.is_default),
                        payload,
                    ),
                )
                route_pk = cursor.lastrowid
            if route.is_default:
                connection.execute(
                    """
                    UPDATE agent_routes SET is_default = 0
                    WHERE agent_definition_pk = ? AND agent_route_pk != ?
                    """,
                    (agent_pk, route_pk),
                )
            connection.execute(
                "UPDATE agent_routes SET is_default = ? WHERE agent_route_pk = ?",
                (int(route.is_default), route_pk),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = connect_existing_database(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
