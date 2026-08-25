---
name: docx-tech-architecture-audit
description: 使用 DocGuard 通用 DOCX 证据流水线审核技术架构报告，并将结构化 findings 原子交付给 DocGuard。
---

# DOCX 技术文档审核

仅做只读分析；不得用重写库保存源 DOCX。所有 Shell 命令使用 `set -euo pipefail`。每阶段先验证上阶段产物存在且非空；输入路径已明确时只验证该路径，不再全盘搜索或重复列目录。

## DocGuard Runtime I/O（强制）

本 skill 由 DocGuard 的长任务 worker 调用。调用提示会提供以下值；不得自行猜测、替换或创建其他任务目录：

- `DOCGUARD_TASK_ID`、`DOCGUARD_ATTEMPT_ID`：本次交付身份。
- `DOCGUARD_AUDIT_MANIFEST`：只读输入 manifest，含任务身份、文档引用、Profile 与审核类型快照。
- `DOCGUARD_RESULT_FILE`：唯一允许交付的最终文件，固定以 `findings.json` 结尾。
- `DOCGUARD_EVIDENCE_DIR`：应用已交付的只读证据包目录。
- `DOCGUARD_WORK_DIR`：应用已交付的只读审计上下文、渲染图件和视觉结果目录。

`DOCGUARD_RESULT_FILE` 的父目录由应用预先创建，并只授予本 attempt 写权限。输入 DOCX、manifest、审计包和图件必须只读。禁止写入 `$HOME`、其他任务目录或任意未声明目录。

最终交付物不是 Markdown 报告。应用负责合并、证据校验、编号和 Markdown/PDF 渲染；Agent 只交付符合 [references/finding-contract.md](references/finding-contract.md) 的结构化 JSON。

## 应用托管预处理（强制）

DocGuard 应用负责在启动 Agent 前，在同一 attempt 的 Linux 工作目录执行 DOCX 接受修订提取、审计包构建、视觉提示词构建和逐图视觉事实提取。应用会将以下只读产物写入 `DOCGUARD_RESULT_FILE` 同级的 `work/`：

- `work/audit-context.md`、`work/audit-evidence.json`；
- `work/extracted/rendered/` 的原始渲染 PNG；
- `work/vision-prompt.txt`、`work/vision-responses/<candidate-id>.raw.txt`；

Agent 必须直接使用这些产物：禁止重新运行提取、构建提示词、调用视觉模型、修改 `DOCGUARD_WORK_DIR`，或覆盖应用已写入的 `$DOCGUARD_EVIDENCE_DIR`。`.raw.txt` 是唯一的视觉事实反馈和可追溯原始响应。Agent 只写临时 findings 和最终 `findings.json`。

## 固定工作流

1. 读取应用已交付的审计包和视觉结果。

   验证 `DOCGUARD_AUDIT_MANIFEST`、`DOCGUARD_RESULT_FILE` 和 `DOCGUARD_WORK_DIR` 存在且非空；`DOCGUARD_RESULT_FILE` 必须以 `findings.json` 结尾且尚未存在。完整读取一次 `$DOCGUARD_WORK_DIR/audit-context.md`，需要机器可读证据时仅读取 `$DOCGUARD_WORK_DIR/audit-evidence.json`。`document-structure.json`、原始 DOCX 和图像渲染工具均不属于 Agent 的读取或执行范围。

   对每张已完成视觉理解的图片，读取对应的 `$DOCGUARD_WORK_DIR/vision-responses/<candidate-id>.raw.txt`。视觉响应失败时记录其局限性，但继续完成全文审核。

2. 执行全文与图文两路审核。

   读取 [references/finding-contract.md](references/finding-contract.md) 和 [review-packs/technical-architecture/review-rules.md](review-packs/technical-architecture/review-rules.md) 各一次，并严格按平台 Finding 契约及本类型规则构造每一项 finding。先审核完整的 `audit-context.md`，形成全文审核发现；不得因为没有图件而忽略全文问题。

   再对每张已取得视觉反馈的最终可见图，使用审计包中的所属章节及原始视觉反馈执行图文一致性审核。跳过视觉理解的装饰性图片不进入逐图审核。图中未体现仅表示该图未提供证据，不表示生产环境不存在；纯文本发现不得以“图中未体现”作为问题依据。

   构造 `evidence_refs` 时，逐项回查 `$DOCGUARD_WORK_DIR/audit-evidence.json`，而不是从审核记录或记忆中重写证据：

   - 一个引用只能对应一个证据项。标题和正文位于不同 block 时分别引用，禁止合并为一个 `quote`。
   - 文本和表格 `quote` 必须逐字复制对应证据项中的连续原文；禁止改写、概括、拼接不连续行或使用 `...` / `……` 代替省略内容。
   - ID 前缀必须与证据类型一致：表格使用 `table:<block_index>`，其他文本块使用 `block:<block_index>`，图片使用 `image:<image_id>`。
   - 表格的精确高亮可在 `evidence_refs[].selector` 中提供；只允许用于 `table:<block_index>`，格式为：
   
     ```json
     {
       "row_match": {"列名": "精确单元格值"},
       "columns": ["需高亮的列名"]
     }
     ```
   
     `row_match` 的列名必须来自该表表头，且非空时必须恰好匹配一条数据行；`columns` 中的列名也必须存在于表头。无需精确高亮时填 `null`。不得对段落或图片使用 `selector`。
   - 图片的精确高亮可在 `evidence_refs[].region` 中提供；只允许用于 `image:<image_id>`，格式为 `{"x": 0.05, "y": 0.20, "width": 0.40, "height": 0.15}`。四个值是相对原图宽高的 0 到 1 归一化比例，矩形不得越界。仅在对象或文字可可靠定位时使用；否则填 `null` 并说明局限性。不得对文本或表格使用 `region`。
   
3. 交付结构化 findings。

   `$DOCGUARD_EVIDENCE_DIR/audit-evidence.json` 和 `rendered/` 已由应用写入且只读。只生成临时 findings、执行校验并原子重命名；不得覆盖证据包。

   根据输入 manifest 填写 `task_id`、`attempt_id`、`input_sha256`、Profile、提示词版本、`review_type_id`、`review_type_version` 与 `core_contract_version`；不得伪造或猜测它们。`evidence_refs` 只能引用应用已交付证据包中的 `block:<索引>`、`table:<索引>` 或 `image:<图片ID>`；不要编造 ID、原文摘录或图片坐标。先生成临时文件，校验通过后才在同一文件系统原子交付：

   ```bash
   BASE="{baseDir}"
   PARTIAL_FILE="$(dirname "$DOCGUARD_RESULT_FILE")/findings.partial.json"
   # 将完整 docguard-agent-result-v1 JSON 写入 "$PARTIAL_FILE"。
   python3 "$BASE/scripts/validate_findings.py" \
     --manifest "$DOCGUARD_AUDIT_MANIFEST" \
     --evidence "$DOCGUARD_EVIDENCE_DIR/audit-evidence.json" \
     --input "$PARTIAL_FILE"
   mv "$PARTIAL_FILE" "$DOCGUARD_RESULT_FILE"
   test -s "$DOCGUARD_RESULT_FILE"
   ```

   预检会核对证据 ID、类型、原文摘录、表格 selector 和图片 region。预检失败时必须修改 `$PARTIAL_FILE` 并重新运行，禁止绕过预检或继续执行 `mv`。禁止直接写 `findings.json`，禁止交付半写入文件，禁止以聊天答复、Markdown、JSON 片段或截图替代该文件。完成后聊天最终答复只能简短确认 `findings.json` 已写入；不得在答复中重复 findings。

## 资源

- [references/finding-contract.md](references/finding-contract.md)：所有报告类型共用的 Finding 契约。
- [references/agent-result-contract.md](references/agent-result-contract.md)：所有报告类型共用的结果 envelope。
- [review-packs/technical-architecture/](review-packs/technical-architecture/)：本类型的规则和视觉事实提取资料。
