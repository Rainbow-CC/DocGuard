INSERT OR IGNORE INTO projects
    (project_id, name, description, owner, status, created_at, updated_at)
VALUES (
    'default',
    'default',
    '历史审核记录及未指定项目的默认归属。',
    NULL,
    'active',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);
