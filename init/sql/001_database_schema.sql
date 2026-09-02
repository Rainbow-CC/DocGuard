PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS review_type_definitions (
    review_type_id TEXT NOT NULL,
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    definition TEXT NOT NULL,
    PRIMARY KEY (review_type_id, version)
);

CREATE TABLE IF NOT EXISTS agent_definitions (
    agent_definition_pk INTEGER PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    definition TEXT NOT NULL,
    UNIQUE (agent_id, version)
);

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
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_review_type_one_active
ON review_type_definitions (review_type_id)
WHERE enabled = 1;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_audit_tasks_created_at ON audit_tasks (created_at DESC);

CREATE TABLE IF NOT EXISTS vision_response_cache (
    cache_key TEXT PRIMARY KEY,
    image_sha256 TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    model TEXT NOT NULL,
    raw_response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
