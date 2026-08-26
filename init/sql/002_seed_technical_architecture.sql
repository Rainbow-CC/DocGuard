INSERT OR IGNORE INTO review_type_definitions
    (review_type_id, version, enabled, definition)
VALUES (
    'technical-architecture',
    '1.0.0',
    1,
    '{"review_type_id":"technical-architecture","version":"1.0.0","display_name":"技术架构报告审核","description":"审核技术文档的架构、部署、容量、图文一致性与文档完整性。","skill_ref":"docx-tech-architecture-audit","core_contract_version":1,"rule_pack_ref":"technical-architecture/review-rules.md","rule_pack_version":"1.0.0","visual_policy":{"enabled":true,"policy_ref":"technical-architecture/visual-policy.yaml"},"profile":{"profile_id":"technical-audit","version":"1.0.0","required_nodes":["agent_audit","collect","merge","render"],"report_template":"technical_audit_v1","evidence_policy":"accepted_revision_only","prompt_versions":{"full_text":1,"architecture":1,"merge":1}}}'
);

INSERT OR IGNORE INTO agent_definitions
    (agent_id, version, enabled, definition)
VALUES (
    'content-reviewer',
    '1.0.0',
    1,
    '{"agent_id":"content-reviewer","version":"1.0.0","dimension":"content","scope":null,"agent_backend":"openclaw","agent_model_ref":"openclaw/audit-runtime","skill_ref":"docx-tech-architecture-audit","rule_pack_ref":"technical-architecture/review-rules.md","rule_pack_version":"1.0.0"}'
);

INSERT OR IGNORE INTO review_type_agent_definitions
    (review_type_id, review_type_version, agent_definition_pk, position)
SELECT
    'technical-architecture',
    '1.0.0',
    agent_definition_pk,
    0
FROM agent_definitions
WHERE agent_id = 'content-reviewer' AND version = '1.0.0';
