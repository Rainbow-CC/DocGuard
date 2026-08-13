from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator


def _server_now() -> datetime:
    """Return the DocGuard server's local time as an offset-aware datetime."""
    return datetime.now().astimezone()


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


class AgentRunStatus(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"


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


# todo: do not validate this field currently, consider to delete it;
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


class EvidenceSelector(BaseModel):
    """Optional, deterministic refinement inside a block-level evidence item."""

    row_match: dict[str, str] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)


class ImageRegion(BaseModel):
    """A normalized rectangle over an evidence image, when visual extraction is certain."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_image(self) -> ImageRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("image region must stay within the image bounds")
        return self


class EvidenceRef(BaseModel):
    """A user-reviewable reference to an item in the task evidence bundle."""

    evidence_id: str = Field(min_length=1)
    role: Literal["primary", "supporting"] = "primary"
    quote: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    selector: EvidenceSelector | None = None
    region: ImageRegion | None = None


class Finding(BaseModel):
    """The JSON contract returned by an audit agent.

    The Chinese field names mirror the OpenClaw skill contract.  The two
    legacy aliases keep previously produced Finding payloads readable while
    ensuring new JSON schema/output uses the contract names.

    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    finding_id: str = Field(default_factory=lambda: f"fd_{uuid4().hex}")
    schema_version: Literal["finding-v1"] = "finding-v1"
    rule_id: str
    category: FindingCategory
    review_dimension: str
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
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
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


class AuditAgentDefinition(BaseModel):
    """A versioned specialist registered under one review type.

    Example::

        AuditAgentDefinition(
            agent_id="architecture-advisor",
            version="1.0.0",
            dimension="architecture",       # Stable report category.
            scope="deployment",              # Optional category subdivision.
            agent_model_ref="openclaw/architect",
            skill_ref="docx-architecture-advisor",
            rule_pack_ref="technical-architecture/architecture-rules.md",
            rule_pack_version="1.0.0",
        )

    This agent must deliver ``findings/architecture.deployment.findings.json``.
    ``dimension`` and ``scope`` describe the result, not the particular model
    or implementation, so the report remains stable when an agent is replaced.
    """

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: str
    dimension: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    scope: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    agent_backend: AgentBackend = AgentBackend.OPENCLAW
    agent_model_ref: str
    skill_ref: str
    rule_pack_ref: str
    rule_pack_version: str

    @property
    def artifact_stem(self) -> str:
        return ".".join(part for part in (self.dimension, self.scope) if part)


class ReviewTypeDefinition(BaseModel):
    """A versioned, platform-owned definition of one report review product."""

    review_type_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: str
    display_name: str
    description: str
    agent_backend: AgentBackend = AgentBackend.OPENCLAW
    agent_model_ref: str
    skill_ref: str
    core_contract_version: int = Field(ge=1)
    rule_pack_ref: str
    rule_pack_version: str
    visual_policy: dict[str, object] = Field(default_factory=dict)
    profile: AuditProfile
    agents: list[AuditAgentDefinition] = Field(default_factory=list)

    def resolved_agents(self) -> list[AuditAgentDefinition]:
        """Return registered specialists, retaining legacy single-agent definitions."""
        if self.agents:
            return list(self.agents)
        # default
        return [
            AuditAgentDefinition(
                agent_id="default",
                version=self.version,
                dimension="content",
                agent_backend=self.agent_backend,
                agent_model_ref=self.agent_model_ref,
                skill_ref=self.skill_ref,
                rule_pack_ref=self.rule_pack_ref,
                rule_pack_version=self.rule_pack_version,
            )
        ]


class InputDocument(BaseModel):
    filename: str
    content_sha256: str = Field(min_length=64, max_length=64)
    source_uri: str


class CreateTaskRequest(BaseModel):
    document: InputDocument
    review_type_id: str = "technical-architecture"
    # Kept as an internal development override.  The web API does not expose it;
    # production routing always comes from the selected review type definition.
    agent_backend: AgentBackend | None = None


class AuditTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = TaskStatus.QUEUED
    document: InputDocument
    profile: AuditProfile
    # Optional only so pre-platform task rows remain readable. Newly created
    # tasks always carry a frozen review type definition.
    review_type: ReviewTypeDefinition | None = None
    agent_backend: AgentBackend
    created_at: datetime = Field(default_factory=_server_now)
    updated_at: datetime = Field(default_factory=_server_now)
    error: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    report_markdown: str | None = None
    checkpoint_thread_id: str | None = None
    attempts: list[AuditAttempt] = Field(default_factory=list)


class AuditAttempt(BaseModel):
    """One independently recoverable delivery attempt for an audit task."""

    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    status: AttemptStatus = AttemptStatus.PREPARED
    created_at: datetime = Field(default_factory=_server_now)
    updated_at: datetime = Field(default_factory=_server_now)
    input_manifest_uri: str
    result_uri: str
    input_sha256: str
    gateway_response_id: str | None = None
    error: str | None = None
    agent_runs: list[AgentRun] = Field(default_factory=list)


class AgentRun(BaseModel):
    """One specialist execution inside an independently recoverable attempt."""

    agent: AuditAgentDefinition
    result_uri: str
    status: AgentRunStatus = AgentRunStatus.PREPARED
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
