---
name: docx-tech-architecture-audit
description: 审核技术 DOCX 报告的全文、表格与最终可见架构图，保留可审计的 OOXML 顺序结构、修订状态、图题和嵌入图片。用于发现全文完整性与一致性问题，以及图文一致性、高可用、安全、数据流和部署声明问题。
---

# DOCX 技术文档审核

仅做只读分析；不得用重写库保存源 DOCX。所有 Shell 命令使用 `set -euo pipefail`。每阶段先验证上阶段产物存在且非空；输入路径已明确时只验证该路径，不再全盘搜索或重复列目录。

## 固定工作流

1. 以“接受修订”视图提取文档并建立审计包。

   将 `INPUT_DOCX` 设为当前会话中用户上传、附带或明确指定的实际 `.docx` 路径；`report.docx` 仅是变量示例，不能当作固定文件名。先验证该路径存在且后缀为 `.docx`。只有用户没有提供路径时，才询问用户；不得搜索磁盘猜测输入文件。

   在 `$HOME` 下创建本次运行唯一的工作目录，目录名必须为 `tech-doc-audit-<UTC时间戳>`，例如 `$HOME/tech-doc-audit-20260714T093015Z`。将提取结果、审计包、视觉原始响应、逐图审核结果和最终审核报告全部保存到该目录；不得将审核材料写入其他位置。最终审核报告固定保存为 `$WORK/audit-report.md`。

   ```bash
   set -euo pipefail
   BASE="{baseDir}"; INPUT_DOCX="<实际上传的 DOCX 路径>"
   TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
   WORK="$HOME/tech-doc-audit-${TIMESTAMP}"
   REPORT="$WORK/audit-report.md"
   test -f "$INPUT_DOCX"
   test "${INPUT_DOCX##*.}" = "docx"
   test ! -e "$WORK"
   mkdir -p "$WORK/vision-responses" "$WORK/reviews"
   python3 "$BASE/scripts/extract_docx_structure.py" "$INPUT_DOCX" --output "$WORK/extracted" --render-png --revision-mode accept
   python3 "$BASE/scripts/build_audit_packet.py" "$WORK/extracted/document-structure.json" \
     --context-output "$WORK/audit-context.md" \
     --evidence-output "$WORK/audit-evidence.json"
   test -s "$WORK/audit-context.md"
   test -s "$WORK/audit-evidence.json"
   ```

   默认 `--revision-mode accept` 必须用于审核：提取器跳过 `w:del` / `w:moveFrom` 及其后代，保留 `w:ins` / `w:moveTo`，因此已删除的段落、表格行、图片和 OLE 预览不得进入审计包或视觉审核。原 DOCX 始终只读；如需排查历史版本，仅可显式使用 `--revision-mode all` 生成另一份独立审计包，且不得将其作为最终版本审核依据。

   `document-structure.json` 是仅供脚本重建审计包的内部提取记录，审核 agent 禁止读取它。不得使用 `read`、Shell、Python、jq 或任何其他方式查看、筛选或解析该文件。

   审核阶段的唯一文档入口是 `audit-context.md`：先完整读取一次，再进行全文和图片审核。它完整保留段落、表格、章节、图片清单、附近文本、修订统计，以及 `block:<索引>` / `table:<索引>` / `image:<ID>` 证据引用。需要机器可读的完整证据时，仅读取 `audit-evidence.json`；该文件含同一套完整证据、原始二维表格行列、候选图元数据和修订模式。候选图均应保留，除非没有 `rendered_png_file`。

2. 仅构造一次视觉提示词，并按需逐图提取事实。

   `vision-prompt.txt` 是视觉事实提取阶段的运行时提示词，不是用户提供的输入文件。通过将单图事实提取模板中的 Schema 占位符替换为完整的架构事实 Schema 生成。整份文档只生成一次，随后原样用于需要理解的候选图，以保持事实提取口径一致。Schema 仅用于引导模型输出，不得作为接受视觉反馈的校验门槛。

   执行：

   ```bash
   python3 "$BASE/scripts/build_vision_prompt.py" \
     --template "$BASE/references/vision-extraction-prompt.md" \
     --schema "$BASE/references/architecture-facts.schema.json" \
     --output "$WORK/vision-prompt.txt"
   test -s "$WORK/vision-prompt.txt"
   ```

   保留全部最终可见候选图，但仅将与技术架构审核可能相关的图片发送给视觉理解模型。可根据图题、附近文本、文件名或图片可见内容跳过明显的企业 Logo、品牌标识、装饰图、分隔图和纯图标；在审计包或审核记录中注明跳过原因。无法合理判断是否相关时，发送给视觉理解模型。

   对每张需要理解且有 `rendered_png_file` 的候选图，使用候选图唯一 ID 命名并保存原始响应至 `$WORK/vision-responses/`，例如 `RAW_RESPONSE="$WORK/vision-responses/<candidate-id>.raw.txt"`：

   **图片原样使用（强制）：** 禁止裁剪、缩放、旋转、拼接、标注、增强、压缩、重编码或以任何方式修改 `rendered_png_file`。视觉理解模型必须直接接收提取工具渲染产生的原始图片文件；不得另行生成局部截图、区域裁剪图或派生图片，即使为了放大细节或减少无关内容也不允许。若原始渲染图难以辨认，记录该局限性，并基于原图调用视觉理解工具或本地 `read` 工具进行询问。

   ```bash
   mmx vision describe --image "$IMAGE_FILE" --prompt "$(cat "$WORK/vision-prompt.txt")" --output json --quiet --timeout 240 > "$RAW_RESPONSE"
   ```

   视觉阶段只发送图片和视觉提示词，不发送章节正文，也不要求质量判断。不得解析、提取 JSON 段、依据 Schema 校验或因响应不是标准 JSON 而重试；将模型原始响应作为视觉反馈。调用失败时记录局限性并继续；若模型响应失败，或者返回的内容没有提取到事实信息，可调用本地read工具读图。

3. 执行全文与图文两路审核。

   读取 [references/architecture-review-contract.md](references/architecture-review-contract.md) 一次，并严格按其 Markdown 模板输出。先审核完整的 `audit-context.md`，形成全文审核发现：章节完整性、术语/版本/范围一致性、表格一致性、需求与方案、数据安全、可用性、容量、部署、实施和运维等维度均可形成纯文本发现；不得因为没有图件而忽略全文问题。

   再对每张已取得视觉反馈的最终可见图，使用审计包中的所属章节及原始视觉反馈替换 `<TITLE>`、`<TEXT>`、`<VISION_RESPONSE>` 后进行图文一致性审核。跳过视觉理解的装饰性图片不进入逐图审核。图中未体现仅表示该图未提供证据，不表示生产环境不存在；纯文本发现不得以“图中未体现”作为问题依据。

   每个问题只能在最终报告中出现一次，并使用稳定编号：缺陷使用 `F-001` 起的连续编号，观察事项使用 `O-001` 起的连续编号。相同根因的图文问题必须合并。文本证据格式为 `第<章节号>章（<章节标题>），block:<索引>：<证据内容>`；图文问题的图片证据格式为 `第<章节号>章（<章节标题>），image:<图片ID>：<证据内容>`。章节号以 `audit-context.md` 的 `第<章节号>章` 标题及 `audit-evidence.json` 的 `chapter_number` 为准；无法归属章节时，明确写为“未归属章节”，不得猜测。

4. 以中文生成最终审核报告。

   最终审核报告必须是严谨、正式的 Markdown 文档，不得使用口语化表达、对话式措辞、表情符号或不确定的闲聊式结论。严重级别只能使用 `重大`、`一般`、`优化`、`观察`。报告必须严格遵循契约中的固定章节与问题卡片模板，禁止自由发挥章节结构，禁止以只有“摘要”而无详情的表格代替问题明细。每个缺陷必须输出分类、审核维度、判定、文本证据、图片证据（仅图文问题）、问题说明、影响、修订建议、修订位置和完成标准。将逐图审核中间结果保存在 `$WORK/reviews/`，并将最终审核报告写入 `$REPORT`；完成前验证 `$REPORT` 存在且非空。

   最终向用户回复时，仅告知审核报告已完成及其 Markdown 文件位置；不得追问、不得提出后续选项、不得邀请用户继续操作。

## 资源

- [references/vision-extraction-prompt.md](references/vision-extraction-prompt.md)：单图事实提取模板。
- [references/architecture-facts.schema.json](references/architecture-facts.schema.json)：视觉事实提取提示词的输出引导 Schema，不作响应校验。
- [references/architecture-review-contract.md](references/architecture-review-contract.md)：审核的唯一提示词与输出契约。
