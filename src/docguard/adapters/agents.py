from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
from typing import Protocol

import httpx

from docguard.settings import Settings

from docguard.domain.models import AgentBackend, AgentRun, AuditAttempt, AuditProfile, AuditTask, Finding

try:
    from deepseek_harness import DeepSeekHarness
except ImportError:
    DeepSeekHarness = None  # type: ignore


logger = logging.getLogger("docguard.agents")


class GraphAuditGateway(Protocol):
    """Synchronous Finding producer consumed by the LangGraph audit flow."""

    def audit_full_text(self, profile: AuditProfile) -> list[Finding]: ...

    def audit_architecture(self, profile: AuditProfile) -> list[Finding]: ...


class AgentGateway(Protocol):
    """Dispatches an artifact-delivered specialist audit attempt."""

    """Execute specific agent run (1 task, 1 attempt, 1 agent run)"""
    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None: ...

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None: ...


def artifact_session_id(task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str:
    """Return the stable, specialist-scoped conversation key used by artifact gateways."""
    return f"docguard:task:{task.task_id}:attempt:{attempt.attempt_id}:agent:{run.agent.agent_id}"


def artifact_prompt(task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str:
    """Build the backend-neutral contract prompt for one specialist run."""
    if task.review_type is None:
        raise GatewayExecutionError("Task has no frozen review type definition")
    manifest_path = attempt.input_manifest_uri.removeprefix("file://")
    result_path = run.result_uri.removeprefix("file://")
    document_path = task.document.source_uri.removeprefix("file://")
    attempt_root = PurePosixPath(result_path).parent.parent
    execution_backend = run.execution_backend or task.agent_backend
    return "\n".join(
        [
            "进行文档审核，必要信息如下：",
            f"DOCGUARD_AGENT_ID={run.agent.agent_id}",
            f"DOCGUARD_AGENT_VERSION={run.agent.version}",
            f"DOCGUARD_AGENT_BACKEND={execution_backend.value}",
            f"DOCGUARD_AGENT_MODEL_REF={run.resolved_runtime_binding.model_ref}",
            f"DOCGUARD_DIMENSION={run.agent.dimension}",
            f"DOCGUARD_SCOPE={run.agent.scope or ''}",
            f"DOCGUARD_REVIEW_TYPE={task.review_type.review_type_id}",
            f"DOCGUARD_REVIEW_TYPE_VERSION={task.review_type.version}",
            f"DOCGUARD_CORE_CONTRACT_VERSION={task.review_type.core_contract_version}",
            f"DOCGUARD_VISUAL_POLICY={json.dumps(task.review_type.visual_policy, ensure_ascii=False)}",
            f"INPUT_DOCX={document_path}",
            f"DOCGUARD_TASK_ID={task.task_id}",
            f"DOCGUARD_ATTEMPT_ID={attempt.attempt_id}",
            f"DOCGUARD_AUDIT_MANIFEST={manifest_path}",
            f"DOCGUARD_RESULT_FILE={result_path}",
            f"DOCGUARD_EVIDENCE_DIR={attempt_root / 'evidence'}",
            f"DOCGUARD_WORK_DIR={attempt_root / 'work'}",
            "应用已完成 DOCX 提取、审计包构建和逐图视觉事实提取。",
        ]
    )


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
        settings = Settings.from_environment()
        self.gateway_url = (gateway_url or settings.openclaw_gateway_url).rstrip("/")
        self.api_token = api_token or settings.openclaw_api_token

    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None:
        """Hold the Gateway SSE request and return its response id when available.

        This intentionally does not parse assistant text into findings. The agent
        must atomically deliver its assigned findings artifact to the task directory.
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

        return self._stream_attempt(task, attempt, run, self._prompt(task, attempt, run))

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None:
        """Continue the task's existing Gateway conversation and collect its SSE."""
        return self._stream_attempt(
            task,
            attempt,
            run,
            "当前任务若未完成审核，则继续审核，否则告诉我已完成",
            previous_response_id=run.gateway_response_id,
        )

    def _stream_attempt(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        run: AgentRun,
        input_text: str,
        *,
        previous_response_id: str | None = None,
    ) -> str | None:
        if task.review_type is None:
            raise GatewayExecutionError("Task has no frozen review type definition")
        request: dict[str, object] = {
            "model": run.resolved_runtime_binding.model_ref,
            # A task can be retried and runs one independent session per specialist.
            # Keep those trajectories isolated so an action-chain export can identify
            # exactly one session from task, attempt, and Agent identity.
            # https://docs.openclaw.ai/gateway/openresponses-http-api
            # openclaw will generate a stable session key with "users" value;
            "user": run.gateway_session_id or artifact_session_id(task, attempt, run),
            "stream": True,
            "input": input_text,
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        headers = {"Authorization": f"Bearer {self.api_token}"}
        response_id: str | None = None
        event_count = 0
        request_body = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        logger.info(
            "openclaw.dispatch.request task_id=%s attempt_id=%s agent_id=%s endpoint=%s request_body=%s",
            task.task_id,
            attempt.attempt_id,
            run.agent.agent_id,
            f"{self.gateway_url}/responses",
            request_body,
        )
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
                        print(
                            "openclaw.sse "
                            f"task_id={task.task_id} attempt_id={attempt.attempt_id} "
                            f"event={event} payload={payload!r}",
                            flush=True,
                        )
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
    def _prompt(task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str:
        return artifact_prompt(task, attempt, run)

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


class DshAgentGateway:
    """Dispatches audit tasks through DeepSeek Harness SDK."""

    backend = AgentBackend.DSH

    def __init__(
        self,
        dsh_home: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        profile: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        settings = Settings.from_environment()
        self.dsh_home = dsh_home or settings.dsh_home
        self.provider = provider or settings.dsh_provider
        self.model = model or settings.dsh_model
        self.profile = profile or settings.dsh_profile
        self.max_tokens = max_tokens or settings.dsh_max_tokens

    def audit_full_text(self, profile: AuditProfile) -> list[Finding]:
        raise NotImplementedError("DSH audit_full_text requires audit_graph integration")

    def audit_architecture(self, profile: AuditProfile) -> list[Finding]:
        raise NotImplementedError("DSH audit_architecture requires audit_graph integration")

    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None:
        """Run a DSH specialist and let it deliver its durable findings artifact."""
        if DeepSeekHarness is None:
            raise GatewayExecutionError(
                "deepseek-harness-sdk is not installed. Run: pip install deepseek-harness-sdk"
            )
        if not self.dsh_home:
            raise GatewayExecutionError(
                "DOCGUARD_DSH_HOME must be configured to use DSH backend"
            )

        session_id = run.gateway_session_id or artifact_session_id(task, attempt, run)
        prompt = artifact_prompt(task, attempt, run)

        try:
            with DeepSeekHarness(
                dsh_home=self.dsh_home,
                cwd=run.workspace_path,
                patches=(run.runtime_patch_path,) if run.runtime_patch_path else (),
                provider=run.resolved_runtime_binding.provider or self.provider,
                model=run.resolved_runtime_binding.model or self.model,
                profile=run.resolved_runtime_binding.runtime_config_ref or self.profile,
                max_tokens=self.max_tokens,
            ) as harness:
                result = harness.run(prompt, session_id=session_id)
            logger.info(
                "dsh.execute_finished task_id=%s attempt_id=%s agent_id=%s response_chars=%s",
                task.task_id,
                attempt.attempt_id,
                run.agent.agent_id,
                len(result.final_response),
            )
            # ``final_response`` is completion text, not a provider response id.
            # The durable session key lives on ``AgentRun.gateway_session_id``.
            return None
        except Exception as exc:
            logger.exception("dsh.execute_failed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
            raise GatewayExecutionError(f"DSH execution failed: {exc}") from exc

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt, run: AgentRun) -> str | None:
        """Continue an existing DSH session."""
        if DeepSeekHarness is None:
            raise GatewayExecutionError(
                "deepseek-harness-sdk is not installed. Run: pip install deepseek-harness-sdk"
            )
        if not self.dsh_home:
            raise GatewayExecutionError(
                "DOCGUARD_DSH_HOME must be configured to use DSH backend"
            )

        session_id = run.gateway_session_id or artifact_session_id(task, attempt, run)

        prompt = "当前任务若未完成审核，则继续审核，否则告诉我已完成"

        try:
            with DeepSeekHarness(
                dsh_home=self.dsh_home,
                cwd=run.workspace_path,
                patches=(run.runtime_patch_path,) if run.runtime_patch_path else (),
                provider=run.resolved_runtime_binding.provider or self.provider,
                model=run.resolved_runtime_binding.model or self.model,
                profile=run.resolved_runtime_binding.runtime_config_ref or self.profile,
                max_tokens=self.max_tokens,
            ) as harness:
                result = harness.run(prompt, session_id=session_id)
            logger.info(
                "dsh.continue_finished task_id=%s attempt_id=%s agent_id=%s response_chars=%s",
                task.task_id,
                attempt.attempt_id,
                run.agent.agent_id,
                len(result.final_response),
            )
            return None
        except Exception as exc:
            logger.exception("dsh.continue_failed task_id=%s attempt_id=%s", task.task_id, attempt.attempt_id)
            raise GatewayExecutionError(f"DSH continue failed: {exc}") from exc


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
        case AgentBackend.DSH:
            raise ValueError(
                "DSH is artifact-delivered; use the DSH execute_attempt path instead of audit_graph"
            )
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
