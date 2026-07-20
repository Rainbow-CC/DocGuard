from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COLLECTING = "collecting"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"


class AgentBackend(StrEnum):
    STUB = "stub"
    OPENCLAW = "openclaw"
    LANGCHAIN = "langchain"


class Severity(StrEnum):
    CRITICAL = "重大"
    HIGH = "一般"
    MEDIUM = "优化"
    INFO = "观察"


class FindingJudgment(StrEnum):
    IMAGE_TEXT_CONFLICT = "图文不一致"
    TEXT_CONFLICT = "文本不一致"
    TEXT_INCOMPLETE = "文本不完整"
    NO_DIAGRAM_EVIDENCE = "未提供图示证据"
    UNCERTAIN = "不确定"
    NOT_APPLICABLE = "不适用"


class FindingCategory(StrEnum):
    CONSISTENCY = "一致性"
    USABILITY = "可用性"
    DEPLOYMENT = "部署"
    SECURITY = "安全"
    DATA_FLOW = "数据流"
    READABILITY = "可读性"


class ReviewDimension(StrEnum):
    REQUIREMENTS_AND_POSITIONING = "需求与系统定位"
    SOLUTION_AND_ARCHITECTURE = "方案与架构合理性"
    PERFORMANCE_CAPACITY_RESOURCES = "性能、容量与资源"
    SECURITY_DATA_COMPLIANCE = "安全、数据与合规"
    AVAILABILITY_BACKUP_DR = "可用性、备份与灾备"
    INTEGRATION_BOUNDARIES_DATA_FLOW = "集成、边界与数据流"
    DEPLOYMENT_NETWORK_ENVIRONMENT = "部署、网络与环境"
    IMPLEMENTATION_OPERATIONS = "实施与运维可行性"
    DOCUMENT_GOVERNANCE = "文档治理与完整性"
    CONSISTENCY_READABILITY = "一致性与可读性"


class EvidenceRef(BaseModel):
    """A stable, auditable pointer to one source item in the audit package."""

    evidence_id: str = Field(pattern=r"^(txt|tbl|fig|meta)_\d{3,}$")
    kind: Literal["text", "table", "figure", "metadata"]
    source_uri: str
    location: str = Field(description="Section/page/table cell/figure region identifier")
    sha256: str = Field(min_length=64, max_length=64)
    excerpt: str | None = None


class Finding(BaseModel):
    """The JSON contract returned by an audit agent.

    The Chinese field names mirror the OpenClaw skill contract.  The two
    legacy aliases keep previously produced Finding payloads readable while
    ensuring new JSON schema/output uses the contract names.

    TODO: is this class too complex?
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    finding_id: str = Field(default_factory=lambda: f"fd_{uuid4().hex}")
    schema_version: Literal["finding-v1"] = "finding-v1"
    rule_id: str
    category: FindingCategory
    review_dimension: ReviewDimension
    judgment: FindingJudgment = Field(
        validation_alias=AliasChoices("judgment", "decision"),
        description="审核判定，必须取 OpenClaw skill 定义的发现判定之一",
    )
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    title: str
    text_evidence: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("text_evidence", "textEvidence"),
        description="一条或多条完整定位证据",
    )
    image_evidence: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("image_evidence", "imageEvidence"),
        description="完整图片定位证据；纯文本审核时必须写：不适用（纯文本审核）",
    )
    problem_description: str = Field(
        min_length=1,
        validation_alias=AliasChoices("problem_description", "issue_description", "claim"),
        description="问题根因及影响范围",
    )
    impact: str = Field(min_length=1)
    revision_suggestion: str = Field(
        min_length=1,
        validation_alias=AliasChoices("revision_suggestion", "recommendation"),
        description="可执行的修订动作",
    )
    revision_location: str = Field(min_length=1)
    completion_criteria: str = Field(
        min_length=1,
        validation_alias=AliasChoices("completion_criteria", "acceptance_criteria"),
        description="可验证的完成状态",
    )
    evidence_ids: list[str] = Field(min_length=1)
    root_cause_key: str
    agent_backend: AgentBackend

    # Compatibility accessors for the current renderer and deduplication code.
    @property
    def claim(self) -> str:
        return self.problem_description

    @property
    def recommendation(self) -> str:
        return self.revision_suggestion

    @property
    def acceptance_criteria(self) -> str:
        return self.completion_criteria


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
    attempts: list[AuditAttempt] = Field(default_factory=list)


class AuditAttempt(BaseModel):
    """One independently recoverable delivery attempt for an audit task."""

    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    status: AttemptStatus = AttemptStatus.PREPARED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_manifest_uri: str
    result_uri: str
    input_sha256: str
    gateway_response_id: str | None = None
    error: str | None = None


class TaskCreatedResponse(BaseModel):
    task_id: str
    status: TaskStatus
    status_url: HttpUrl | str


class UploadDocumentResponse(BaseModel):
    upload_id: str
    agent_id: str
    filename: str
    size_bytes: int
    content_sha256: str
    agent_path: str
    source_uri: str
