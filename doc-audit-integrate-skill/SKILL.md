---
name: docx-tech-architecture-audit
description: 使用 DocGuard 通用 DOCX 证据流水线审核技术架构报告，并将结构化 findings 原子交付给 DocGuard。
---

# DOCX 技术文档审核

仅做只读分析；不得用重写库保存源 DOCX。所有 Shell 命令使用 `set -euo pipefail`。每阶段先验证上阶段产物存在且非空；输入路径已明确时只验证该路径，不再全盘搜索或重复列目录。

## DocGuard Runtime I/O（强制）

本 skill 由 DocGuard 的长任务 worker 调用。调用提示会提供以下值；不得自行猜测、替换或创建其他任务目录：

- `INPUT_DOCX`：只读 DOCX 路径。
- `DOCGUARD_TASK_ID`、`DOCGUARD_ATTEMPT_ID`：本次交付身份。
- `DOCGUARD_AUDIT_MANIFEST`：只读输入 manifest，含任务身份、文档引用、Profile 与审核类型快照。
- `DOCGUARD_RESULT_FILE`：唯一允许交付的最终文件，固定以 `findings.json` 结尾。
- `DOCGUARD_EVIDENCE_DIR`：应用提供的证据包交付目录。必须在提交 findings 前写入 `audit-evidence.json` 和渲染图片；不得写入其他目录。

`DOCGUARD_RESULT_FILE` 的父目录由应用预先创建，并只授予本 attempt 写权限。输入 DOCX、manifest、审计包和图件必须只读。禁止写入 `$HOME`、其他任务目录或任意未声明目录。

最终交付物不是 Markdown 报告。应用负责合并、证据校验、编号和 Markdown/PDF 渲染；Agent 只交付符合 [references/finding-contract.md](references/finding-contract.md) 的结构化 JSON。

## 固定工作流

1. 以“接受修订”视图提取文档并建立审计包。

   将 `INPUT_DOCX` 设为当前运行时提供的实际 `.docx` 路径。先验证该路径存在且后缀为 `.docx`。工作目录只能位于最终结果文件的父目录下，所有中间产物均保存其中；不得修改源 DOCX。

   ```bash
   set -euo pipefail
   BASE="{baseDir}"
   : "${INPUT_DOCX:?INPUT_DOCX is required}"
   : "${DOCGUARD_TASK_ID:?DOCGUARD_TASK_ID is required}"
   : "${DOCGUARD_ATTEMPT_ID:?DOCGUARD_ATTEMPT_ID is required}"
   : "${DOCGUARD_AUDIT_MANIFEST:?DOCGUARD_AUDIT_MANIFEST is required}"
   : "${DOCGUARD_RESULT_FILE:?DOCGUARD_RESULT_FILE is required}"
   RESULT_DIR="$(dirname "$DOCGUARD_RESULT_FILE")"
   WORK="$RESULT_DIR/work"
   test -f "$INPUT_DOCX"
   test "${INPUT_DOCX##*.}" = "docx"
   test -s "$DOCGUARD_AUDIT_MANIFEST"
   test "$(basename "$DOCGUARD_RESULT_FILE")" = "findings.json"
   test ! -e "$DOCGUARD_RESULT_FILE"
   mkdir -p "$WORK/vision-responses" "$WORK/reviews"
   python3 "$BASE/scripts/extract_docx_structure.py" "$INPUT_DOCX" --output "$WORK/extracted" --render-png --revision-mode accept
   python3 "$BASE/scripts/build_audit_packet.py" "$WORK/extracted/document-structure.json" \
     --context-output "$WORK/audit-context.md" \
     --evidence-output "$WORK/audit-evidence.json"
   test -s "$WORK/audit-context.md"
   test -s "$WORK/audit-evidence.json"
   ```

   默认 `--revision-mode accept` 必须用于审核：提取器跳过 `w:del` / `w:moveFrom` 及其后代，保留 `w:ins` / `w:moveTo`，因此已删除的段落、表格行、图片和 OLE 预览不得进入审计包或视觉审核。原 DOCX 始终只读；如需排查历史版本，仅可显式使用 `--revision-mode all` 生成另一份独立审计包，且不得将其作为最终版本审核依据。

   `document-structure.json` 是仅供脚本重建审计包的内部提取记录，审核 agent 禁止读取它。审核阶段的唯一文档入口是 `audit-context.md`：先完整读取一次，再进行全文和图片审核。需要机器可读的完整证据时，仅读取 `audit-evidence.json`。候选图均应保留，除非没有 `rendered_png_file`。

2. 仅构造一次视觉提示词，并按需逐图提取事实。

   `vision-prompt.txt` 是视觉事实提取阶段的运行时提示词，不是用户提供的输入文件。通过将当前审核类型规则包的单图事实提取模板中的 Schema 占位符替换为对应事实 Schema 生成。整份文档只生成一次，随后原样用于需要理解的图件。

   ```bash
   python3 "$BASE/scripts/build_vision_prompt.py" \
     --template "$BASE/review-packs/technical-architecture/vision-prompt.md" \
     --schema "$BASE/review-packs/technical-architecture/vision-facts.schema.json" \
     --output "$WORK/vision-prompt.txt"
   test -s "$WORK/vision-prompt.txt"
   ```

   保留全部最终可见候选图，但仅将与技术架构审核可能相关的图片发送给视觉理解模型。可根据图题、附近文本、文件名或图片可见内容跳过明显的企业 Logo、品牌标识、装饰图、分隔图和纯图标；在审核记录中注明跳过原因。无法合理判断是否相关时，发送给视觉理解模型。

   **图片原样使用（强制）：** 禁止裁剪、缩放、旋转、拼接、标注、增强、压缩、重编码或以任何方式修改 `rendered_png_file`。视觉理解模型必须直接接收提取工具渲染产生的原始图片文件。

   ```bash
   mmx vision describe --image "$IMAGE_FILE" --prompt "$(cat "$WORK/vision-prompt.txt")" --output json --quiet --timeout 240 > "$WORK/vision-responses/<candidate-id>.raw.txt"
   ```

   视觉阶段只发送图片和视觉提示词，不发送章节正文，也不要求质量判断。不得依据 Schema 严格校验视觉响应；将模型原始响应作为视觉反馈。若出现网络超时等调用失败，则重试1次，仍然失败则调用 image 工具进行视觉理解，若仍然失败，不进行重试，直接在输出文件中写入"图片理解失败".

3. 执行全文与图文两路审核。

   读取 [references/finding-contract.md](references/finding-contract.md) 和 [review-packs/technical-architecture/review-rules.md](review-packs/technical-architecture/review-rules.md) 各一次，并严格按平台 Finding 契约及本类型规则构造每一项 finding。先审核完整的 `audit-context.md`，形成全文审核发现；不得因为没有图件而忽略全文问题。

   再对每张已取得视觉反馈的最终可见图，使用审计包中的所属章节及原始视觉反馈执行图文一致性审核。跳过视觉理解的装饰性图片不进入逐图审核。图中未体现仅表示该图未提供证据，不表示生产环境不存在；纯文本发现不得以“图中未体现”作为问题依据。

   每个问题只能在最终 `findings` 数组中出现一次。相同根因的图文问题必须合并，使用稳定且可解释的 `root_cause_key`。中间审核记录保存到 `$WORK/reviews/`。

   构造 `evidence_refs` 时，逐项回查 `$WORK/audit-evidence.json`，而不是从审核记录或记忆中重写证据：

   - 一个引用只能对应一个证据项。标题和正文位于不同 block 时分别引用，禁止合并为一个 `quote`。
   - 文本和表格 `quote` 必须逐字复制对应证据项中的连续原文；禁止改写、概括、拼接不连续行或使用 `...` / `……` 代替省略内容。
   - ID 前缀必须与证据类型一致：表格使用 `table:<block_index>`，其他文本块使用 `block:<block_index>`，图片使用 `image:<image_id>`。

4. 交付结构化 findings。

   根据输入 manifest 填写 `task_id`、`attempt_id`、`input_sha256`、Profile、提示词版本、`review_type_id`、`review_type_version` 与 `core_contract_version`；不得伪造或猜测它们。必须先将可展示的审计包交付给应用：`audit-evidence.json` 写入 `$DOCGUARD_EVIDENCE_DIR`，并将 `$WORK/extracted/rendered/` 原样复制为 `$DOCGUARD_EVIDENCE_DIR/rendered/`。`evidence_refs` 只能引用其中的 `block:<索引>`、`table:<索引>` 或 `image:<图片ID>`；不要编造 ID、原文摘录或图片坐标。先生成临时文件，校验通过后才在同一文件系统原子交付：

   ```bash
   PARTIAL_FILE="$RESULT_DIR/findings.partial.json"
   mkdir -p "$DOCGUARD_EVIDENCE_DIR"
   cp "$WORK/audit-evidence.json" "$DOCGUARD_EVIDENCE_DIR/audit-evidence.json"
   cp -a "$WORK/extracted/rendered" "$DOCGUARD_EVIDENCE_DIR/rendered"
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
