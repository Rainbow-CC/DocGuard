from __future__ import annotations

import json
import logging
import os
from typing import Protocol

import httpx

from docguard.domain.models import AgentBackend, AuditAttempt, AuditProfile, AuditTask, Finding


logger = logging.getLogger("docguard.agents")


class GraphAuditGateway(Protocol):
    """Synchronous Finding producer consumed by the LangGraph audit flow."""

    def audit_full_text(self, profile: AuditProfile) -> list[Finding]: ...

    def audit_architecture(self, profile: AuditProfile) -> list[Finding]: ...


class OpenClawAttemptGateway(Protocol):
    """Dispatches an artifact-delivered OpenClaw audit attempt."""

    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None: ...

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None: ...


class StubAgentGateway:
    """A deterministic local executor used until a real model adapter is configured."""

    backend = AgentBackend.STUB

    def audit_full_text(self, profile: AuditProfile) -> list[Finding]:
        return []

    def audit_architecture(self, profile: AuditProfile) -> list[Finding]:
        return []


class OpenClawAgentGateway:
    """Dispatches an audit skill through OpenResponses; results arrive as an artifact."""

    def __init__(self, gateway_url: str | None = None, api_token: str | None = None) -> None:
        self.gateway_url = (gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "")).rstrip("/")
        self.api_token = api_token or os.getenv("OPENCLAW_API_TOKEN", "")

    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None:
        """Hold the Gateway SSE request and return its response id when available.

        This intentionally does not parse assistant text into findings.  The agent
        must atomically deliver findings.json to the task's result directory.
        """
        if not self.gateway_url or not self.api_token:
            logger.error(
                "openclaw.dispatch.configuration_missing task_id=%s attempt_id=%s "
                "gateway_url_configured=%s api_token_configured=%s",
                task.task_id,
                attempt.attempt_id,
                bool(self.gateway_url),
                bool(self.api_token),
            )
            raise GatewayExecutionError("OPENCLAW_GATEWAY_URL and OPENCLAW_API_TOKEN must be configured")

        return self._stream_attempt(task, attempt, self._prompt(task, attempt))

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None:
        """Continue the task's existing Gateway conversation and collect its SSE."""
        return self._stream_attempt(task, attempt, "继续", previous_response_id=attempt.gateway_response_id)

    def _stream_attempt(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        input_text: str,
        *,
        previous_response_id: str | None = None,
    ) -> str | None:
        request: dict[str, object] = {
            "model": "openclaw/audit-runtime",
            "user": f"docguard:task:{task.task_id}",
            "stream": True,
            "input": input_text,
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        headers = {"Authorization": f"Bearer {self.api_token}"}
        response_id: str | None = None
        event_count = 0
        logger.info(
            "openclaw.dispatch.started task_id=%s attempt_id=%s endpoint=%s model=%s",
            task.task_id,
            attempt.attempt_id,
            f"{self.gateway_url}/responses",
            request["model"],
        )
        try:
            timeout = httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=60.0)
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", f"{self.gateway_url}/responses", headers=headers, json=request
                ) as response:
                    response.raise_for_status()
                    for event, payload in _iter_sse_events(response):
                        event_count += 1
                        if event == "response.created":
                            response_id = _response_id(payload) or response_id
                            logger.info(
                                "openclaw.response.created task_id=%s attempt_id=%s response_id=%s",
                                task.task_id,
                                attempt.attempt_id,
                                response_id,
                            )
                        elif event == "response.failed":
                            message = _response_error(payload) or "OpenClaw reported response.failed"
                            raise GatewayExecutionError(message)
        except httpx.HTTPError as exc:
            logger.exception(
                "openclaw.dispatch.transport_failed task_id=%s attempt_id=%s",
                task.task_id,
                attempt.attempt_id,
            )
            raise GatewayExecutionError(f"OpenClaw transport failure: {exc}") from exc
        logger.info(
            "openclaw.dispatch.finished task_id=%s attempt_id=%s response_id=%s sse_events=%s",
            task.task_id,
            attempt.attempt_id,
            response_id,
            event_count,
        )
        return response_id

    @staticmethod
    def _prompt(task: AuditTask, attempt: AuditAttempt) -> str:
        manifest_path = attempt.input_manifest_uri.removeprefix("file://")
        result_path = attempt.result_uri.removeprefix("file://")
        document_path = task.document.source_uri.removeprefix("file://")
        return "\n".join(
            [
                "执行 docx-tech-architecture-audit skill。",
                f"INPUT_DOCX={document_path}",
                f"DOCGUARD_TASK_ID={task.task_id}",
                f"DOCGUARD_ATTEMPT_ID={attempt.attempt_id}",
                f"DOCGUARD_AUDIT_MANIFEST={manifest_path}",
                f"DOCGUARD_RESULT_FILE={result_path}",
                f"DOCGUARD_EVIDENCE_DIR={result_path.rsplit('/', maxsplit=1)[0]}/evidence",
                "只读输入 DOCX 和 manifest；不得输出最终 Markdown 审核报告。",
                "必须先校验结果，再以同目录临时文件加原子重命名交付 findings.json。",
                "聊天最终答复只确认工件已写入，不得在答复中输出 findings。",
            ]
        )

class LangChainAgentGateway:
    """Integration seam for a LangChain structured-output runnable."""

    backend = AgentBackend.LANGCHAIN

    def audit_full_text(self, profile: AuditProfile) -> list[Finding]:
        return self._not_configured("full_text")

    def audit_architecture(self, profile: AuditProfile) -> list[Finding]:
        return self._not_configured("architecture")

    def _not_configured(self, audit_type: str) -> list[Finding]:
        raise RuntimeError(
            f"LangChain adapter is not configured for {audit_type}. "
            "Bind a model with structured output to the Finding[] contract."
        )


def graph_gateway_for(backend: AgentBackend) -> GraphAuditGateway:
    """Create a gateway that can synchronously supply findings to ``audit_graph``.

    OpenClaw deliberately does not implement this contract: its result is a durable
    artifact that may arrive after the SSE request finishes or disconnects.
    """
    match backend:
        case AgentBackend.STUB:
            return StubAgentGateway()
        case AgentBackend.LANGCHAIN:
            return LangChainAgentGateway()
        case AgentBackend.OPENCLAW:
            raise ValueError(
                "OpenClaw is artifact-delivered; use the OpenClaw attempt path instead of audit_graph"
            )


class GatewayExecutionError(RuntimeError):
    pass


def _iter_sse_events(response: httpx.Response):
    event = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                if payload != "[DONE]":
                    try:
                        yield event, json.loads(payload)
                    except json.JSONDecodeError:
                        pass
            event = "message"
            data_lines = []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        payload = "\n".join(data_lines)
        if payload != "[DONE]":
            try:
                yield event, json.loads(payload)
            except json.JSONDecodeError:
                pass


def _response_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response", payload)
    return response.get("id") if isinstance(response, dict) else None


def _response_error(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    return error.get("message") if isinstance(error, dict) else None
