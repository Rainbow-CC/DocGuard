CREATE TABLE IF NOT EXISTS agent_routes (
    agent_route_pk INTEGER PRIMARY KEY,
    route_id TEXT NOT NULL,
    version TEXT NOT NULL,
    agent_definition_pk INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 1 CHECK (is_default IN (0, 1)),
    definition TEXT NOT NULL,
    UNIQUE (route_id, version),
    FOREIGN KEY (agent_definition_pk)
      REFERENCES agent_definitions (agent_definition_pk) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_one_default_route
ON agent_routes (agent_definition_pk)
WHERE enabled = 1 AND is_default = 1;
