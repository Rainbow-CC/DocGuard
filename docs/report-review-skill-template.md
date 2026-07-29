# 报告审核 Agent 扩展模板

本模板供平台工程师新增报告审核类型时使用；它不属于 Agent 的运行时输入。

新增的 Agent skill 应具有以下 front matter：

```yaml
---
name: docx-<report-type>-audit
description: 使用 DocGuard 通用 DOCX 证据流水线审核 <报告类型>，并原子交付结构化 findings。
---
```

## 固定流程

1. 只读 `INPUT_DOCX`，用 core 的 `extract_docx_structure.py` 以接受修订视图提取文档。
2. 用 core 的 `build_audit_packet.py` 生成 `audit-context.md` 和 `audit-evidence.json`，并交付渲染图件。
3. 读取 core 的 `references/finding-contract.md`、`references/agent-result-contract.md`，再读取 `DOCGUARD_RULE_PACK` 指向的规则包。
4. 仅当 `DOCGUARD_VISUAL_POLICY` 启用时，使用规则包提供的视觉模板与事实 Schema 做图件理解。
5. 只按平台 Finding 契约构造 `findings`；不得添加类型私有字段或输出 Markdown 报告。
6. 将完整结果写入临时文件，运行 core 的 `validate_findings.py`，成功后原子重命名到 `DOCGUARD_RESULT_FILE`。

## 可变内容

每个新类型只新增以下内容：

```text
review-packs/<report-type>/
├── review-rules.md
├── visual-policy.yaml
└── 可选的视觉提示词和事实 Schema
```

不得复制或修改 core 的提取、证据、Finding 校验与原子交付逻辑。规则包版本必须与任务 manifest 的审核类型快照一致。
