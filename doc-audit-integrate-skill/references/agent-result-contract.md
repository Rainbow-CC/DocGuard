# DocGuard Agent 结果契约

Agent 必须向 `DOCGUARD_RESULT_FILE` 原子交付 JSON。顶层必填字段为：

```text
schema_version, task_id, attempt_id, input_sha256, profile_id, profile_version,
prompt_versions, review_type_id, review_type_version, core_contract_version, findings
```

可选字段 `routing`：审核 agent 自主决定的审核方向规则包决策（`routing-decision-v1`，含 `shared`、`packs[].pack_id`、`packs[].rule_pack`、可选 `packs[].reasoning` 用于说明分类依据、`fallback`），须逐字取自 `routing-decision.json` 的对象内容。**分类不再由脚本执行**：agent 按需阅读 `audit-context.md` 后自行判断哪些 pack 适用，并在 `reasoning` 中给出自然语言依据。

- `schema_version` 固定为 `docguard-agent-result-v1`。
- 所有任务、输入、Profile 与审核类型字段必须逐项匹配 `DOCGUARD_AUDIT_MANIFEST`。
- `core_contract_version` 必须匹配 manifest 中冻结的审核类型定义。
- `findings` 中的每一项必须遵循 `finding-contract.md`，无发现时使用空数组。

结果写入临时文件后，必须运行平台提供的 `validate_findings.py`；预检成功后才能原子重命名为 `findings.json`。
