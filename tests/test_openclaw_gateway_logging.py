import json
import logging

from docguard.adapters import agents
from docguard.domain.models import (
    AgentBackend,
    AgentRun,
    AgentRuntimeBinding,
    AuditAgentDefinition,
    AuditAttempt,
    AuditProfile,
    AuditTask,
    InputDocument,
    ReviewTypeDefinition,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(
            [
                "event: response.created",
                'data: {"response":{"id":"resp_example"}}',
                "",
            ]
        )


class _Client:
    sent_request: dict[str, object] | None = None

    def __init__(self, *, timeout) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def stream(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, object]) -> _Response:
        self.sent_request = {
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
        }
        type(self).sent_request = self.sent_request
        return _Response()


def _audit_run() -> tuple[AuditTask, AuditAttempt, AgentRun]:
    agent = AuditAgentDefinition(
        agent_id="content-reviewer",
        version="1.0.0",
        dimension="content",
        agent_model_ref="openclaw/audit-runtime",
        skill_ref="docx-tech-architecture-audit",
        rule_pack_ref="technical-architecture/review-rules.md",
        rule_pack_version="1.0.0",
    )
    profile = AuditProfile(
        profile_id="technical-audit",
        version="1.0.0",
        required_nodes=[],
        report_template="default",
        evidence_policy="accepted_revision_only",
        prompt_versions={},
    )
    review_type = ReviewTypeDefinition(
        review_type_id="technical-architecture",
        version="1.0.0",
        display_name="Technical architecture",
        description="Audit",
        skill_ref=agent.skill_set_ref,
        core_contract_version=1,
        rule_pack_ref=agent.rule_pack_ref,
        rule_pack_version=agent.rule_pack_version,
        profile=profile,
        agents=[agent],
    )
    task = AuditTask(
        task_id="task-example",
        document=InputDocument(
            filename="方案.docx",
            content_sha256="a" * 64,
            source_uri="file:///docguard-inbox/source.docx",
        ),
        profile=profile,
        review_type=review_type,
        agent_backend=AgentBackend.OPENCLAW,
    )
    attempt = AuditAttempt(
        attempt_id="attempt-example",
        input_manifest_uri="file:///docguard-results/input-manifest.json",
        result_uri="file:///docguard-results/findings.json",
        input_sha256=task.document.content_sha256,
    )
    run = AgentRun(
        agent=agent,
        runtime_binding=AgentRuntimeBinding(
            route_id="technical-audit/content-reviewer",
            route_version="1.0.0",
            route_config_version="test",
            backend=AgentBackend.OPENCLAW,
            target_ref="openclaw/audit-runtime",
        ),
        result_uri="file:///docguard-results/findings/content.findings.json",
    )
    return task, attempt, run


def test_openclaw_dispatch_logs_the_complete_request_body(monkeypatch) -> None:
    task, attempt, run = _audit_run()
    _Client.sent_request = None
    monkeypatch.setattr(agents.httpx, "Client", _Client)
    records: list[logging.LogRecord] = []

    class LogCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = LogCapture()
    previous_level = agents.logger.level
    agents.logger.setLevel(logging.INFO)
    agents.logger.addHandler(handler)
    try:
        response_id = agents.OpenClawAgentGateway(
            gateway_url="http://gateway.example/v1", api_token="secret-token"
        ).execute_attempt(task, attempt, run)
    finally:
        agents.logger.removeHandler(handler)
        agents.logger.setLevel(previous_level)

    assert response_id == "resp_example"
    assert _Client.sent_request is not None
    assert _Client.sent_request["json"]["user"] == (
        "docguard:task:task-example:attempt:attempt-example:agent:content-reviewer"
    )
    request_log = next(
        record.getMessage()
        for record in records
        if record.getMessage().startswith("openclaw.dispatch.request ")
    )
    logged_body = json.loads(request_log.split("request_body=", maxsplit=1)[1])
    assert logged_body == _Client.sent_request["json"]
    assert "secret-token" not in request_log
