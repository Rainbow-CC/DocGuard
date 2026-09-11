"""Provision a DocGuard SQLite database from the operations-owned init directory."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


SQL_DIRECTORY = Path(__file__).with_name("sql")
DEFAULT_PROJECT_ID = "default"


def provision_database(database_path: Path | str) -> list[str]:
    """Create or upgrade a database using the checked-in operations scripts."""

    path = Path(database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    scripts = sorted(SQL_DIRECTORY.glob("*.sql"))
    if not scripts:
        raise RuntimeError(f"No SQL initialization scripts found in {SQL_DIRECTORY}")

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        for script in scripts:
            connection.executescript(script.read_text(encoding="utf-8"))
        _upgrade_legacy_embedded_agents(connection)
        _upgrade_agent_routes(connection)
        _upgrade_audit_task_projects(connection)
    return [script.name for script in scripts]


def _upgrade_legacy_embedded_agents(connection: sqlite3.Connection) -> None:
    """Move pre-agent-table JSON definitions during an operations-led upgrade."""

    rows = connection.execute(
        "SELECT review_type_id, version, definition FROM review_type_definitions"
    ).fetchall()
    for row in rows:
        try:
            definition = json.loads(row["definition"])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON for review type {row['review_type_id']}@{row['version']}"
            ) from exc
        agents = definition.pop("agents", [])
        if not agents:
            continue
        if not isinstance(agents, list):
            raise RuntimeError(
                f"Invalid agents list for review type {row['review_type_id']}@{row['version']}"
            )
        assigned = connection.execute(
            """
            SELECT 1 FROM review_type_agent_definitions
            WHERE review_type_id = ? AND review_type_version = ?
            """,
            (row["review_type_id"], row["version"]),
        ).fetchone()
        if not assigned:
            _replace_agent_assignments(connection, row["review_type_id"], row["version"], agents)
        connection.execute(
            """
            UPDATE review_type_definitions SET definition = ?
            WHERE review_type_id = ? AND version = ?
            """,
            (
                json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                row["review_type_id"],
                row["version"],
            ),
        )


def _upgrade_audit_task_projects(connection: sqlite3.Connection) -> None:
    """Associate pre-project task rows with the reserved default project.

    SQLite cannot add a non-null foreign-key column with a non-null default while
    foreign keys are enabled.  The initial schema has the full constraint for new
    installations; this operation upgrades old installations by adding the same
    required column and trigger-backed reference validation.
    """

    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(audit_tasks)").fetchall()
    }
    if "project_id" not in columns:
        connection.execute(
            "ALTER TABLE audit_tasks ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'"
        )
    connection.execute(
        """
        UPDATE audit_tasks
        SET project_id = ?
        WHERE project_id IS NULL OR trim(project_id) = ''
        """,
        (DEFAULT_PROJECT_ID,),
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_tasks_project_id ON audit_tasks (project_id)"
    )
    connection.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS audit_tasks_project_must_exist_on_insert
        BEFORE INSERT ON audit_tasks
        FOR EACH ROW
        WHEN NEW.project_id IS NULL
          OR trim(NEW.project_id) = ''
          OR NOT EXISTS (SELECT 1 FROM projects WHERE project_id = NEW.project_id)
        BEGIN
            SELECT RAISE(ABORT, 'audit_tasks.project_id must reference an existing project');
        END;

        CREATE TRIGGER IF NOT EXISTS audit_tasks_project_must_exist_on_update
        BEFORE UPDATE OF project_id ON audit_tasks
        FOR EACH ROW
        WHEN NEW.project_id IS NULL
          OR trim(NEW.project_id) = ''
          OR NOT EXISTS (SELECT 1 FROM projects WHERE project_id = NEW.project_id)
        BEGIN
            SELECT RAISE(ABORT, 'audit_tasks.project_id must reference an existing project');
        END;

        CREATE TRIGGER IF NOT EXISTS projects_with_audit_tasks_cannot_be_deleted
        BEFORE DELETE ON projects
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM audit_tasks WHERE project_id = OLD.project_id)
        BEGIN
            SELECT RAISE(ABORT, 'projects referenced by audit tasks cannot be deleted');
        END;
        """
    )


def _upgrade_agent_routes(connection: sqlite3.Connection) -> None:
    """Normalize logical Agents and attach backend-neutral execution routes."""

    rows = connection.execute(
        "SELECT agent_definition_pk, agent_id, version, definition FROM agent_definitions"
    ).fetchall()
    for row in rows:
        definition = json.loads(row["definition"])
        definition.pop("agent_model_ref", None)
        definition.pop("agent_backend", None)
        definition.pop("workspace_ref", None)
        definition.pop("workspace_version", None)
        if "skill_ref" in definition and "skill_set_ref" not in definition:
            definition["skill_set_ref"] = definition.pop("skill_ref")
        definition.setdefault("skill_set_version", definition["version"])
        connection.execute(
            "UPDATE agent_definitions SET definition = ? WHERE agent_definition_pk = ?",
            (
                json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                row["agent_definition_pk"],
            ),
        )
        route_id = f"technical-audit/{row['agent_id']}"
        payload = {
            "route_id": route_id,
            "version": row["version"],
            "agent_id": row["agent_id"],
            "agent_version": row["version"],
            "is_default": True,
        }
        connection.execute(
            """
            INSERT INTO agent_routes
                (route_id, version, agent_definition_pk, enabled, is_default, definition)
            VALUES (?, ?, ?, 1, 1, ?)
            ON CONFLICT(route_id, version) DO UPDATE SET is_default = 1
            """,
            (
                route_id,
                row["version"],
                row["agent_definition_pk"],
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    review_rows = connection.execute(
        "SELECT review_type_id, version, definition FROM review_type_definitions"
    ).fetchall()
    for row in review_rows:
        definition = json.loads(row["definition"])
        removed_runtime = definition.pop("runtime_bindings", None)
        removed_routes = definition.pop("agent_routes", None)
        if removed_runtime is not None or removed_routes is not None:
            connection.execute(
                """
                UPDATE review_type_definitions SET definition = ?
                WHERE review_type_id = ? AND version = ?
                """,
                (
                    json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                    row["review_type_id"],
                    row["version"],
                ),
            )
    connection.execute("DROP TABLE IF EXISTS agent_runtime_bindings")


def _replace_agent_assignments(
    connection: sqlite3.Connection, review_type_id: str, review_type_version: str, agents: list[object]
) -> None:
    for position, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise RuntimeError(f"Agent #{position} for {review_type_id}@{review_type_version} is not an object")
        try:
            agent_id = agent["agent_id"]
            version = agent["version"]
        except KeyError as exc:
            raise RuntimeError(
                f"Agent #{position} for {review_type_id}@{review_type_version} is missing {exc.args[0]}"
            ) from exc
        definition = json.dumps(agent, ensure_ascii=False, separators=(",", ":"))
        existing = connection.execute(
            "SELECT agent_definition_pk, definition FROM agent_definitions WHERE agent_id = ? AND version = ?",
            (agent_id, version),
        ).fetchone()
        if existing is not None:
            if not _agent_definitions_equal(json.loads(existing["definition"]), agent):
                raise RuntimeError(f"Agent {agent_id}@{version} conflicts with the stored definition")
            agent_definition_pk = existing["agent_definition_pk"]
        else:
            cursor = connection.execute(
                """
                INSERT INTO agent_definitions (agent_id, version, enabled, definition)
                VALUES (?, ?, 1, ?)
                """,
                (agent_id, version, definition),
            )
            agent_definition_pk = cursor.lastrowid
        connection.execute(
            """
            INSERT INTO review_type_agent_definitions
                (review_type_id, review_type_version, agent_definition_pk, position)
            VALUES (?, ?, ?, ?)
            """,
            (review_type_id, review_type_version, agent_definition_pk, position),
        )


def _agent_definitions_equal(stored: object, incoming: object) -> bool:
    """Compare legacy backend-coupled and current neutral Agent definitions."""
    if not isinstance(stored, dict) or not isinstance(incoming, dict):
        return stored == incoming
    def normalize(value: dict[str, object]) -> dict[str, object]:
        result = dict(value)
        result.pop("agent_backend", None)
        result.pop("agent_model_ref", None)
        result.pop("workspace_ref", None)
        result.pop("workspace_version", None)
        if "skill_ref" in result and "skill_set_ref" not in result:
            result["skill_set_ref"] = result.pop("skill_ref")
        result.setdefault("skill_set_version", result.get("version"))
        return {key: item for key, item in result.items() if item is not None}

    normalized_stored = normalize(stored)
    normalized_incoming = normalize(incoming)
    return normalized_stored == normalized_incoming


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a DocGuard SQLite database")
    parser.add_argument("--database-path", type=Path, required=True)
    arguments = parser.parse_args()
    scripts = provision_database(arguments.database_path)
    print(f"Provisioned {arguments.database_path.resolve()} using: {', '.join(scripts)}")


if __name__ == "__main__":
    main()
