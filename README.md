# DocGuard

面向 DOCX 技术文档的证据驱动审核服务骨架。它坚持一条边界：**agent 只输出结构化审核判断 `Finding[]`；程序负责文件处理、流程控制、证据校验与最终报告。**

当前版本默认使用 `openclaw` 审核执行器；它通过 Gateway 的 OpenResponses SSE 启动长任务，Agent 将结构化结果原子写入共享 `findings.json`，应用随后校验并渲染报告。确定性的 `stub` 执行器仍可用于本地开发与测试。

## 技术架构

```mermaid
flowchart LR
    API["FastAPI 控制面"] --> Q["任务调度/Worker"]
    Q --> AG["OpenClaw SSE 执行 skill<br/>DOCX 提取与审核"]
    AG --> AR["原子交付 findings.json"]
    AR --> MG["应用收集、校验与去重"]
    MG --> CV["Finding JSON 契约校验"]
    CV --> RR["程序化 Markdown/PDF 渲染"]
    RR --> UI["任务详情<br/>报告、证据与运行记录"]
    Q --> LG["LangGraph Stub 编排"]
    API --> DB[("SQLite：任务状态/Finding/报告")]
```

### 分层职责

| 层 | 职责 | 当前实现 | 生产替换 |
| --- | --- | --- | --- |
| FastAPI | 创建任务、查询状态、鉴权、展示下载 | `api/app.py` | 保持接口，补鉴权与分页 |
| Worker | 领取长任务、重试、限流、租约 | FastAPI `BackgroundTasks` 演示实现 | 独立 worker + Redis/RabbitMQ/云队列 |
| LangGraph | 节点编排、失败恢复、人工暂停恢复 | `graph/audit_graph.py` | `PostgresSaver` checkpoint |
| 审计 Skill | 接受修订、DOCX/表格/图件提取、建立审计包与审核 | OpenClaw Agent | 受限运行时 + 独立审计包存储 |
| 同步审核 Gateway | 向 LangGraph 返回 `Finding[]` | Stub/LangChain 接口 | 结构化输出执行器 |
| OpenClaw 调度器 | 启动 artifact-delivered attempt 并记录 Gateway SSE | `OpenClawAgentGateway` | 队列 worker + 重试 |
| 工件收集 | 读取并校验 `findings.json` | 共享 WSL 目录 | 对象存储事件/队列 |
| 存储 | 任务元数据、状态、Finding 与报告 | SQLite `data/docguard.sqlite3` | PostgreSQL + S3/MinIO |
| 报告 | Findings 渲染及下载 | Markdown | 固定模板 Markdown/PDF |

LangGraph 负责同步的 Stub 编排；OpenClaw 负责审核判断和工件交付；校验器和渲染器负责什么结果可接受以及如何呈现。不要让模型直接输出最终报告。

### 审核图（`src/docguard/graph/audit_graph.py`）

```mermaid
flowchart TD
    START((START)) --> PP[preprocess<br/>接受 DOCX、提取内容并创建 evidence]
    PP --> TA[full_text_audit<br/>全文审核]
    TA --> AA[architecture_audit<br/>架构审核]
    AA --> MG[merge<br/>按 root_cause_key 合并去重]
    MG --> CV[validate<br/>校验 Finding 引用的 evidence ID]
    CV --> RR[render<br/>渲染 Markdown 报告]
    RR --> END((END))
```

## 核心数据契约

`ReviewTypeDefinition` 是平台可选报告审核类型的版本化元数据，保存于 SQLite；应用启动时加载启用类型，主页以下拉框展示。它绑定 OpenClaw Agent/skill、规则包、视觉策略与核心契约版本。创建任务时会冻结完整定义和 `AuditProfile` 快照，保证重跑可复现。

所有审核类型必须共用 DOCX 提取、`audit-context.md`、`audit-evidence.json` 和严格的 `Finding` 契约。类型扩展仅增加 Agent/skill 和规则包，不能分叉证据或结果协议。技术架构审核是内置种子类型；工程师可参考 [`报告审核 Agent 扩展模板`](docs/report-review-skill-template.md) 创建新 skill。

OpenClaw Agent 负责提取 DOCX、建立审计包并生成可定位证据。新 `Finding` 使用结构化 `evidence_refs` 引用审计包中的 block、表格或图片；应用校验引用 ID、原文摘录、表格选择器与图片区域，并在任务详情中展示可复核的原文/图件。兼容既有 `evidence_ids`，但旧工件不会获得新证据阅读器的定位能力。

建议生产环境为每个任务冻结：输入文件哈希、Profile 快照、提示词版本、模型引用、审计包 manifest、运行 ID 和原始模型响应 URI。

### Artifact 交付路径

OpenClaw attempt 使用应用与 Agent 共享的结果根目录。应用侧通过
`DOCGUARD_RESULT_WRITE_ROOT` 配置该目录（默认：
`\\wsl.localhost\\Ubuntu\\home\\ubuntu\\docguard-results`），Agent 侧通过
`DOCGUARD_RESULT_AGENT_ROOT` 使用对应的 WSL 路径（默认：
`/home/ubuntu/docguard-results`）。每次任务和 attempt 的目录结构固定如下：

```text
<result-root>/
└── <task_id>/
    └── <attempt_id>/
        ├── input-manifest.json
        ├── findings.json
        └── evidence/
            ├── audit-evidence.json
            └── rendered/
                └── *.png
```

其中，`input-manifest.json` 由应用创建，`audit-evidence.json` 和 `rendered/` 由
OpenClaw skill 交付，`findings.json` 是 Agent 最终交付的结构化结果。Agent 必须
先写入同目录临时文件并校验成功，再通过原子重命名交付 `findings.json`；应用检测到
`findings.json` 后，会读取同一 attempt 下的 `evidence/audit-evidence.json`，校验
`evidence_refs` 是否引用真实证据，最后合并 Finding 并生成报告。

证据引用只能使用当前审计包中的 `block:<block_index>`、`table:<block_index>` 或
`image:<image_id>`。图片文件必须位于该 attempt 的 `evidence/rendered/` 目录内；应用
不会把内部文件路径直接暴露给浏览器，而是通过证据接口生成受控图片 URL。完整的交付
约束见 [`doc-audit-integrate-skill/SKILL.md`](doc-audit-integrate-skill/SKILL.md)。

## 快速开始

需安装 [uv](https://docs.astral.sh/uv/) 和 Python 3.12+。

```bash
uv sync --group dev
uv run fastapi dev src/docguard/api/app.py
```

创建任务：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"review_type_id":"technical-architecture","document":{"filename":"方案.docx","content_sha256":"<64位SHA256>","source_uri":"s3://audit-input/方案.docx"}}'
```

运行检查：

```bash
uv run pytest
uv run ruff check .
```

## 接入真实审核能力

1. OpenClaw skill 负责接受修订后的 DOCX、建立审计包和生成证据；生产环境应将其工作目录与审计包保留策略纳入对象存储和审计日志。
2. `OpenClawAgentGateway` 已通过 `POST /v1/responses` 的 SSE 启动审核，并保存 Gateway response ID；生产 worker 应在 SSE 中断后进入 `collecting` 并轮询该 attempt 的结果工件，而非立即重发。
3. 实现 `LangChainAgentGateway`：使用模型的结构化输出能力将输出直接反序列化为 `Finding[]`，禁止返回 Markdown。
4. 将 SQLite 任务库替换为 PostgreSQL；将 `BackgroundTasks` 替换为独立队列 worker，并周期性调用 `collect_pending()`；为 LangGraph 配置持久化 checkpointer 和保留策略。
5. 补充人工复核节点、任务级重跑、RBAC、对象存储签名访问、评测集及可观测性。

### OpenClaw 安全边界

审核 worker 应当给 OpenClaw 最小工具权限：只读指定审计包与图件、仅允许调用审核模型、仅可写入指定结果目录。不要默认暴露 shell、浏览器、消息发送、任意文件写入或外网写能力。

## 项目结构

```text
src/docguard/
├── api/          # FastAPI 控制面
├── adapters/     # OpenClaw/LangChain/Stub 执行器
├── domain/       # 版本化数据契约
├── graph/        # LangGraph 工作流
└── services/     # Profile、存储、任务、报告
tests/            # 最小工作流回归测试
```

## 当前边界

该仓库是框架而不是完整 DOCX 审核产品。DOCX 接受修订、解析、渲染、PDF 输出、对象存储、队列与真实模型调用尚未实现；本地 SQLite 已用于持久化任务状态，它们都已经有明确的扩展位置，且不会破坏已固定的任务、证据、finding 和报告边界。
