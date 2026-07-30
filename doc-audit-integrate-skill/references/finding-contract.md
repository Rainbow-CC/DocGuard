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
- `selector` 仅用于表格证据（`table:<block_index>`），用于让页面精确高亮一行或若干单元格。其结构为：

  ```json
  {
    "row_match": {"列名": "精确单元格值"},
    "columns": ["需高亮的列名"]
  }
  ```

  - `row_match` 的键和值均为字符串，键必须是该表第一行中的表头，值必须与目标单元格完全一致。
  - `columns` 是表头名称数组，数组中的每一项都会在匹配行中高亮；可为空，表示只高亮匹配行。
  - `row_match` 非空时必须且只能匹配一条数据行；列名不存在、匹配零行或多行都会导致预检失败。
  - 不需要精确行/单元格高亮时，`selector` 必须为 `null`；段落和图片证据不得使用 `selector`。

- `region` 仅用于图片证据（`image:<image_id>`），用于在页面上绘制高亮框。其结构为：

  ```json
  {"x": 0.05, "y": 0.20, "width": 0.40, "height": 0.15}
  ```

  - 四个值均为 0 到 1 之间、相对于原图宽高的归一化比例；`x + width` 和 `y + height` 不得大于 1。
  - 仅在图中对象、文字或区域可以可靠确认时提供；无法可靠定位时使用 `null`，并在 `explanation` 中说明局限性。
  - 表格和段落证据不得使用 `region`。

- `root_cause_key` 只用于标识单条 Finding 的稳定、可解释根因；平台不得仅因该字段相同而合并、删除或隐藏 Finding。
- `impact` 只说明对文档评审、实施准备或理解的影响；`revision_suggestion` 可执行；`completion_criteria` 可验证。

各报告类型只能通过其规则包决定检查项、`rule_id`、审核维度及规则触发条件，不能改变本契约。
