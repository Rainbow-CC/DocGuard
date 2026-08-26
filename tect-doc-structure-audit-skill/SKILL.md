---
name: docx-tech-format-audit
description: 使用 DocGuard 证据流水线，基于 block-formatting-context.json 审核技术文档的文本结构与格式，并原子交付结构化 Findings。
---

# DOCX 技术文档格式与结构审核

仅做只读分析；不得用重写库保存源 DOCX。所有 Shell 命令使用 `set -euo pipefail`。每阶段先验证上阶段产物存在且非空；输入路径已明确时只验证该路径，不再全盘搜索或重复列目录。

## DocGuard Runtime I/O（强制）

本 Skill 由 DocGuard 的长任务 worker 调用。调用提示会提供以下值；不得自行猜测、替换或创建其他任务目录：

- `DOCGUARD_TASK_ID`、`DOCGUARD_ATTEMPT_ID`：本次交付身份。
- `DOCGUARD_AUDIT_MANIFEST`：只读输入 manifest，含任务身份、文档引用、Profile 与审核类型快照。
- `DOCGUARD_RESULT_FILE`：唯一允许交付的最终文件，固定以 `.findings.json` 结尾，例如 `findings/format.findings.json`。
- `DOCGUARD_EVIDENCE_DIR`：应用已交付的只读证据包目录。
- `DOCGUARD_WORK_DIR`：应用已交付的只读审核上下文目录。

`DOCGUARD_RESULT_FILE` 的父目录由应用预先创建，并只授予本 attempt 写权限。输入 DOCX、manifest、审计包和工作目录内产物必须只读。禁止写入 `$HOME`、其他任务目录或任意未声明目录。

最终交付物不是 Markdown 报告。应用负责合并、证据校验、编号和 Markdown/PDF 渲染；Agent 只交付符合 [references/finding-contract.md](references/finding-contract.md) 与 [references/agent-result-contract.md](references/agent-result-contract.md) 的结构化 JSON。

## 应用托管预处理（强制）

DocGuard 应用会在启动 Agent 前，将下列只读产物写入同一 attempt 的工作目录：

- `work/extracted/block-formatting-context.json`：唯一的文档审核来源；该文件是 `schema_version: "2.0"`、`content_scope: "text_only"` 的文本章节树。
- `work/audit-evidence.json`：仅用于最终交付前的证据 ID 与原文校验，不得用于发现或补充审核问题。

Agent 不得重新运行提取、构建上下文、修改 `DOCGUARD_WORK_DIR`，或覆盖应用写入的 `$DOCGUARD_EVIDENCE_DIR`。除提交结果所需的证据校验外，不得读取原始 DOCX、`document-structure.json`、`block-formatting.json`、表格、图片、渲染图、视觉结果或外部资料。

## 固定工作流

1. 验证并读取上下文。

   验证 `DOCGUARD_AUDIT_MANIFEST`、`DOCGUARD_RESULT_FILE` 和 `DOCGUARD_WORK_DIR` 存在且非空；`DOCGUARD_RESULT_FILE` 必须以 `.findings.json` 结尾且尚未存在。完整读取一次 `$DOCGUARD_WORK_DIR/extracted/block-formatting-context.json`，确认其包含根级 `items`。文件缺失、为空或 schema 不符时，停止审核并报告输入问题；不得回退读取其他文档产物。

2. 执行文本结构与格式审核。

   读取 [references/finding-contract.md](references/finding-contract.md)、[references/agent-result-contract.md](references/agent-result-contract.md) 和 [review-packs/technical-architecture/review-rules.md](review-packs/technical-architecture/review-rules.md)。规则文件是唯一的审核规则来源。

   按原始顺序递归遍历上下文的 `items`：

   - `kind: "section"` 表示章节；使用 `level`、`title` 和嵌套 `items` 审核标题层级及标题格式。
   - `kind: "paragraph"` 表示正文；使用 `text`、`block_index`、`paragraph` 和 `runs[].font` 审核正文文本、段落间距、对齐、缩进和字体等规则涉及的属性。
   - 标题和正文均可作为文本证据。仅使用其 `block_index` 与上下文中连续、逐字一致的原文构造 `block:<block_index>` 证据。

   仅执行规则文件中已由规则维护者填写完整的规则。上下文未提供的信息不得推断，也不得从其他文件补证。表格、图片、嵌入对象和空文本段落不在审核范围内，不得因其缺失产生 Finding。

3. 交付结构化 Findings。

   `$DOCGUARD_EVIDENCE_DIR/audit-evidence.json` 仅在这一阶段用于核对 `block:<索引>` 的证据 ID 和精确原文。不得通过它新增审核范围或发现。只生成临时 findings、执行校验并原子重命名；不得覆盖证据包。

   根据输入 manifest 填写 `task_id`、`attempt_id`、`input_sha256`、Profile、提示词版本、`review_type_id`、`review_type_version` 与 `core_contract_version`；并根据 `DOCGUARD_DIMENSION`、`DOCGUARD_SCOPE`、`DOCGUARD_AGENT_ID`、`DOCGUARD_AGENT_VERSION` 和模型引用填写 Agent metadata，不得伪造或猜测它们。`evidence_refs` 仅可引用上下文中出现的 `block:<索引>`；不得使用表格或图片证据。先生成临时文件，校验通过后才在同一文件系统原子交付：

   ```bash
   BASE="{baseDir}"
   PARTIAL_FILE="${DOCGUARD_RESULT_FILE}.tmp"
   # 将完整 docguard-agent-result-v1 JSON 写入 "$PARTIAL_FILE"。
   python3 "$BASE/scripts/validate_findings.py" \
     --manifest "$DOCGUARD_AUDIT_MANIFEST" \
     --evidence "$DOCGUARD_EVIDENCE_DIR/audit-evidence.json" \
     --input "$PARTIAL_FILE"
   mv "$PARTIAL_FILE" "$DOCGUARD_RESULT_FILE"
   test -s "$DOCGUARD_RESULT_FILE"
   ```

   预检会核对证据 ID、类型和原文摘录。预检失败时必须修改 `$PARTIAL_FILE` 并重新运行，禁止绕过预检或继续执行 `mv`。禁止直接写最终 `*.findings.json`，禁止交付半写入文件，禁止以聊天答复、Markdown、JSON 片段或截图替代该文件。完成后聊天最终答复只能简短确认结果文件已写入；不得在答复中重复 Findings。

## 资源

- [references/finding-contract.md](references/finding-contract.md)：平台 Finding 契约。
- [references/agent-result-contract.md](references/agent-result-contract.md)：平台结果 envelope。
- [review-packs/technical-architecture/review-rules.md](review-packs/technical-architecture/review-rules.md)：由规则维护者填写的文本结构与格式审核规则。
