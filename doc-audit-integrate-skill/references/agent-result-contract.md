# DocGuard Agent 结果契约

Agent 必须向 `DOCGUARD_RESULT_FILE` 原子交付一个 UTF-8 编码的 JSON 对象。顶层字段必须且只能为下表所列字段；不得添加私有字段，也不得省略任何字段。

| 字段 | JSON 类型 | 必填值、来源与约束 |
|---|---|---|
| `schema_version` | string | 固定为 `docguard-agent-result-v1`。 |
| `task_id` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.task_id`。 |
| `attempt_id` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.attempt_id`。 |
| `input_sha256` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.document.content_sha256`。 |
| `profile_id` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.profile.profile_id`。 |
| `profile_version` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.profile.version`。 |
| `prompt_versions` | object | 原样复制 `DOCGUARD_AUDIT_MANIFEST.profile.prompt_versions`，不得补充、删除或修改其中的键和值。 |
| `review_type_id` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.review_type.review_type_id`。 |
| `review_type_version` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.review_type.version`。 |
| `core_contract_version` | string | 原样复制 `DOCGUARD_AUDIT_MANIFEST.review_type.core_contract_version`。 |
| `dimension` | string | 使用当前已注册交付 Agent 的 `dimension`，即 manifest 中 `review_type.agents` 内与 `producer_agent_id` 对应项的 `dimension`。必须与调用提示中的 `DOCGUARD_DIMENSION` 一致。 |
| `scope` | string 或 `null` | 使用当前已注册交付 Agent 的 `scope`。没有细分范围时必须为 JSON `null`，不得使用空字符串、`"null"` 或自行推断的值；必须与 `DOCGUARD_SCOPE` 一致。 |
| `producer_agent_id` | string | 当前交付 Agent 的注册 `agent_id`；必须与 `DOCGUARD_AGENT_ID` 一致，并能在 manifest 的 `review_type.agents` 中找到。 |
| `producer_agent_version` | string | 当前交付 Agent 的注册 `version`；必须与 `DOCGUARD_AGENT_VERSION` 一致。 |
| `producer_model_ref` | string 或 `null` | 当前交付 Agent 的注册 `agent_model_ref`。当注册值为空时必须为 JSON `null`；不得自行填充模型名称。 |
| `findings` | array | 本次审核的 Finding 数组。无发现时必须为 `[]`；有发现时每一项必须严格遵循 [finding-contract.md](finding-contract.md)。 |

`task_id` 至 `core_contract_version` 必须与 manifest 逐值相等。`dimension` 至 `producer_model_ref` 必须与 manifest 中该注册 Agent 的配置逐值相等；不得猜测、替换或伪造 metadata。

## `findings` 与证据引用

每项 Finding 的字段、固定值、枚举和证据约束均以 [finding-contract.md](finding-contract.md) 为准。`evidence_refs` 是 Finding 内的非空数组；其中每个引用对象必须提供非空字符串 `evidence_id`、`quote` 和 `explanation`。

`evidence_refs` 的 `role`、`selector` 和 `region` 为可选字段：

- 省略 `role` 时，平台按 `primary` 处理；提供时只能为 `primary` 或 `supporting`。
- `selector` 仅用于表格证据；不需要精确表格高亮时可省略或设为 `null`。
- `region` 仅用于图片证据；无法可靠定位或不需要图片高亮时可省略或设为 `null`。
- 表格、段落证据不得提供非 `null` 的 `region`；图片证据不得提供非 `null` 的 `selector`。

结果写入临时文件后，必须运行平台提供的 `validate_findings.py`；预检成功后才能原子重命名为 `DOCGUARD_RESULT_FILE` 指定的最终文件。最终文件名以 `DOCGUARD_RESULT_FILE` 为准。
