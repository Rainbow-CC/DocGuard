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
        return self._stream_attempt(task, attempt, "当前任务若未完成审核，则继续审核，否则告诉我已完成", previous_response_id=attempt.gateway_response_id)

    def _stream_attempt(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        input_text: str,
        *,
        previous_response_id: str | None = None,
    ) -> str | None:
        if task.review_type is None:
            raise GatewayExecutionError("Task has no frozen review type definition")
        request: dict[str, object] = {
            "model": task.review_type.agent_model_ref,
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
    def _prompt(task: AuditTask, attempt: AuditAttempt) -> str:
        if task.review_type is None:
            raise GatewayExecutionError("Task has no frozen review type definition")
        manifest_path = attempt.input_manifest_uri.removeprefix("file://")
        result_path = attempt.result_uri.removeprefix("file://")
        document_path = task.document.source_uri.removeprefix("file://")
        return "\n".join(
            [
                f"执行 {task.review_type.skill_ref} skill。",
                f"DOCGUARD_REVIEW_TYPE={task.review_type.review_type_id}",
                f"DOCGUARD_REVIEW_TYPE_VERSION={task.review_type.version}",
                f"DOCGUARD_CORE_CONTRACT_VERSION={task.review_type.core_contract_version}",
                f"DOCGUARD_RULE_PACK={task.review_type.rule_pack_ref}",
                f"DOCGUARD_RULE_PACK_VERSION={task.review_type.rule_pack_version}",
                f"DOCGUARD_VISUAL_POLICY={json.dumps(task.review_type.visual_policy, ensure_ascii=False)}",
                f"INPUT_DOCX={document_path}",
                f"DOCGUARD_TASK_ID={task.task_id}",
                f"DOCGUARD_ATTEMPT_ID={attempt.attempt_id}",
                f"DOCGUARD_AUDIT_MANIFEST={manifest_path}",
                f"DOCGUARD_RESULT_FILE={result_path}",
                f"DOCGUARD_EVIDENCE_DIR={result_path.rsplit('/', maxsplit=1)[0]}/evidence",
                f"DOCGUARD_WORK_DIR={result_path.rsplit('/', maxsplit=1)[0]}/work",
                "应用已完成 DOCX 提取、审计包构建和逐图视觉事实提取。",
                "只读取 manifest、DOCGUARD_WORK_DIR/audit-context.md、DOCGUARD_WORK_DIR/audit-evidence.json 和 DOCGUARD_WORK_DIR/vision-responses/；不得重新处理 DOCX、调用视觉模型或覆盖 evidence/。",
                "不得输出最终 Markdown 审核报告。",
                "必须先校验结果，再以同目录临时文件加原子重命名交付 findings.json。",
                "聊天最终答复只确认工件已写入，不得在答复中输出 findings。",
            ]
        )

class DshAgentGateway:
    """Dispatches an audit skill through DSH API Gateway; results arrive as an artifact."""

    def __init__(self, gateway_url: str | None = None, api_key: str | None = None) -> None:
        self.gateway_url = (gateway_url or os.getenv("DSH_GATEWAY_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("DSH_AGW_KEY", "")

    def execute_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None:
        """Create a DSH session, stream SSE, and return the session id.

        The agent must atomically deliver findings.json to the task's result directory.
        """
        if not self.gateway_url or not self.api_key:
            logger.error(
                "dsh.dispatch.configuration_missing task_id=%s attempt_id=%s "
                "gateway_url_configured=%s api_key_configured=%s",
                task.task_id,
                attempt.attempt_id,
                bool(self.gateway_url),
                bool(self.api_key),
            )
            raise GatewayExecutionError("DSH_GATEWAY_URL and DSH_API_KEY must be configured")

        return self._stream_attempt(task, attempt, self._prompt(task, attempt))

    def continue_attempt(self, task: AuditTask, attempt: AuditAttempt) -> str | None:
        """Continue the task's existing DSH session and collect its SSE."""
        if not attempt.gateway_response_id:
            raise GatewayExecutionError("No gateway_response_id for continuing DSH session")
        return self._stream_attempt(
            task, attempt,
            "当前任务若未完成审核，则继续审核，否则告诉我已完成",
            previous_session_id=attempt.gateway_response_id,
        )

    def _claim_key(self) -> str:
        """Claim a new API key from the gateway."""
        url = f"{self.gateway_url}/key"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
        }
        response = httpx.post(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        logger.info("dsh.api_key.claimed")
        return data["apiKey"]

    def _create_session(self) -> str:
        """Create a new DSH session and return its id."""
        url = f"{self.gateway_url}/sessions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = httpx.post(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            return data["sessionId"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Key might be invalid/expired, try to claim a new one
                logger.warning("dsh.api_key.expired, claiming new key")
                self.api_key = self._claim_key()
                headers["Authorization"] = f"Bearer {self.api_key}"
                response = httpx.post(url, headers=headers, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                return data["sessionId"]
            raise

    def _adopt_session(self, session_id: str) -> None:
        """Adopt an existing DSH session."""
        url = f"{self.gateway_url}/sessions/{session_id}/adopt"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = httpx.post(url, headers=headers, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Key might be invalid/expired, try to claim a new one
                logger.warning("dsh.api_key.expired, claiming new key")
                self.api_key = self._claim_key()
                headers["Authorization"] = f"Bearer {self.api_key}"
                response = httpx.post(url, headers=headers, timeout=30.0)
                response.raise_for_status()
            else:
                raise

    def _stream_attempt(
        self,
        task: AuditTask,
        attempt: AuditAttempt,
        input_text: str,
        *,
        previous_session_id: str | None = None,
    ) -> str | None:
        if task.review_type is None:
            raise GatewayExecutionError("Task has no frozen review type definition")

        # Step 1: Create or adopt session
        if previous_session_id:
            session_id = previous_session_id
            self._adopt_session(session_id)
        else:
            session_id = self._create_session()

        logger.info(
            "dsh.session.created task_id=%s attempt_id=%s session_id=%s",
            task.task_id,
            attempt.attempt_id,
            session_id,
        )

        # Step 2: Attach to SSE stream BEFORE sending message (same as ask.py)
        stream_url = f"{self.gateway_url}/sessions/{session_id}/stream"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        timeout = httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=60.0)

        # Use httpx.stream() context manager for proper SSE handling
        try:
            with httpx.stream("GET", stream_url, headers=headers, timeout=timeout) as stream_response:
                stream_response.raise_for_status()

                # Step 3: Send message
                message_url = f"{self.gateway_url}/sessions/{session_id}/messages"
                message_headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json; charset=utf-8",
                }
                message_response = httpx.post(
                    message_url,
                    headers=message_headers,
                    json={"content": input_text},
                    timeout=timeout,
                )
                message_response.raise_for_status()

                # Step 4: Read SSE events until turn_end
                event_count = 0
                for event, payload in _iter_sse_events(stream_response):
                    event_count += 1
                    print(
                        "dsh.sse "
                        f"task_id={task.task_id} attempt_id={attempt.attempt_id} "
                        f"event={event} payload={payload!r}",
                        flush=True,
                    )
                    if event == "turn_end":
                        logger.info(
                            "dsh.dispatch.finished task_id=%s attempt_id=%s session_id=%s sse_events=%s",
                            task.task_id,
                            attempt.attempt_id,
                            session_id,
                            event_count,
                        )
                        break

        except httpx.HTTPError as exc:
            logger.exception(
                "dsh.dispatch.transport_failed task_id=%s attempt_id=%s",
                task.task_id,
                attempt.attempt_id,
            )
            raise GatewayExecutionError(f"DSH transport failure: {exc}") from exc

        return session_id

    def _upload_file(self, session_id: str, file_path: str, category: str = "evidence") -> str | None:
        """Upload a file to the DSH session and return the file URI.

        The file is sent as a content block in a message to make it available to the agent.
        """
        if not os.path.exists(file_path):
            logger.warning("dsh.upload.file_not_found session_id=%s path=%s", session_id, file_path)
            return None

        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_content = f.read()

        # For now, send file content as base64-encoded text in a content block
        # This makes the file content available to the agent in the session
        import base64

        content_block = {
            "type": "file",
            "name": file_name,
            "category": category,
            "content": base64.b64encode(file_content).decode("utf-8"),
            "mime_type": self._guess_mime_type(file_path),
        }

        message_url = f"{self.gateway_url}/sessions/{session_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            response = httpx.post(
                message_url,
                headers=headers,
                json={"content": [content_block]},
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=60.0),
            )
            response.raise_for_status()
            data = response.json()
            logger.info(
                "dsh.upload.success session_id=%s file=%s message_id=%s",
                session_id,
                file_name,
                data.get("messageId"),
            )
            return f"file://{file_path}"
        except httpx.HTTPError as exc:
            logger.warning(
                "dsh.upload.failed session_id=%s file=%s error=%s",
                session_id,
                file_name,
                exc,
            )
            return None

    @staticmethod
    def _guess_mime_type(file_path: str) -> str:
        """Guess MIME type from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        mime_types = {
            ".txt": "text/plain",
            ".json": "application/json",
            ".md": "text/markdown",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        return mime_types.get(ext, "application/octet-stream")

    @staticmethod
    def _prompt(task: AuditTask, attempt: AuditAttempt) -> str:
        if task.review_type is None:
            raise GatewayExecutionError("Task has no frozen review type definition")
        manifest_path = attempt.input_manifest_uri.removeprefix("file://")
        result_path = attempt.result_uri.removeprefix("file://")
        document_path = task.document.source_uri.removeprefix("file://")
        return "\n".join(
            [
                f"执行 {task.review_type.skill_ref} skill。",
                f"DOCGUARD_REVIEW_TYPE={task.review_type.review_type_id}",
                f"DOCGUARD_REVIEW_TYPE_VERSION={task.review_type.version}",
                f"DOCGUARD_CORE_CONTRACT_VERSION={task.review_type.core_contract_version}",
                f"DOCGUARD_RULE_PACK={task.review_type.rule_pack_ref}",
                f"DOCGUARD_RULE_PACK_VERSION={task.review_type.rule_pack_version}",
                f"DOCGUARD_VISUAL_POLICY={json.dumps(task.review_type.visual_policy, ensure_ascii=False)}",
                f"INPUT_DOCX={document_path}",
                f"DOCGUARD_TASK_ID={task.task_id}",
                f"DOCGUARD_ATTEMPT_ID={attempt.attempt_id}",
                f"DOCGUARD_AUDIT_MANIFEST={manifest_path}",
                f"DOCGUARD_RESULT_FILE={result_path}",
                f"DOCGUARD_EVIDENCE_DIR={result_path.rsplit('/', maxsplit=1)[0]}/evidence",
                f"DOCGUARD_WORK_DIR={result_path.rsplit('/', maxsplit=1)[0]}/work",
                "应用已完成 DOCX 提取、审计包构建和逐图视觉事实提取。",
                "只读取 manifest、DOCGUARD_WORK_DIR/audit-context.md、DOCGUARD_WORK_DIR/audit-evidence.json 和 DOCGUARD_WORK_DIR/vision-responses/；不得重新处理 DOCX、调用视觉模型或覆盖 evidence/。",
                "不得输出最终 Markdown 审核报告。",
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

    OpenClaw and DSH deliberately do not implement this contract: their results are
    durable artifacts that may arrive after the SSE request finishes or disconnects.
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
        case AgentBackend.DSH:
            raise ValueError(
                "DSH is artifact-delivered; use the DSH attempt path instead of audit_graph"
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
