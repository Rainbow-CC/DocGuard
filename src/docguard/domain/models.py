from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentBackend(StrEnum):
    STUB = "stub"
    OPENCLAW = "openclaw"
    LANGCHAIN = "langchain"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EvidenceRef(BaseModel):
    """A stable, auditable pointer to one source item in the audit package."""

    evidence_id: str = Field(pattern=r"^(txt|tbl|fig|meta)_\d{3,}$")
    kind: Literal["text", "table", "figure", "metadata"]
    source_uri: str
    location: str = Field(description="Section/page/table cell/figure region identifier")
    sha256: str = Field(min_length=64, max_length=64)
    excerpt: str | None = None


class Finding(BaseModel):
    """The only judgment contract an audit agent is allowed to return."""

    finding_id: str = Field(default_factory=lambda: f"fd_{uuid4().hex}")
    schema_version: Literal["finding-v1"] = "finding-v1"
    rule_id: str
    category: str
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    title: str
    claim: str
    recommendation: str
    acceptance_criteria: str
    evidence_ids: list[str] = Field(min_length=1)
    root_cause_key: str
    agent_backend: AgentBackend


class AuditProfile(BaseModel):
    profile_id: str
    version: str
    required_nodes: list[str]
    report_template: str
    evidence_policy: Literal["accepted_revision_only"]
    prompt_versions: dict[str, int]


class InputDocument(BaseModel):
    filename: str
    content_sha256: str = Field(min_length=64, max_length=64)
    source_uri: str


class CreateTaskRequest(BaseModel):
    document: InputDocument
    profile_id: str = "technical-audit"
    agent_backend: AgentBackend = AgentBackend.STUB


class AuditTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = TaskStatus.QUEUED
    document: InputDocument
    profile: AuditProfile
    agent_backend: AgentBackend
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    report_markdown: str | None = None
    checkpoint_thread_id: str | None = None


class TaskCreatedResponse(BaseModel):
    task_id: str
    status: TaskStatus
    status_url: HttpUrl | str
