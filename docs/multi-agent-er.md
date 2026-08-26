# 多智能体数据模型（ER 图）

 `ReviewTypeDefinition`、`AuditProfile` 与 `AuditAgentDefinition` 会在创建任务时冻结到 `AuditTask`。图中的实体名对应代码类名的大写形式。

```mermaid
erDiagram
    REVIEW_TYPE ||--|| AUDIT_PROFILE : uses
    REVIEW_TYPE ||--o{ AUDIT_AGENT_DEFINITION : registers

    AUDIT_TASK }o--|| REVIEW_TYPE : freezes
    AUDIT_TASK }o--|| AUDIT_PROFILE : freezes
    AUDIT_TASK ||--|| INPUT_DOCUMENT : reviews
    AUDIT_TASK ||--o{ AUDIT_ATTEMPT : retries_as

    AUDIT_ATTEMPT ||--o{ AGENT_RUN : starts
    AGENT_RUN }o--|| AUDIT_AGENT_DEFINITION : snapshots
    AGENT_RUN ||--|| AGENT_RESULT : delivers

    AGENT_RESULT ||--o{ FINDING : contains
    FINDING ||--o{ EVIDENCE_REF : cites

    REVIEW_TYPE {
        string reviewTypeId PK
        string version
        string displayName
        int coreContractVersion
    }

    AUDIT_PROFILE {
        string profileId PK
        string version
        string reportTemplate
    }

    AUDIT_AGENT_DEFINITION {
        string agentId PK
        string version
        string dimension
        string scope
        string modelRef
        string skillRef
        string rulePackRef
    }

    AUDIT_TASK {
        string taskId PK
        string status
        json reviewTypeSnapshot
        json profileSnapshot
    }

    INPUT_DOCUMENT {
        string filename
        string contentSha256
        string sourceUri
    }

    AUDIT_ATTEMPT {
        string attemptId PK
        string status
        string inputSha256
        string findingsDirectory
    }

    AGENT_RUN {
        string resultUri
        string status
        string gatewayResponseId
        string error
    }

    AGENT_RESULT {
        string dimension
        string scope
        string producerAgentId
        string producerAgentVersion
        string producerModelRef
    }

    FINDING {
        string findingId PK
        string ruleId
        string severity
        string judgment
        string title
    }

    EVIDENCE_REF {
        string evidenceId
        string quote
        string role
    }
```

## 关键关系

- **审核类型与 Agent**：一个 `ReviewTypeDefinition` 注册多个 `AuditAgentDefinition`。Agent 定义可独立注册；`dimension` 是报告中的稳定一级分类，`scope` 是可选的细分分类。
- **任务快照**：`AuditTask` 冻结审核类型和 Profile，避免后续配置变更影响历史任务的可追溯性。
- **尝试与运行**：任务可有多个 `AuditAttempt`；每个 attempt 为每个注册 Agent 建立一个 `AgentRun`，记录该 Agent 的 Gateway 会话、状态、错误与专属结果路径。
- **结果工件**：每个 `AgentRun` 原子交付一个 `AgentResult` 文件：`findings/<dimension>[.<scope>].findings.json`。只有匹配 `*.findings.json` 的最终文件会被扫描；临时文件不参与收集。
- **审核结论与证据**：`AgentResult` 包含统一契约的 `Finding[]`，每个 Finding 可引用一个或多个 `EvidenceRef`，对应当前 attempt 的证据包。

## 目录映射

```text
<task_id>/<attempt_id>/
├── input-manifest.json
├── findings/
│   ├── content.findings.json
│   └── architecture.deployment.findings.json
└── evidence/
    └── audit-evidence.json
```

`content.findings.json` 的结果 metadata 为 `dimension=content`、`scope=null`；`architecture.deployment.findings.json` 则为 `dimension=architecture`、`scope=deployment`。程序会将文件名、AgentRun 配置和结果 metadata 三者交叉校验。
