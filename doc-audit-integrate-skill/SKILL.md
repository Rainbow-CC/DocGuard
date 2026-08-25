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

   对每张需要理解的候选图，直接使用当前会话可用的图片理解能力，将 `rendered_png_file` 作为原始图片附件，并将 `$WORK/vision-prompt.txt` 的完整内容作为唯一提示词发送给视觉模型。

   - 当前模型支持视觉输入时，优先直接由当前模型完成理解；不要求、也不得调用特定供应商 CLI 或固定命令。
   - 每次请求只能发送一张候选图片；图片必须直接使用 `rendered_png_file` 的原始文件，不得裁剪、缩放、旋转、拼接、转码或添加标注。
   - 请求中仅包含图片和 `vision-prompt.txt` 的内容；不得附加章节正文、审核结论、评分要求或其他上下文。
   - 将每张图的完整原始模型响应写入 `$WORK/vision-responses/<candidate-id>.raw.txt`，供后续图文一致性审核使用。
   - 若图片理解请求失败，可使用当前会话中另一项可用的图片理解能力重试 1 次；两次均失败时，在对应 `.raw.txt` 中写入“图片理解失败”，并继续处理其他候选图。

   视觉阶段只发送图片和视觉提示词，不发送章节正文，也不要求质量判断。不得依据 Schema 严格校验视觉响应；将模型原始响应作为视觉反馈。

3. 执行全文与图文两路审核。

   **AI 自主决定审核范围**（不依赖关键词触发）：

   - 完整通读 `$WORK/audit-context.md`（章节 + 表格 + 图片清单 + 关联关系），不预设重点。
   - 读取 `$BASE/review-packs/routing.json` 得到候选 pack 清单（每个 pack 仅含 `pack_id` / `display_name` / `rule_pack`，部分含 `always: true`）。
   - 根据通读结果**自主判断**每个 pack 是否适用。判断必须给出可事后追溯的 `reasoning`（章节引用、表格 ID、图片 ID 等），不得仅写"看起来相关"等模糊理由。
   - 写到 `$WORK/routing-decision.json`：

     ```json
     {
       "schema_version": "routing-decision-v1",
       "shared": ["technical-architecture/shared.md"],
       "packs": [
         {"pack_id": "system-metrics", "rule_pack": "...", "reasoning": "第3章包含 RPO/RTO/响应时间/并发用户数等指标"},
         ...
       ],
       "fallback": false
     }
     ```

   - **`fallback` 的语义（强制）**：
     - `fallback=false` 是默认状态，表示至少有一个 pack 适用。
     - `fallback=true` 仅用于**全部候选 pack 均不适用**的极端情况；此时 `packs=[]` 且 `findings=[]`，`meta.fallback_reason` 必填。
     - **禁止用 `fallback=true` 跳过无关 pack**——`fallback=true` 仅表示"零 pack 适用"，不能用作"未严格审视每个 pack"的兜底。

   **必查项清单（硬性约束，防止漏检）**：

   不管 routing-decision.json 写什么，**以下 14 类问题必须被审核**（文档中确实不涉及的明确写"不涉及"+ 证据；缺失或错误的必须报告为 finding）：

   | # | 必查项 | 对应规则 | 触发信号 |
   |---|---|---|---|
   | 1 | 系统编码（XYYYNN 格式）| AP-A01 | 系统基本描述表无系统编码字段 |
   | 2 | 并发率在 5%-20% | AP-B08 | 用户说明表的并发数/总数比值 |
   | 3 | RPO 应对措施含"代码配置单独存放"| AP-B09 | C 级系统的 RPO 措施描述 |
   | 4 | RTO 应对措施含"本地冗余/单点故障"| AP-B10 | C 级系统的 RTO 措施描述 |
   | 5 | 响应时间含"流程申请/审批类"| AP-B11 | 系统指标表响应时间行 |
   | 6 | 应用服务器单节点业务影响说明 | AP-C01 | 节点表应用服务器行数 = 1 |
   | 7 | 数据库节点 ≥ 2 + 主从关系 | AP-C02 | 节点表数据库行数 + 主从/集群字段 |
   | 8 | 应用协作表含集成方式 | AP-C04 | 应用协作表列结构 |
   | 9 | 设计指导 ≥ 2 条原则 | AP-C06 | 设计指导表行数 |
   | 10 | 资源池标注容器云平台 | AP-C08 | 资源池使用情况表 |
   | 11 | 节点表字段全部非空 | AP-C09 | 节点表 N 行各列 |
   | 12 | 软件版本在版本序列内 | AP-D02 | 系统软件/中间件/数据库版本号 |
   | 13 | 4.5 第三方软件选型安全分析 | AP-D06 | 4.5 章节存在性 |
   | 14 | 4.7 密码算法应用方案 | AP-E01 | 4.7 章节存在性 |

   **Finding 粒度标准（统一拆题粒度）**：

   - 同一规则的多个独立问题应**拆为多个 Finding**（如 AP-A01 缺字段 + 字段值不符 = 2 个 Finding）。
   - 同一问题出现在多个证据项时**只算 1 个 Finding**（合并所有 evidence_refs）。
   - 重大级别问题必须**单列**，不与其他问题合并。
   - 涉及图文不一致时，**图与文各算 1 个 Finding**，不合并。
   - `root_cause_key` 仅用于标识单条 Finding 的稳定、可解释根因，**不作为合并依据**。

   **视觉审核必查项（减少视觉审核随机性）**：

   对每张已取得视觉反馈的最终可见图，**必须逐项检查**以下一致性：
   1. **节点数一致性**：图上节点实例数（如圆圈数字"2"） vs 节点配置表行数。
   2. **命名一致性**：图上组件名 vs 文字描述中的组件名（"微办公" vs "微聊"）。
   3. **协议/集成方式一致性**：图上协议标签 vs 文字描述的协议（"HTTPS" vs "HTTP"）。
   4. **版本/比例一致性**：图上标注版本号 vs 文字描述版本号。
   5. **方向/拓扑一致性**：箭头方向、连接关系 vs 文字描述的依赖关系。`

   ```json
   {
     "schema_version": "routing-decision-v1",
     "shared": ["technical-architecture/shared.md"],
     "packs": [
       {"pack_id": "...", "rule_pack": "...", "reasoning": "本章包含 RPO/RTO 与并发用户数，匹配 B 类"},
       ...
     ],
     "fallback": false
   }
   ```

   判定依据可包括对 `audit-context.md` 章节内容的引用（如"3.2 系统指标 章节首段提到 RPO=300 秒"），便于事后追溯；判定过程不读取 `$WORK/extracted/` 或全文证据，仅依赖 `audit-context.md` 已经汇总的章节与小节标题、表格、图片清单。

   **`fallback` 的语义（强制）**：
   - `fallback=false` 是默认状态，表示至少有一个 pack 适用。
   - `fallback=true` 仅用于**全部候选 pack 均不适用**的极端情况；此时 `packs` 必须为空数组 `[]`，`findings` 也必须为空数组 `[]`，envelope 的 `meta.fallback_reason` 必须写明"无 pack 适用"的判定依据。
   - 任何 `display_name` 看起来与文档主题明显无关的 pack，仍应在 `packs` 中出现并给出 `reasoning` 说明不适用原因，或从候选清单中直接跳过。**禁止用 `fallback=true` 跳过无关 pack**——`fallback=true` 仅表示"零 pack 适用"，不能用作"未严格审视每个 pack"的兜底。

   随后读取 [references/finding-contract.md](references/finding-contract.md)、所有 `shared` 文件与所有 `packs[].rule_pack` 文件各一次，并严格按平台 Finding 契约及所选审核方向的规则构造每一项 finding。先审核完整的 `audit-context.md`，形成全文审核发现；不得因为没有图件而忽略全文问题。

   再对每张已取得视觉反馈的最终可见图，使用审计包中的所属章节及原始视觉反馈执行图文一致性审核。跳过视觉理解的装饰性图片不进入逐图审核。图中未体现仅表示该图未提供证据，不表示生产环境不存在；纯文本发现不得以“图中未体现”作为问题依据。

   每个可独立整改的问题在最终 `findings` 数组中只能出现一次；不得仅因 `root_cause_key` 相同而合并、删除或隐藏 Finding。`root_cause_key` 仅用于标识单条 Finding 的稳定、可解释根因。中间审核记录保存到 `$WORK/reviews/`。

   构造 `evidence_refs` 时，逐项回查 `$WORK/audit-evidence.json`，而不是从审核记录或记忆中重写证据：

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
   - 图片的精确高亮可在 `evidence_refs[].region` 中提供；只允许用于 `image:<image_id>`。仅在对象或文字可可靠定位时使用；否则填 `null` 并说明局限性。不得对文本或表格使用 `region`。

**审核阶段中断保护（强制 checkpoint，防止 agent 被切断丢失工作）**

   为防止 agent 在审核中途被 OpenClaw 切断（超时、配额、SSE 中断、max_steps 触顶等）导致前序工作全部丢失，必须按以下规则 checkpoint：

   **写入时机（任意一条触发即写一次）**：
   - 每完成**一个 pack** 的 findings 追加后立即写一次。
   - validate 每次失败后、写 `$WORK/reviews/validate-retry.log` 之前先写。
   - 准备退出 agent 之前（包括任何“看起来快完成了”、“已构造 N 个 finding” 的判断）写一次最终 checkpoint。
   - **上下文超长预感**：当 agent 判断 remaining context 不够再构造一个 finding 时，先 checkpoint 再退出。

   **Checkpoint 文件**：`$WORK/reviews/findings-checkpoint.json`，结构：

   ```json
   {
     "schema_version": "findings-checkpoint-v1",
     "task_id": "<DOCGUARD_TASK_ID>",
     "attempt_id": "<DOCGUARD_ATTEMPT_ID>",
     "completed_packs": ["system-coding-naming", "system-metrics"],
     "findings_so_far": [ /* 已构造的完整 finding 数组 */ ],
     "last_updated_at": "2026-08-25T14:43:06+0800",
     "next_pack": "architecture-deployment",
     "interrupted": false
   }
   ```

   **原子写**（用 python 临时文件 + `os.replace` 避免半写）：

   ```bash
   python3 - <<'PY'
   import json, datetime, os
   ckpt = {
     "schema_version": "findings-checkpoint-v1",
     "task_id": "$DOCGUARD_TASK_ID",
     "attempt_id": "$DOCGUARD_ATTEMPT_ID",
     "completed_packs": ["..."],
     "findings_so_far": [...],
     "last_updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
     "next_pack": "...",
     "interrupted": False,
   }
   tmp = "$WORK/reviews/findings-checkpoint.json.tmp"
   with open(tmp, "w", encoding="utf-8") as f:
       json.dump(ckpt, f, ensure_ascii=False, indent=2)
   os.replace(tmp, "$WORK/reviews/findings-checkpoint.json")
   PY
   ```

   **重启续写规则**（attempt 启动后**第一件事**）：
   - 读 `$WORK/reviews/findings-checkpoint.json`（若存在）：
     - 提取 `completed_packs` 跳过这些 pack，从 `next_pack` 开始构造。
     - 把 checkpoint 的 `findings_so_far` 作为初始 findings 数组，新构造的 finding **追加**到末尾（**不得重写已有 finding**）。
   - 不存在则按全新流程跑。
   - `interrupted=true` 优先恢复续写，不重跑已完成 pack（避免重复审核浪费 token）。
   - 写 `routing-decision.json` 时若 `interrupted=true`，把 `completed_packs` 也写进去，便于事后追溯。

   **Checkpoint 清理**：
   - validate 通过 + `mv "$PARTIAL_FILE" "$DOCGUARD_RESULT_FILE"` 成功后**立即删除** checkpoint，防止下次 attempt 误读。
   - 失败兜底（`findings=[]` + `meta.error`）交付后，checkpoint 保留 7 天用于事后追溯；保留期内可作为审计证据。
   - 删除命令：`rm -f "$WORK/reviews/findings-checkpoint.json"`。

4. 交付结构化 findings。

   根据输入 manifest 填写 `task_id`、`attempt_id`、`input_sha256`、Profile、提示词版本、`review_type_id`、`review_type_version` 与 `core_contract_version`；不得伪造或猜测它们。结果 envelope 的顶层 `routing` 字段必须逐字取自 `$WORK/routing-decision.json` 的对象内容（不能是文件路径字符串）。必须先将可展示的审计包交付给应用：`audit-evidence.json` 写入 `$DOCGUARD_EVIDENCE_DIR`，并将 `$WORK/extracted/rendered/` 原样复制为 `$DOCGUARD_EVIDENCE_DIR/rendered/`。`evidence_refs` 只能引用其中的 `block:<索引>`、`table:<索引>` 或 `image:<图片ID>`；不要编造 ID、原文摘录或图片坐标。先生成临时文件，校验通过后才在同一文件系统原子交付：

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

   预检会核对证据 ID、类型、原文摘录、表格 selector 和图片 region。预检失败时必须修改 `$PARTIAL_FILE` 并重新运行，禁止绕过预检或继续执行 `mv`。禁止直接写 `findings.json`，禁止交付半写入文件，禁止以聊天答复、Markdown、JSON 片段或截图替代该文件。

   **错误恢复策略（最小可交付原则，强制）**：

   - **validate 重试上限**：预检失败时，读取 stderr 中具体出错字段，定位到 `$PARTIAL_FILE` 中对应 `evidence_refs[i]` 并修正（只改该字段，不得重写其他字段或丢失已有 findings），然后重新运行 `validate_findings.py`。最多重试 **3 次**；每次失败必须在 `$WORK/reviews/validate-retry.log` 追加一行 `[第 N 次] <错误摘要>`，便于事后追溯。
   - **validate 仍未通过**：3 次重试后仍失败时，禁止继续死循环；将 `findings` 数组置为 `[]`，在 envelope 的 `meta.error` 写入 `"validate_failed_after_3_retries: <最后一次错误摘要>"`，再跑一次 validate（空 findings 通常会通过），最后 mv 交付。**禁止绕过预检直接 mv；禁止在仍有 findings 的情况下 mv。**
   - **视觉阶段全失败**：若所有候选图的视觉响应均为"图片理解失败"、或视觉阶段脚本中断，仍须继续执行文本审核并交付 findings；图文一致性审核的部分在 `meta.warnings` 数组追加 `"vision_stage_failed_all_images"` 即可。禁止因视觉失败而中止整个交付。
   - **audit-context.md 缺章节或 pack 资源缺失**：立即 halt，并把缺失的章节路径/资源路径写入 `meta.error`，不得用空内容或别的资源替代（例：`"missing_resource: review-packs/technical-architecture/rules/B.md"`）。
   - **任何 halt 路径**：必须确保 `audit-evidence.json` 与 `rendered/` 已先写入 `$DOCGUARD_EVIDENCE_DIR`，再写入 `meta.error`，确保 DocGuard 下游能看到错误现场。

   完成后聊天最终答复只能简短确认 `findings.json` 已写入；不得在答复中重复 findings，也不得复述 validate 错误日志。

## 资源

- [references/finding-contract.md](references/finding-contract.md)：所有报告类型共用的 Finding 契约。
- [references/agent-result-contract.md](references/agent-result-contract.md)：所有报告类型共用的结果 envelope。
- [review-packs/routing.json](review-packs/routing.json)：按审核方向路由到规则包的清单；`shared` 为始终加载的共享参考数据，`packs[].rule_pack` 为相对 `review-packs/` 的类别规则文件路径，`"always": true` 表示该类别始终适用。**分类决策由审核 agent 自主判断**：按需阅读 `audit-context.md` 全文后，自行判断适用规则包并把结果（含自然语言 `reasoning`）写入 `routing-decision.json`。
- [review-packs/technical-architecture/](review-packs/technical-architecture/)：技术可行性研究报告类型的规则（`rules/` 下按审核方向 A–G 拆分）和视觉事实提取资料。

新增或调整审核方向时：在 `review-packs/technical-architecture/rules/` 下新建或编辑对应类别的规则文件，并在 `routing.json` 的 `packs` 增加或修改一条记录（仅 `pack_id` / `display_name` / `rule_pack`，跨章节始终适用时加 `"always": true`）。**不再使用 `applies_when` 关键词机制**——分类决策完全由审核 agent 自主判断。
