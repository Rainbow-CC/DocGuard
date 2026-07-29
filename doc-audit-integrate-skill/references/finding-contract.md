# DocGuard 平台 Finding 契约

所有报告审核 Agent 必须基于 `audit-context.md`、`audit-evidence.json` 和最终可见图件交付可由 DocGuard 校验、展示和渲染的 `findings`。不得输出最终 Markdown 审核报告。

## 固定约束

- 每个 Finding 必须使用 `finding-v1` 的全部字段；不得添加私有字段：

```text
finding_id, schema_version, rule_id, category, review_dimension, judgment,
severity, confidence, title, text_evidence, image_evidence,
problem_description, impact, revision_suggestion, revision_location,
completion_criteria, evidence_ids, evidence_refs, root_cause_key, agent_backend
```

- `schema_version` 固定为 `finding-v1`，`agent_backend` 固定为 `openclaw`；`confidence` 范围为 0 至 1。

## 标准 Finding 示例

以下 JSON 是所有报告类型必须遵守的完整结构；报告类型只能替换规则标识、审核维度和业务内容，不能删改字段或添加私有字段：

```json
{
  "finding_id": "fd_<稳定标识>",
  "schema_version": "finding-v1",
  "rule_id": "<报告类型.规则标识>",
  "category": "一致性",
  "review_dimension": "一致性与可读性",
  "judgment": "文本不一致",
  "severity": "一般",
  "confidence": 0.9,
  "title": "<可独立理解的标题>",
  "text_evidence": ["第1章（概述），block:1：<完整证据>"],
  "image_evidence": ["不适用（纯文本审核）"],
  "problem_description": "<根因及范围>",
  "impact": "<对评审、实施准备或理解的影响>",
  "revision_suggestion": "<可执行动作>",
  "revision_location": "第 1 章",
  "completion_criteria": "<可验证状态>",
  "evidence_ids": ["block:1"],
  "evidence_refs": [
    {
      "evidence_id": "block:1",
      "role": "primary",
      "quote": "<原文精确摘录>",
      "explanation": "<该摘录如何支持结论>",
      "selector": null,
      "region": null
    }
  ],
  "root_cause_key": "<稳定、可解释的根因键>",
  "agent_backend": "openclaw"
}
```
- 所有结论必须有可定位的输入证据。没有明确证据时使用“不确定”，不得推断生产事实或监管结论。
- `judgment` 只能为：`图文不一致`、`文本不一致`、`文本不完整`、`未提供图示证据`、`不确定`、`不适用`。
- `severity` 只能为：`重大`、`一般`、`优化`、`观察`。
- `category` 只能为：`一致性`、`可用性`、`部署`、`安全`、`数据流`、`可读性`。
- `evidence_ids` 是兼容字段，至少一个；平台展示和校验以 `evidence_refs` 为准。
- 每个 `evidence_refs[].evidence_id` 必须引用本次 `audit-evidence.json` 中的 `block:<索引>`、`table:<索引>` 或 `image:<图片ID>`。
- 文本和表格 `quote` 必须是相应证据的连续逐字摘录；图片 `quote` 描述可见文字或元素。`explanation` 必须说明证据与结论的关系。
- 表格 selector 与图片 region 的格式必须遵守平台校验器；相同根因合并为一个稳定的 `root_cause_key`。
- `impact` 只说明对文档评审、实施准备或理解的影响；`revision_suggestion` 可执行；`completion_criteria` 可验证。

各报告类型只能通过其规则包决定检查项、`rule_id`、审核维度及规则触发条件，不能改变本契约。
